"""Time handling and exchange sessions."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from aruna.core.clock import (
    JAKARTA,
    IdxCalendar,
    age_seconds,
    crypto_session,
    isoformat,
    require_aware,
    to_utc,
)
from aruna.core.enums import CryptoSession, IdxSession


def wib(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JAKARTA)


class TestAwareness:
    def test_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="naive datetime rejected"):
            require_aware(datetime(2026, 8, 15, 10, 0))

    def test_aware_datetime_passes(self) -> None:
        value = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
        assert require_aware(value) is value

    def test_to_utc_converts(self) -> None:
        assert to_utc(wib(2026, 8, 17, 9, 0)).hour == 2

    def test_isoformat_is_utc_with_z(self) -> None:
        assert isoformat(wib(2026, 8, 17, 9, 0)) == "2026-08-17T02:00:00.000Z"

    def test_age_is_positive_for_the_past(self) -> None:
        reference = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
        past = datetime(2026, 8, 15, 11, 30, tzinfo=UTC)
        assert age_seconds(past, reference=reference) == 1800


class TestIdxCalendar:
    @pytest.fixture
    def calendar(self) -> IdxCalendar:
        return IdxCalendar()

    # 2026-08-17 is a Monday, 2026-08-21 a Friday, 2026-08-15 a Saturday.

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            (wib(2026, 8, 17, 7, 0), IdxSession.PRE_MARKET),
            (wib(2026, 8, 17, 8, 50), IdxSession.OPENING),
            (wib(2026, 8, 17, 10, 0), IdxSession.MID_SESSION),
            (wib(2026, 8, 17, 14, 0), IdxSession.MID_SESSION),
            (wib(2026, 8, 17, 15, 55), IdxSession.CLOSING),
            (wib(2026, 8, 17, 18, 0), IdxSession.AFTER_MARKET),
        ],
    )
    def test_monday_sessions(
        self, calendar: IdxCalendar, moment: datetime, expected: IdxSession
    ) -> None:
        assert calendar.session(moment) is expected

    def test_weekend_is_after_market(self, calendar: IdxCalendar) -> None:
        assert calendar.session(wib(2026, 8, 15, 10, 0)) is IdxSession.AFTER_MARKET
        assert calendar.is_open(wib(2026, 8, 15, 10, 0)) is False

    def test_open_during_session_one(self, calendar: IdxCalendar) -> None:
        assert calendar.is_open(wib(2026, 8, 17, 10, 0)) is True

    def test_closed_during_lunch_break(self, calendar: IdxCalendar) -> None:
        assert calendar.is_open(wib(2026, 8, 17, 12, 30)) is False

    def test_open_during_session_two(self, calendar: IdxCalendar) -> None:
        assert calendar.is_open(wib(2026, 8, 17, 14, 30)) is True

    def test_friday_session_one_ends_earlier(self, calendar: IdxCalendar) -> None:
        assert calendar.is_open(wib(2026, 8, 21, 11, 45)) is False
        assert calendar.is_open(wib(2026, 8, 17, 11, 45)) is True

    def test_friday_session_two_starts_later(self, calendar: IdxCalendar) -> None:
        assert calendar.is_open(wib(2026, 8, 21, 13, 45)) is False
        assert calendar.is_open(wib(2026, 8, 17, 13, 45)) is True

    def test_closed_after_the_bell(self, calendar: IdxCalendar) -> None:
        assert calendar.is_open(wib(2026, 8, 17, 16, 30)) is False

    def test_holiday_calendar_is_absent_until_loaded(self, calendar: IdxCalendar) -> None:
        """PHASE 1 has no IDX holiday source; is_open is a schedule hint only."""
        assert calendar.has_holiday_calendar is False

    def test_loaded_holiday_closes_the_market(self, calendar: IdxCalendar) -> None:
        calendar.load_holidays({date(2026, 8, 17)})
        assert calendar.has_holiday_calendar is True
        assert calendar.is_open(wib(2026, 8, 17, 10, 0)) is False
        assert calendar.session(wib(2026, 8, 17, 10, 0)) is IdxSession.AFTER_MARKET

    def test_accepts_utc_input(self, calendar: IdxCalendar) -> None:
        """03:00 UTC on Monday is 10:00 WIB - inside session one."""
        assert calendar.is_open(datetime(2026, 8, 17, 3, 0, tzinfo=UTC)) is True


class TestCryptoSession:
    @pytest.mark.parametrize(
        ("hour", "expected"),
        [
            (0, CryptoSession.ASIA),
            (7, CryptoSession.ASIA),
            (8, CryptoSession.EUROPE),
            (12, CryptoSession.EUROPE),
            (13, CryptoSession.US),
            (20, CryptoSession.US),
            (21, CryptoSession.H24),
            (23, CryptoSession.H24),
        ],
    )
    def test_bands_cover_the_whole_day(self, hour: int, expected: CryptoSession) -> None:
        moment = datetime(2026, 8, 15, hour, 0, tzinfo=UTC)
        assert crypto_session(moment) is expected

    def test_every_hour_resolves(self) -> None:
        for hour in range(24):
            assert crypto_session(datetime(2026, 8, 15, hour, tzinfo=UTC)) in CryptoSession
