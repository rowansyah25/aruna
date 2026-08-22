"""Secret scrubbing (SPEC 43)."""

from __future__ import annotations

import pytest

from aruna.core.redaction import MASK, Redactor


@pytest.fixture
def redactor() -> Redactor:
    return Redactor({"hunter2secret", "another-long-secret-value"})


class TestValueScrubbing:
    def test_known_secret_is_replaced(self, redactor: Redactor) -> None:
        assert redactor.scrub_text("connecting with hunter2secret now") == (
            f"connecting with {MASK} now"
        )

    def test_short_values_are_ignored(self) -> None:
        """A 3-character 'secret' would scrub half the log file."""
        assert Redactor({"abc"}).scrub_text("abcdef") == "abcdef"

    def test_telegram_token_shape_is_scrubbed_even_if_unknown(
        self, redactor: Redactor
    ) -> None:
        token = "8123456789:AAHf9xLqPzR3kTvB2nM7wQe5rY1uI0oP4sD"
        assert token not in redactor.scrub_text(f"token={token}")

    @pytest.mark.parametrize(
        "dsn",
        [
            "mysql://root:topsecretpw@localhost:3306/aruna",
            "postgresql://postgres:topsecretpw@localhost:5432/aruna",
            "redis://:topsecretpw@localhost:6379/0",
        ],
    )
    def test_dsn_credentials_are_scrubbed(self, redactor: Redactor, dsn: str) -> None:
        """The pattern keys on the URL shape, not the scheme, so a credential
        leaking through any driver's error message is caught."""
        scrubbed = redactor.scrub_text(dsn)
        assert "topsecretpw" not in scrubbed
        assert "localhost" in scrubbed

    def test_bearer_header_is_scrubbed(self, redactor: Redactor) -> None:
        scrubbed = redactor.scrub_text("Authorization: Bearer abcdef1234567890xyz")
        assert "abcdef1234567890xyz" not in scrubbed

    def test_key_value_pairs_in_free_text(self, redactor: Redactor) -> None:
        scrubbed = redactor.scrub_text("failed with api_key=abcdef123456 in request")
        assert "abcdef123456" not in scrubbed
        assert "api_key" in scrubbed

    def test_longest_secret_wins(self) -> None:
        """Scrubbing the short one first would leave a readable tail."""
        redactor = Redactor({"secretvalue", "secretvalue-extended-suffix"})
        assert "extended" not in redactor.scrub_text("secretvalue-extended-suffix")


class TestKeyScrubbing:
    def test_sensitive_key_is_masked(self, redactor: Redactor) -> None:
        event = redactor.scrub_event({"db_password": "anything at all"})
        assert event["db_password"] == MASK

    @pytest.mark.parametrize(
        "key", ["password", "api_key", "apikey", "X-Api-Key", "authorization", "bot_token"]
    )
    def test_sensitive_key_variants(self, redactor: Redactor, key: str) -> None:
        assert redactor.scrub_event({key: "value-here"})[key] == MASK

    def test_safe_dsn_key_is_kept(self, redactor: Redactor) -> None:
        """safe_dsn is already masked; scrubbing it would lose the diagnostic."""
        event = redactor.scrub_event({"safe_dsn": "postgresql://postgres:***@host/aruna"})
        assert event["safe_dsn"] != MASK

    def test_nested_structures_are_scrubbed(self, redactor: Redactor) -> None:
        event = redactor.scrub_event(
            {"config": {"password": "x", "host": "127.0.0.1"}, "hosts": ["hunter2secret"]}
        )
        assert event["config"]["password"] == MASK
        assert event["config"]["host"] == "127.0.0.1"
        assert event["hosts"] == [MASK]

    def test_exception_values_are_scrubbed(self, redactor: Redactor) -> None:
        event = redactor.scrub_event({"error": ValueError("failed for hunter2secret")})
        assert "hunter2secret" not in str(event["error"])

    def test_non_string_values_pass_through(self, redactor: Redactor) -> None:
        event = redactor.scrub_event({"count": 42, "ok": True, "ratio": 1.5})
        assert event == {"count": 42, "ok": True, "ratio": 1.5}


class TestExcInfoSurvives:
    """Scrubbing exc_info structurally destroyed the traceback.

    ``scrub_value`` rewrote the exception instance into a string, handing
    structlog ``(type, "ValueError: ...", traceback)`` - which it cannot render,
    so it dropped the traceback silently. A log that loses tracebacks loses them
    exactly when they matter.
    """

    def test_an_exc_info_triple_is_returned_untouched(
        self, redactor: Redactor
    ) -> None:
        try:
            raise ValueError("hunter2secret leaked here")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        scrubbed = redactor.scrub_value(exc_info)
        assert scrubbed is exc_info
        assert isinstance(scrubbed[1], BaseException)
        assert scrubbed[2] is exc_info[2]

    def test_an_ordinary_three_tuple_is_still_scrubbed(
        self, redactor: Redactor
    ) -> None:
        """The guard must not swallow every three-element tuple."""
        scrubbed = redactor.scrub_value(("a", "hunter2secret", "c"))
        assert scrubbed == ("a", MASK, "c")


class TestTheLogFileItself:
    """The leak this file previously could not see.

    Every other test here asserts what the redactor RETURNS. The token reached
    disk anyway, because redaction ran before the traceback was rendered - so
    the secret was still inside an exception object, not yet inside any string
    the redactor was looking at. The only assertion that catches that is one
    against the bytes actually written.
    """

    def test_a_secret_inside_an_exception_does_not_reach_the_log_file(
        self, tmp_path
    ) -> None:
        import json
        import logging

        from aruna.core.logging import configure_logging, get_logger
        from aruna.core.redaction import configure_redactor

        token = "8123456789:AAF-verysecret-token-value-here"
        configure_redactor({token})
        configure_logging(
            level="INFO",
            fmt="json",
            log_dir=tmp_path,
            file_enabled=True,
            secrets={token},
            force=True,
        )
        log = get_logger("test.leak")

        try:
            # The shape python-telegram-bot actually raises: the token is
            # interpolated into the exception's own message.
            raise ValueError(f"The token `{token}` was rejected by the server.")
        except ValueError:
            log.exception("telegram.start_failed")

        for handler in logging.getLogger().handlers:
            handler.flush()

        written = (tmp_path / "aruna.log").read_text(encoding="utf-8")
        assert token not in written, "the bot token reached the log file"
        assert MASK in written

        # And the traceback survived the scrubbing that protected it.
        record = json.loads(written.strip().splitlines()[-1])
        assert "exception" in record, "redaction destroyed the traceback"


class TestRotation:
    def test_secrets_can_be_replaced_at_runtime(self) -> None:
        redactor = Redactor({"oldsecretvalue"})
        redactor.set_secrets({"newsecretvalue"})
        assert redactor.scrub_text("oldsecretvalue") == "oldsecretvalue"
        assert redactor.scrub_text("newsecretvalue") == MASK
