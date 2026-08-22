"""Structured logging.

One pipeline for everything: ARUNA's own ``structlog`` calls and the stdlib
records emitted by asyncmy, redis, httpx, and python-telegram-bot all pass
through the same processors, so third-party output is timestamped, formatted,
and - critically - redacted the same way ARUNA's own output is.

``console`` format is for a developer at a terminal; ``json`` is for a VPS
where logs get shipped somewhere.  Both write the same fields.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from aruna.core.redaction import REDACTOR, configure_redactor

#: Third-party loggers that are noisy at INFO and useful only at DEBUG.
_NOISY_LOGGERS: dict[str, int] = {
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "telegram": logging.INFO,
    "telegram.ext": logging.INFO,
    "telegram.ext.Application": logging.WARNING,
    "apscheduler": logging.WARNING,
    "asyncio": logging.WARNING,
}

_configured = False


def _redaction_processor(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> EventDict:
    """Scrub secrets from every field.

    **Must run AFTER the traceback renderer, not before it.** It sat first in
    both handler chains for ten phases, where ``exc_info`` is still a raw
    ``(type, value, traceback)`` tuple and the traceback text does not exist
    yet. The renderer then expanded that tuple into text *after* redaction had
    finished, so anything secret inside an exception went to disk in the clear.

    That is not hypothetical here: python-telegram-bot raises
    ``InvalidToken(f"The token `{self._token}` was rejected by the server.")``,
    with the token interpolated into the message. A single mistyped
    ``ARUNA_TELEGRAM_BOT_TOKEN`` therefore wrote the real token to
    ``logs/aruna.log`` - a file that rotates, is backed up, and is the first
    thing anyone attaches to a bug report.
    """
    return REDACTOR.scrub_event(dict(event_dict))


def _drop_color_message(
    _logger: WrappedLogger, _name: str, event_dict: EventDict
) -> EventDict:
    """Remove uvicorn-style duplicate message keys if such a library appears."""
    event_dict.pop("color_message", None)
    return event_dict


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _drop_color_message,
    ]


def _renderer(fmt: str, *, colors: bool) -> Processor:
    if fmt == "json":
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=colors)


def configure_logging(
    *,
    level: str = "INFO",
    fmt: str = "console",
    log_dir: Path | None = None,
    file_enabled: bool = True,
    secrets: Iterable[str] = (),
    instance: str | None = None,
    env: str | None = None,
    phase: int | None = None,
    force: bool = False,
) -> structlog.stdlib.BoundLogger:
    """Install the logging pipeline.  Idempotent unless ``force`` is set.

    ``secrets`` is the set of values the redactor must never let through; pass
    :meth:`aruna.core.config.Settings.secrets`.

    Returns the root ARUNA logger.
    """
    global _configured

    configure_redactor(secrets)

    if _configured and not force:
        return get_logger("aruna")

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    shared = _shared_processors()

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(numeric_level)

    # Console handler.  Colors only when attached to a real terminal, so piping
    # to a file or to the Windows scheduler does not embed ANSI escapes.
    console_colors = fmt == "console" and sys.stderr.isatty()
    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                # After the traceback has been rendered to text, never before -
                # see _redaction_processor.
                _redaction_processor,
                _renderer(fmt, colors=console_colors),
            ],
        )
    )
    root.addHandler(console)

    if file_enabled and log_dir is not None:
        target = Path(log_dir)
        target.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target / "aruna.log",
            maxBytes=20 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        # Files are always JSON: they are meant to be searched, not read.
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=shared,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.dict_tracebacks,
                    # Same ordering as the console chain, and for the same
                    # reason. dict_tracebacks turns exc_info into nested dicts
                    # of frames and messages; scrubbing before it ran left every
                    # one of those strings untouched.
                    _redaction_processor,
                    structlog.processors.JSONRenderer(),
                ],
            )
        )
        root.addHandler(file_handler)

    for name, lvl in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(max(lvl, numeric_level))

    structlog.contextvars.clear_contextvars()
    binding: dict[str, Any] = {}
    if instance:
        binding["instance"] = instance
    if env:
        binding["env"] = env
    if phase is not None:
        binding["phase"] = phase
    if binding:
        structlog.contextvars.bind_contextvars(**binding)

    _configured = True
    return get_logger("aruna")


def get_logger(name: str = "aruna") -> structlog.stdlib.BoundLogger:
    """Named logger.  Safe to call before :func:`configure_logging`."""
    return structlog.get_logger(name)


def bind_context(**values: Any) -> None:
    """Attach values to every subsequent log line on this task/thread."""
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def is_configured() -> bool:
    return _configured


def reset_logging() -> None:
    """Tear the pipeline down.  Used by tests."""
    global _configured
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    _configured = False


__all__ = [
    "bind_context",
    "clear_context",
    "configure_logging",
    "get_logger",
    "is_configured",
    "reset_logging",
]
