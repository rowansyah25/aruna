"""The Yahoo chart range must follow ``limit``, not ignore it.

Yahoo's chart endpoint has no bar count - ``range`` alone decides how much
comes back.  The adapter used to ask for ``_MAX_RANGE`` on every call, so the
upkeep loop's premise ("refresh a few bars, not five hundred") had no effect on
IDX whatsoever: a four-bar refresh downloaded 60 days of 15m bars or two years
of hourly bars, built a ``Candle`` for each row, and returned four.

These tests fake ``_chart`` and model the one thing that matters - the payload
is a function of the range asked for - so they measure the request, not the
network.  If the range choice is reverted to ``_MAX_RANGE``, the first three
classes here go red; the backfill and coverage classes stay green, because
their job is to catch the opposite mistake of narrowing the window so far that
a first backfill or a catch-up pass silently gets fewer bars than it asked for.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import Horizon
from aruna.data.idx.yahoo import _INTERVALS, _MAX_RANGE, YahooIdxProvider, _chart_range

#: Friday 2026-08-14 16:00 WIB.  Fixed so row counts are arithmetic, not
#: whatever weekday the suite happens to run on.
ANCHOR = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)

#: IDX regular market, 09:00-16:00 WIB = 02:00-09:00 UTC.
SESSION_OPEN_UTC = 2
SESSION_CLOSE_UTC = 9

_STEP_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def range_days(range_: str) -> int:
    """``'5d'`` -> 5, ``'3mo'`` -> 91, ``'2y'`` -> 730."""
    for suffix, factor in (("mo", 30), ("y", 365), ("d", 1)):
        if range_.endswith(suffix):
            return int(range_[: -len(suffix)]) * factor
    raise AssertionError(f"unhandled yahoo range {range_!r}")


def stamps_for(interval: str, range_: str) -> list[int]:
    """Epochs Yahoo would return for ``range_``: weekday sessions only.

    Holidays are not modelled - that is what
    :class:`TestEmptyWindowWidens` covers explicitly.
    """
    out: list[datetime] = []
    for back in range(range_days(range_), 0, -1):
        day = (ANCHOR - timedelta(days=back)).date()
        if day.weekday() >= 5:
            continue
        open_utc = datetime(day.year, day.month, day.day, SESSION_OPEN_UTC, tzinfo=UTC)
        if interval == "1d":
            out.append(open_utc)
        elif interval == "1wk":
            if day.weekday() == 0:
                out.append(open_utc)
        else:
            close_utc = datetime(
                day.year, day.month, day.day, SESSION_CLOSE_UTC, tzinfo=UTC
            )
            step = timedelta(minutes=_STEP_MINUTES[interval])
            moment = open_utc
            while moment < close_utc:
                out.append(moment)
                moment += step
    return [int(moment.timestamp()) for moment in out]


class FakeChart:
    """Yahoo's chart endpoint, reduced to its one relevant behaviour.

    It returns everything inside the range it was handed, which is exactly why
    asking for the widest range on every refresh was expensive.  ``blank``
    names ranges that come back with no bars at all - a real outcome when a
    narrow window lands entirely inside an exchange holiday.
    """

    def __init__(self, *, blank: frozenset[str] = frozenset()) -> None:
        self.ranges: list[str] = []
        self.rows: list[int] = []
        self._blank = blank

    async def __call__(
        self, symbol: str, *, interval: str, range_: str
    ) -> tuple[dict[str, Any], float]:
        stamps = [] if range_ in self._blank else stamps_for(interval, range_)
        self.ranges.append(range_)
        self.rows.append(len(stamps))
        count = len(stamps)
        payload = {
            "meta": {"currency": "IDR"},
            "timestamp": stamps,
            "indicators": {
                "quote": [
                    {
                        "open": [100.0] * count,
                        "high": [101.0] * count,
                        "low": [99.0] * count,
                        "close": [100.5] * count,
                        "volume": [1_000] * count,
                    }
                ]
            },
        }
        return payload, 12.0


@pytest.fixture
def provider() -> YahooIdxProvider:
    return YahooIdxProvider(DataSettings(_env_file=None))


def attach(provider: YahooIdxProvider, chart: FakeChart) -> FakeChart:
    provider._chart = chart  # type: ignore[method-assign]
    return chart


class TestRoutineRefreshAsksForALittle:
    """A four-bar refresh must not request the maximum window."""

    @pytest.mark.parametrize(
        ("interval", "expected"),
        [(Horizon.M15, "5d"), (Horizon.H1, "5d"), (Horizon.D1, "1mo")],
    )
    async def test_range_follows_the_limit(
        self, provider: YahooIdxProvider, interval: Horizon, expected: str
    ) -> None:
        chart = attach(provider, FakeChart())

        candles = await provider.fetch_candles("BBCA", interval, limit=4)

        assert chart.ranges == [expected]
        assert chart.ranges != [_MAX_RANGE[interval]]
        assert len(candles) == 4

    @pytest.mark.parametrize(
        ("interval", "factor"),
        [(Horizon.M15, 8), (Horizon.H1, 50), (Horizon.D1, 50)],
    )
    async def test_rows_pulled_drop_by_orders_of_magnitude(
        self, provider: YahooIdxProvider, interval: Horizon, factor: int
    ) -> None:
        """The saving is in rows moved and parsed, not in request count.

        Against the session model below the reduction comes out at 10.8x for
        15m, 130x for 1h and 124x for 1d; ``factor`` is a floor under each, set
        loose enough that it locks the behaviour rather than the arithmetic.
        """
        chart = attach(provider, FakeChart())
        widest = len(stamps_for(_INTERVALS[interval], _MAX_RANGE[interval]))

        await provider.fetch_candles("BBCA", interval, limit=4)

        assert chart.rows[0] * factor <= widest

    async def test_only_the_tail_is_returned(self, provider: YahooIdxProvider) -> None:
        """Truncation keeps the newest bars - a refresh wants the latest ones."""
        chart = attach(provider, FakeChart())

        candles = await provider.fetch_candles("BBCA", Horizon.M15, limit=4)

        newest = stamps_for("15m", chart.ranges[0])[-1]
        assert int(candles[-1].open_time.timestamp()) == newest
        assert [c.open_time for c in candles] == sorted(c.open_time for c in candles)


class TestBackfillKeepsItsHistory:
    """Narrowing the routine refresh must not shrink the first backfill."""

    @pytest.mark.parametrize("interval", [Horizon.M15, Horizon.H1, Horizon.D1])
    async def test_five_hundred_bars_still_arrive(
        self, provider: YahooIdxProvider, interval: Horizon
    ) -> None:
        chart = attach(provider, FakeChart())

        candles = await provider.fetch_candles("BBCA", interval, limit=500)

        assert len(candles) == 500
        assert chart.rows[0] >= 500

    @pytest.mark.parametrize(
        "interval", [Horizon.M1, Horizon.M15, Horizon.H1, Horizon.D1]
    )
    @pytest.mark.parametrize("limit", [4, 50, 200, 500])
    async def test_the_window_covers_the_bars_asked_for(
        self, provider: YahooIdxProvider, interval: Horizon, limit: int
    ) -> None:
        """The invariant behind the whole change.

        Whatever range is chosen must hold at least ``limit`` bars, unless it
        is already Yahoo's cap - beyond the cap the venue has nothing more to
        give, and that shortfall is reported by absence, never padded.
        """
        chart = attach(provider, FakeChart())

        await provider.fetch_candles("BBCA", interval, limit=limit)

        assert chart.rows[0] >= limit or chart.ranges[0] == _MAX_RANGE[interval]


class TestEmptyWindowWidens:
    """A narrow window that lands on nothing is our problem, not the venue's."""

    async def test_a_blank_narrow_window_is_retried_at_the_cap(
        self, provider: YahooIdxProvider
    ) -> None:
        """Five days can be entirely holiday; the ingestor would have logged
        'provider returned none' and started its 60-second retry cadence."""
        chart = attach(provider, FakeChart(blank=frozenset({"5d"})))

        candles = await provider.fetch_candles("BBCA", Horizon.M15, limit=4)

        assert chart.ranges == ["5d", _MAX_RANGE[Horizon.M15]]
        assert len(candles) == 4

    async def test_a_window_with_data_is_not_asked_twice(
        self, provider: YahooIdxProvider
    ) -> None:
        chart = attach(provider, FakeChart())

        await provider.fetch_candles("BBCA", Horizon.M15, limit=4)

        assert len(chart.ranges) == 1

    async def test_empty_at_the_cap_stays_empty(self, provider: YahooIdxProvider) -> None:
        """No third try, and no invented bars: a genuinely dead symbol is
        reported as no data (SPEC 4)."""
        chart = attach(provider, FakeChart(blank=frozenset({"5d", "60d"})))

        candles = await provider.fetch_candles("BBCA", Horizon.M15, limit=4)

        assert candles == []
        assert chart.ranges == ["5d", "60d"]

    async def test_no_retry_when_the_first_try_was_already_the_cap(
        self, provider: YahooIdxProvider
    ) -> None:
        chart = attach(provider, FakeChart(blank=frozenset({"60d"})))

        candles = await provider.fetch_candles("BBCA", Horizon.M15, limit=500)

        assert candles == []
        assert chart.ranges == ["60d"]


