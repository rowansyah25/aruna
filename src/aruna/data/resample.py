"""Exact candle aggregation.

Some horizons the specification asks for are not offered natively by the
providers ARUNA uses: Binance spot has no 10m interval, and Yahoo has no 3-day
or 5-day interval.  Those can be built from smaller bars - but only by
arithmetic, never by estimation.

3m is no longer in that list.  Binance publishes it natively, so crypto 3m bars
now come from the venue and resampling them would be redundant work on top of
worse data.  The machinery below still handles 3m because IDX has no such
interval either and the function is not market-specific.

The rule here is that a derived candle is produced **only when every
constituent bar is present**.  An aggregate built over a gap is wrong in a way
that looks perfectly plausible, and it would silently poison any backtest
(SPEC 35) built on top of it.  Incomplete buckets are dropped and reported, not
approximated (SPEC 4).

Derived candles carry ``source`` marked as resampled, so no later phase can
mistake them for something the venue actually published.

**Only for continuously-traded series.** Buckets are calendar-aligned, which is
correct for crypto and wrong for equities: SPEC 3's 3D and 5D IDX horizons mean
*trading* days, and a calendar bucket spanning a weekend silently contains
fewer sessions than it claims. Those horizons are prediction windows evaluated
over daily candles in PHASE 7, not bar sizes to be manufactured here.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from aruna.core.enums import Horizon
from aruna.data.models import Candle

#: Marks a source as derived rather than published.
RESAMPLED_SUFFIX = ":resampled"


def resampled_source(source: str, base: Horizon) -> str:
    return f"{source}{RESAMPLED_SUFFIX}({base.value})"


def is_resampled(source: str) -> bool:
    return RESAMPLED_SUFFIX in source


def can_resample(base: Horizon, target: Horizon) -> bool:
    """True when ``target`` is a whole multiple of ``base``.

    Anything else would need to split a bar, which cannot be done exactly - the
    path inside a candle is not recoverable from its OHLC.
    """
    base_sec = base.duration.total_seconds()
    target_sec = target.duration.total_seconds()
    if base_sec <= 0 or target_sec <= base_sec:
        return False
    return target_sec % base_sec == 0


def bars_per_bucket(base: Horizon, target: Horizon) -> int:
    return int(target.duration.total_seconds() // base.duration.total_seconds())


def _bucket_start(moment: datetime, target: Horizon, epoch: datetime) -> datetime:
    """Align a timestamp to its bucket, measured from a fixed epoch.

    Anchoring to an epoch rather than to the first candle in the list keeps
    bucket boundaries identical no matter which window was fetched.
    """
    step = target.duration
    elapsed = (moment - epoch).total_seconds()
    index = int(elapsed // step.total_seconds())
    return epoch + timedelta(seconds=index * step.total_seconds())


def resample_candles(
    candles: list[Candle],
    target: Horizon,
    *,
    epoch: datetime | None = None,
    require_closed: bool = True,
) -> list[Candle]:
    """Aggregate ``candles`` into ``target`` bars.

    Buckets missing any constituent bar are omitted entirely.  Use
    :func:`incomplete_buckets` to see what was dropped and why.
    """
    if not candles:
        return []

    base = candles[0].interval
    if any(c.interval is not base for c in candles):
        raise ValueError("cannot resample a mixed-interval series")
    if not can_resample(base, target):
        raise ValueError(
            f"{target.value} is not a whole multiple of {base.value}; "
            "exact aggregation is impossible"
        )

    ordered = sorted(candles, key=lambda c: c.open_time)
    if require_closed:
        ordered = [c for c in ordered if c.is_closed]
    if not ordered:
        return []

    anchor = epoch or datetime(1970, 1, 1, tzinfo=ordered[0].open_time.tzinfo)
    expected = bars_per_bucket(base, target)

    buckets: dict[datetime, list[Candle]] = {}
    for candle in ordered:
        buckets.setdefault(_bucket_start(candle.open_time, target, anchor), []).append(candle)

    out: list[Candle] = []
    for start in sorted(buckets):
        members = buckets[start]
        if len(members) != expected:
            # Incomplete: reported by incomplete_buckets(), never averaged.
            continue
        out.append(_merge(members, start, target))
    return out


def _merge(members: list[Candle], start: datetime, target: Horizon) -> Candle:
    first, last = members[0], members[-1]
    volume = sum((c.volume for c in members), start=type(first.volume)(0))
    quote_volume = (
        sum((c.quote_volume for c in members), start=type(first.volume)(0))
        if all(c.quote_volume is not None for c in members)
        else None
    )
    trade_count = (
        sum(c.trade_count or 0 for c in members)
        if all(c.trade_count is not None for c in members)
        else None
    )

    base_provenance = last.provenance
    provenance = replace(
        base_provenance,
        source=resampled_source(_strip_resample(base_provenance.source), first.interval),
    )

    return Candle(
        market=first.market,
        symbol=first.symbol,
        interval=target,
        open_time=start,
        close_time=start + target.duration,
        open=first.open,
        high=max(c.high for c in members),
        low=min(c.low for c in members),
        close=last.close,
        volume=volume,
        quote_volume=quote_volume,
        trade_count=trade_count,
        is_closed=True,
        provenance=provenance,
    )


def _strip_resample(source: str) -> str:
    return source.split(RESAMPLED_SUFFIX)[0]


def incomplete_buckets(
    candles: list[Candle], target: Horizon, *, epoch: datetime | None = None
) -> list[tuple[datetime, int, int]]:
    """Buckets that could not be built: ``(start, present, expected)``."""
    if not candles:
        return []
    base = candles[0].interval
    if not can_resample(base, target):
        return []

    ordered = sorted((c for c in candles if c.is_closed), key=lambda c: c.open_time)
    if not ordered:
        return []

    anchor = epoch or datetime(1970, 1, 1, tzinfo=ordered[0].open_time.tzinfo)
    expected = bars_per_bucket(base, target)

    counts: dict[datetime, int] = {}
    for candle in ordered:
        start = _bucket_start(candle.open_time, target, anchor)
        counts[start] = counts.get(start, 0) + 1

    return [
        (start, present, expected)
        for start, present in sorted(counts.items())
        if present != expected
    ]


__all__ = [
    "RESAMPLED_SUFFIX",
    "bars_per_bucket",
    "can_resample",
    "incomplete_buckets",
    "is_resampled",
    "resample_candles",
    "resampled_source",
]
