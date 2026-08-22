"""Secret scrubbing for anything ARUNA writes out.

SPEC 43 requires that no secret reaches a log file.  Two mechanisms, because
neither alone is sufficient:

* **by key** - an event field literally named ``password``/``token``/... is
  replaced regardless of its content;
* **by value** - the configured secrets, plus a few shapes that are secrets no
  matter what field they arrive in (a Telegram bot token, a DSN with inline
  credentials, a ``Bearer`` header), are substituted out of rendered strings.

Value-based scrubbing catches the case that matters most in practice: a secret
that arrives embedded in an exception message from a third-party library, where
ARUNA never chose the field name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from types import TracebackType
from typing import Any

MASK = "***REDACTED***"

#: Field names whose value is always replaced, matched case-insensitively as a
#: substring so ``db_password`` and ``X-Api-Key`` are both covered.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "api-key",
    "authorization",
    "auth_header",
    "credential",
    "private_key",
    "access_key",
    "dsn",
)

#: Field names that contain a sensitive word but are safe, and would otherwise
#: lose useful diagnostics.
SAFE_KEY_EXACT: frozenset[str] = frozenset({"safe_dsn", "has_token", "token_present"})

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Telegram bot token: <numeric bot id>:<35-char secret>
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    # Credentials inline in a connection URL, e.g. mysql://user:pw@host.
    # The username part is optional: ARUNA's own Redis URL is
    # redis://:password@host, which a username-required pattern would miss.
    re.compile(r"(?<=://)[^\s:/@]*:[^\s:/@]+(?=@)"),
    # Authorization headers
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    # key=value / key: value forms in free text
    re.compile(
        r"(?i)\b(api[_-]?key|token|secret|password)\b\s*[=:]\s*[\"']?([^\s\"',;}]{6,})"
    ),
)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SAFE_KEY_EXACT:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


class Redactor:
    """Applies key-based and value-based scrubbing.

    ``known_secrets`` comes from :meth:`aruna.core.config.Settings.secrets` and
    can be refreshed at runtime, so a credential rotated mid-process is still
    scrubbed.
    """

    def __init__(self, known_secrets: Iterable[str] = ()) -> None:
        self._secrets: tuple[str, ...] = ()
        self.set_secrets(known_secrets)

    def set_secrets(self, known_secrets: Iterable[str]) -> None:
        # Longest first: scrubbing a token before the substring that happens to
        # sit inside it avoids leaving a recognisable tail behind.
        self._secrets = tuple(
            sorted({s for s in known_secrets if s and len(s) >= 6}, key=len, reverse=True)
        )

    @property
    def secret_count(self) -> int:
        return len(self._secrets)

    def scrub_text(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, MASK)
        for pattern in _PATTERNS:
            text = pattern.sub(_mask_match, text)
        return text

    def scrub_value(self, value: Any, *, key: str | None = None) -> Any:
        if key is not None and _is_sensitive_key(key):
            return MASK if value is not None else None
        if isinstance(value, str):
            return self.scrub_text(value)
        if isinstance(value, dict):
            return {k: self.scrub_value(v, key=str(k)) for k, v in value.items()}
        if isinstance(value, tuple) and _is_exc_info(value):
            # Hand exc_info back untouched. Scrubbing it structurally rewrote
            # the exception instance into a string, producing
            # ``(type, "ValueError: ...", traceback)`` - which structlog cannot
            # render, so it silently dropped the traceback altogether. The
            # secrets inside are handled properly by scrubbing the *rendered*
            # text instead, which is why this processor now runs after the
            # traceback renderer rather than before it.
            return value
        if isinstance(value, (list, tuple, set)):
            scrubbed = [self.scrub_value(v) for v in value]
            return type(value)(scrubbed) if not isinstance(value, set) else set(scrubbed)
        if isinstance(value, BaseException):
            return self.scrub_text(f"{type(value).__name__}: {value}")
        return value

    def scrub_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return {key: self.scrub_value(value, key=str(key)) for key, value in event.items()}


def _is_exc_info(value: tuple[Any, ...]) -> bool:
    """Is this the ``(type, value, traceback)`` triple structlog passes around?"""
    return (
        len(value) == 3
        and isinstance(value[1], BaseException)
        and (value[2] is None or isinstance(value[2], TracebackType))
    )


def _mask_match(match: re.Match[str]) -> str:
    """Keep the label of a ``key=value`` match, mask only the value."""
    if match.lastindex == 2:
        return f"{match.group(1)}={MASK}"
    return MASK


#: Process-wide redactor.  Installed into the logging pipeline at startup and
#: refreshed with the real secrets once configuration has loaded.
REDACTOR = Redactor()


def configure_redactor(secrets: Iterable[str]) -> Redactor:
    REDACTOR.set_secrets(secrets)
    return REDACTOR


__all__ = [
    "MASK",
    "REDACTOR",
    "SAFE_KEY_EXACT",
    "SENSITIVE_KEY_PARTS",
    "Redactor",
    "configure_redactor",
]
