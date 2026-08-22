"""Maintenance: keeping stored candles current, and scoring what is due.

Two jobs that look unrelated and are not. Outcome resolution reads candles; a
frozen candle series means predictions cannot be scored, and - worse - that any
that *are* scored get a mid-horizon price recorded as their final result, which
SPEC 22 then forbids anyone to correct. So the refresh runs first and the
resolution runs second, in one cycle, in one task, on purpose.
"""

from __future__ import annotations

from aruna.upkeep.candles import (
    SAMPLING_FALLBACK_DEPTH,
    CandleRefresher,
    IntervalState,
    RefreshResult,
    bar_start,
    refresh_intervals,
)
from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

__all__ = [
    "SAMPLING_FALLBACK_DEPTH",
    "CandleRefresher",
    "IntervalState",
    "RefreshResult",
    "UpkeepLoop",
    "UpkeepStats",
    "bar_start",
    "refresh_intervals",
]
