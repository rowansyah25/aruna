"""The candle series indicators read from.

This type is where SPEC 24 is enforced. It **refuses unclosed bars**: an
indicator that reaches into a still-forming candle is reading a price that has
not settled, which is look-ahead leakage wearing ordinary clothes. Because the
refusal lives here rather than in each indicator, no future indicator can
forget it.

Values are exposed as ``float``. Prices stay :class:`~decimal.Decimal` in
storage and in the paper ledger, where exactness is the point; an EMA is a
statistical estimate and does not benefit from 28-digit arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle


class InsufficientData(ValueError):
    """Not enough bars for a requested computation."""


@dataclass(frozen=True, slots=True)
class CandleSeries:
    """An ordered, closed-only series for one asset and interval."""

    market: Market
    symbol: str
    interval: Horizon
    opens: tuple[float, ...]
    highs: tuple[float, ...]
    lows: tuple[float, ...]
    closes: tuple[float, ...]
    volumes: tuple[float, ...]
    #: Bar open times. Used to label swings and returns, because a bar is
    #: conventionally named by when it started.
    times: tuple[datetime, ...]
    #: Bar close times. Separate from ``times`` because the two answer different
    #: questions: a bar is *named* by its open, but its content is only known at
    #: its close. Freshness and SPEC 24 cutoffs must use this one - reporting a
    #: daily bar as "as of" its open understates the evidence by a full day.
    close_times: tuple[datetime, ...] = ()
    #: Bars that were dropped for still being open. Reported so a caller can
    #: tell "no data" from "data exists but has not settled".
    excluded_open_bars: int = 0

    @classmethod
    def from_candles(cls, candles: list[Candle]) -> CandleSeries:
        if not candles:
            raise InsufficientData("cannot build a series from zero candles")

        first = candles[0]
        if any(c.symbol != first.symbol for c in candles):
            raise ValueError("candle series must cover exactly one symbol")
        if any(c.interval is not first.interval for c in candles):
            raise ValueError("candle series must cover exactly one interval")

        # SPEC 24: settled bars only.
        closed = sorted(
            (c for c in candles if c.is_closed), key=lambda c: c.open_time
        )
        excluded = len(candles) - len(closed)
        if not closed:
            raise InsufficientData(
                f"all {len(candles)} candle(s) are still forming; "
                "an unsettled bar cannot be used as evidence"
            )

        return cls(
            market=first.market,
            symbol=first.symbol,
            interval=first.interval,
            opens=tuple(float(c.open) for c in closed),
            highs=tuple(float(c.high) for c in closed),
            lows=tuple(float(c.low) for c in closed),
            closes=tuple(float(c.close) for c in closed),
            volumes=tuple(float(c.volume) for c in closed),
            times=tuple(c.open_time for c in closed),
            close_times=tuple(c.close_time for c in closed),
            excluded_open_bars=excluded,
        )

    @classmethod
    def from_rows(
        cls, rows: list[dict], *, market: Market, symbol: str, interval: Horizon
    ) -> CandleSeries:
        """Build from repository rows (already closed-only by query)."""
        if not rows:
            raise InsufficientData(f"no stored candles for {symbol} {interval.value}")
        ordered = sorted(rows, key=lambda r: r["open_time"])
        return cls(
            market=market,
            symbol=symbol,
            interval=interval,
            opens=tuple(float(r["open"]) for r in ordered),
            highs=tuple(float(r["high"]) for r in ordered),
            lows=tuple(float(r["low"]) for r in ordered),
            closes=tuple(float(r["close"]) for r in ordered),
            volumes=tuple(float(r["volume"]) for r in ordered),
            times=tuple(r["open_time"] for r in ordered),
            close_times=tuple(r["close_time"] for r in ordered),
        )

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def last_close(self) -> float:
        return self.closes[-1]

    @property
    def last_time(self) -> datetime:
        """Open time of the newest settled bar."""
        return self.times[-1]

    @property
    def data_through(self) -> datetime:
        """The instant this series knows the market through (SPEC 24).

        The close of the newest settled bar. Falls back to its open time only
        when a caller built the series without close times, which is worse but
        never optimistic.
        """
        return self.close_times[-1] if self.close_times else self.times[-1]

    @property
    def typical_prices(self) -> tuple[float, ...]:
        """(high + low + close) / 3 per bar - the VWAP and CCI input."""
        return tuple(
            (h + low + c) / 3
            for h, low, c in zip(self.highs, self.lows, self.closes, strict=True)
        )

    def has(self, bars: int) -> bool:
        return len(self) >= bars

    def tail(self, bars: int) -> CandleSeries:
        """The most recent ``bars`` bars."""
        if bars >= len(self):
            return self
        return CandleSeries(
            market=self.market,
            symbol=self.symbol,
            interval=self.interval,
            opens=self.opens[-bars:],
            highs=self.highs[-bars:],
            lows=self.lows[-bars:],
            closes=self.closes[-bars:],
            volumes=self.volumes[-bars:],
            times=self.times[-bars:],
            close_times=self.close_times[-bars:],
            excluded_open_bars=self.excluded_open_bars,
        )


__all__ = ["CandleSeries", "InsufficientData"]
