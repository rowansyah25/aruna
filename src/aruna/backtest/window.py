"""Point-in-time evidence (SPEC 24, 36).

A backtest is only worth running if it cannot see the future. That is easy to
say and easy to get wrong: one query that forgets a `WHERE close_time <= t`
turns a losing strategy into a spectacular one, and the result looks entirely
plausible.

So the guarantee here is structural rather than disciplinary. :class:`Window`
owns every bar for one asset and hands out *views* clipped to an instant. No
caller receives the full series, and the clipping happens in one place that can
be tested directly. :func:`assert_no_leakage` is the belt to that braces - it
raises rather than warns, because a leaked backtest is worse than no backtest:
it produces a number people act on.

The other half is knowing what history does *not* contain. ARUNA never recorded
historical order books, so a replayed decision has no bid, no ask and no spread.
:meth:`Window.state_at` says so on the state it builds rather than substituting
the close price for a quote, and the report is required to carry that through to
the cost model.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aruna.agents.context import MarketState
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError

#: Bars needed before the analysis engine produces anything worth deciding on.
#: Matches the live path's floor in ``DeliberationService``.
MIN_BARS = 15


class LeakageError(ArunaError):
    """Evidence from after the simulated instant reached a decision."""


@dataclass(frozen=True, slots=True)
class Bar:
    """One settled bar, in the form the window needs."""

    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_price: Decimal


class Window:
    """Every stored bar for one asset and interval, sliceable by instant."""

    def __init__(
        self,
        bars: list[Bar],
        *,
        market: Market,
        symbol: str,
        interval: Horizon,
    ) -> None:
        self._bars = sorted(bars, key=lambda b: b.close_time)
        self._closes = [b.close_time for b in self._bars]
        self.market = market
        self.symbol = symbol
        self.interval = interval

    @classmethod
    def from_rows(
        cls, rows: list[dict], *, market: Market, symbol: str, interval: Horizon
    ) -> Window:
        bars = [
            Bar(
                open_time=row["open_time"],
                close_time=row["close_time"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                close_price=row["close"],
            )
            for row in rows
        ]
        return cls(bars, market=market, symbol=symbol, interval=interval)

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        if not self._bars:
            return None
        return self._bars[0].close_time, self._bars[-1].close_time

    def bars_through(self, moment: datetime) -> list[Bar]:
        """Bars that had closed at or before ``moment``. Nothing else exists.

        The whole point of the class: a caller cannot ask for anything wider,
        because it never gets the underlying list.
        """
        cut = bisect_right(self._closes, moment)
        return self._bars[:cut]

    def bars_between(self, start: datetime, end: datetime) -> list[Bar]:
        """Bars closing in ``(start, end]`` - the outcome window."""
        lo = bisect_right(self._closes, start)
        hi = bisect_right(self._closes, end)
        return self._bars[lo:hi]

    def series_at(self, moment: datetime, *, lookback: int = 300) -> CandleSeries:
        """The evidence a decision at ``moment`` is allowed to see."""
        visible = self.bars_through(moment)
        if len(visible) < MIN_BARS:
            raise InsufficientData(
                f"{self.symbol} {self.interval.value}: only {len(visible)} settled "
                f"bar(s) by {moment.isoformat()}, {MIN_BARS} needed"
            )
        window = visible[-lookback:]
        series = CandleSeries(
            market=self.market,
            symbol=self.symbol,
            interval=self.interval,
            opens=tuple(b.open for b in window),
            highs=tuple(b.high for b in window),
            lows=tuple(b.low for b in window),
            closes=tuple(b.close for b in window),
            volumes=tuple(b.volume for b in window),
            times=tuple(b.open_time for b in window),
            close_times=tuple(b.close_time for b in window),
        )
        assert_no_leakage(series.data_through, moment, self.symbol)
        return series

    def state_at(self, moment: datetime) -> MarketState:
        """Market state reconstructed from the bar that had just settled.

        The missing order book is expressed by leaving ``bid``, ``ask`` and
        ``spread_bps`` as ``None`` - which the cost model already reads as "no
        quote was observed, so charge no spread". That is the honest
        representation, and it is why this does *not* reach for
        ``data_quality``.

        ``data_quality`` in ARUNA means the SPEC 5 gate on a live feed: STALE,
        DUPLICATE, MISSING, ABNORMAL_SPREAD - a feed misbehaving right now.
        A settled historical bar is none of those; it is the settled truth, and
        it passed that gate when it was ingested. Flagging it anyway sets risk
        to EXTREME and trips the no-trade engine on every single step, so the
        backtest publishes nothing and reads as a cautious strategy rather than
        as a broken harness. That is what it did before this comment existed.

        ``is_realtime`` is True for the same reason: relative to the instant
        being simulated, a just-settled bar *is* current, and the live system
        also decides only from settled bars. Marking it delayed would discount
        every backtested decision relative to its live twin and make the two
        sets of numbers incomparable - which would defeat the point of sharing
        the decision path at all.
        """
        visible = self.bars_through(moment)
        if not visible:
            raise InsufficientData(f"{self.symbol}: no bars by {moment.isoformat()}")
        last = visible[-1]
        return MarketState(
            last_price=last.close_price,
            is_realtime=True,
            declared_delay_sec=0,
            data_quality="OK",
            quality_detail=(
                "backtest: price is a settled bar close; no order book was "
                "recorded, so bid, ask and spread are unavailable and no "
                "spread cost is charged"
            ),
            source="backtest",
        )

    def prices_after(
        self, start: datetime, end: datetime
    ) -> list[tuple[datetime, Decimal]]:
        """Closes inside an outcome window, for scoring a simulated prediction."""
        return [
            (bar.close_time, bar.close_price)
            for bar in self.bars_between(start, end)
        ]

    def steps(
        self, *, start: datetime, end: datetime, every: int = 1
    ) -> list[datetime]:
        """Instants to decide at: bar closes, optionally thinned.

        Deciding on bar closes rather than on a wall clock is what keeps a
        backtest aligned with the live system, which also only ever acts on
        settled bars.
        """
        moments = [
            bar.close_time
            for bar in self._bars
            if start <= bar.close_time <= end
        ]
        return moments[:: max(1, every)]


def assert_no_leakage(as_of: datetime, moment: datetime, symbol: str) -> None:
    """Refuse evidence dated after the instant being simulated (SPEC 24).

    Raises rather than logging. A backtest that quietly saw the future produces
    a number somebody will act on, and by then the mistake is expensive.
    """
    if as_of > moment:
        raise LeakageError(
            f"{symbol}: evidence dated {as_of.isoformat()} reached a decision "
            f"simulated at {moment.isoformat()}. The backtest saw the future; "
            "its results are void."
        )


__all__ = ["MIN_BARS", "Bar", "LeakageError", "Window", "assert_no_leakage"]