class TestRangeChoice:
    """``_chart_range`` on its own, including intervals the refresher never asks
    for - the cap still has to hold for them."""

    NOW = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)

    @pytest.mark.parametrize("interval", list(_INTERVALS))
    def test_never_exceeds_yahoos_cap(self, interval: Horizon) -> None:
        chosen = _chart_range(interval, 100_000, None, None, self.NOW)

        assert chosen == _MAX_RANGE[interval]
        assert range_days(chosen) <= range_days(_MAX_RANGE[interval])

    @pytest.mark.parametrize("interval", list(_INTERVALS))
    def test_a_tiny_limit_never_picks_the_cap_for_stored_intervals(
        self, interval: Horizon
    ) -> None:
        chosen = _chart_range(interval, 1, None, None, self.NOW)

        assert range_days(chosen) <= range_days(_MAX_RANGE[interval])
        if interval in (Horizon.M15, Horizon.H1, Horizon.D1):
            assert chosen != _MAX_RANGE[interval]

    def test_an_explicit_start_widens_the_window(self) -> None:
        """``range`` is anchored at now, so a caller reaching 200 days back
        needs a window that reaches 200 days back - the limit alone would have
        asked for five days."""
        start = self.NOW - timedelta(days=200)

        assert _chart_range(Horizon.H1, 4, start, None, self.NOW) == "1y"
        assert _chart_range(Horizon.H1, 4, None, None, self.NOW) == "5d"

    def test_an_end_in_the_past_widens_the_window(self) -> None:
        """Asking for four bars around last March still has to span from March
        to now, because Yahoo counts backwards from today."""
        end = self.NOW - timedelta(days=150)

        assert _chart_range(Horizon.D1, 4, None, end, self.NOW) == "6mo"

    def test_an_end_in_the_future_does_not_shrink_the_window(self) -> None:
        """Yahoo has no future bars, so an end past now must not eat into the
        span that reaches backwards."""
        end = self.NOW + timedelta(days=30)

        assert _chart_range(Horizon.H1, 4, None, end, self.NOW) == _chart_range(
            Horizon.H1, 4, None, None, self.NOW
        )

    def test_a_zero_limit_still_asks_for_a_real_range(self) -> None:
        """Yahoo has no zero-length range, and a division by the bars-per-day
        figure must not turn a degenerate limit into an empty request."""
        assert _chart_range(Horizon.D1, 0, None, None, self.NOW) == "5d"
