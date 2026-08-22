"""Logging pipeline, end to end."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aruna.core.logging import bind_context, configure_logging, get_logger, reset_logging


def _read_log_lines(log_dir: Path) -> list[dict]:
    logging.shutdown()
    path = log_dir / "aruna.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class TestConfiguration:
    def test_writes_json_lines_to_file(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", fmt="json", log_dir=tmp_path, force=True)
        get_logger("aruna.test").info("something.happened", asset="BTC/USDT")

        entries = _read_log_lines(tmp_path)
        assert any(e["event"] == "something.happened" and e["asset"] == "BTC/USDT" for e in entries)

    def test_context_binding_appears_on_every_line(self, tmp_path: Path) -> None:
        configure_logging(
            level="INFO",
            fmt="json",
            log_dir=tmp_path,
            instance="aruna-test",
            env="development",
            phase=1,
            force=True,
        )
        get_logger("aruna.test").info("first")
        get_logger("aruna.test").info("second")

        entries = [e for e in _read_log_lines(tmp_path) if e["event"] in ("first", "second")]
        assert len(entries) == 2
        assert all(e["instance"] == "aruna-test" and e["phase"] == 1 for e in entries)

    def test_extra_context_can_be_bound_later(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", fmt="json", log_dir=tmp_path, force=True)
        bind_context(signal_id="abc-123")
        get_logger("aruna.test").info("locked")

        entries = [e for e in _read_log_lines(tmp_path) if e["event"] == "locked"]
        assert entries and entries[0]["signal_id"] == "abc-123"

    def test_level_filtering(self, tmp_path: Path) -> None:
        configure_logging(level="WARNING", fmt="json", log_dir=tmp_path, force=True)
        get_logger("aruna.test").info("suppressed")
        get_logger("aruna.test").warning("kept")

        events = {e["event"] for e in _read_log_lines(tmp_path)}
        assert "suppressed" not in events
        assert "kept" in events

    def test_reset_removes_handlers(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", fmt="json", log_dir=tmp_path, force=True)
        reset_logging()
        assert logging.getLogger().handlers == []


class TestRedactionInPipeline:
    def test_configured_secret_never_reaches_the_file(self, tmp_path: Path) -> None:
        configure_logging(
            level="INFO",
            fmt="json",
            log_dir=tmp_path,
            secrets={"hunter2secret"},
            force=True,
        )
        get_logger("aruna.test").info("connecting", dsn="mysql://root:hunter2secret@h/db")

        raw = (tmp_path / "aruna.log").read_text(encoding="utf-8")
        assert "hunter2secret" not in raw

    def test_sensitive_field_names_are_masked(self, tmp_path: Path) -> None:
        configure_logging(level="INFO", fmt="json", log_dir=tmp_path, force=True)
        get_logger("aruna.test").info("auth", api_key="abcdef1234567890")

        raw = (tmp_path / "aruna.log").read_text(encoding="utf-8")
        assert "abcdef1234567890" not in raw

    def test_third_party_stdlib_logs_are_redacted_too(self, tmp_path: Path) -> None:
        """asyncmy and telegram log through stdlib; they must be scrubbed as well."""
        configure_logging(
            level="INFO", fmt="json", log_dir=tmp_path, secrets={"hunter2secret"}, force=True
        )
        logging.getLogger("some.third.party").warning(
            "connection failed for mysql://root:hunter2secret@h/db"
        )

        raw = (tmp_path / "aruna.log").read_text(encoding="utf-8")
        assert "hunter2secret" not in raw
