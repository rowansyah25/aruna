"""Market structure (SPEC 6).

Swing points, higher-high/lower-low sequencing, support and resistance,
breakout, false breakout, retest, rejection, compression, expansion, and gaps.

Two things worth stating plainly, because structure analysis invites false
precision:

* A swing point is only confirmed once ``lookback`` bars have formed **on both
  sides** of it. The most recent bars therefore cannot contain a confirmed
  swing - and pretending otherwise is a quiet form of look-ahead, since you
  would be calling a pivot that later data has not yet earned.
* A "false breakout" can only be identified after price comes back. Labelling
  one in real time would be a prediction, not an observation, so it is reported
  only once the return has actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from aruna.analysis.reading import Reading
from aruna.analysis.series import CandleSeries

DEFAULT_SWING_LOOKBACK = 3
DEFAULT_LEVEL_TOLERANCE_PCT = 0.35


class SwingKind(StrEnum):
    HIGH = "HIGH"
    LOW = "LOW"


class TrendStructure(StrEnum):
    """SPEC 6: higher high / lower low sequencing."""

    UPTREND = "UPTREND"          # higher highs and higher lows
    DOWNTREND = "DOWNTREND"      # lower highs and lower lows
    RANGE = "RANGE"              # neither sequence holds
    UNDETERMINED = "UNDETERMINED"  # not enough confirmed swings


class BreakoutState(StrEnum):
    NONE = "NONE"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"
    FALSE_BREAKOUT_UP = "FALSE_BREAKOUT_UP"
    FALSE_BREAKOUT_DOWN = "FALSE_BREAKOUT_DOWN"
    RETEST = "RETEST"
    REJECTION = "REJECTION"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    kind: SwingKind
    index: int
    price: float
    time: datetime


@dataclass(frozen=True, slots=True)
class Level:
    """A support or resistance level built from clustered swing points."""

    price: float
    touches: int
    is_support: bool
    last_touch: datetime

    def distance_pct(self, price: float) -> float:
        return (price - self.price) / self.price * 100.0 if self.price else 0.0


@dataclass(frozen=True, slots=True)
class StructureReport:
    trend: TrendStructure
    breakout: BreakoutState
    swings: tuple[SwingPoint, ...] = ()
    support: tuple[Level, ...] = ()
    resistance: tuple[Level, ...] = ()
    detail: str = ""
    #: Confirmed swings behind the verdict - the sample size for structure.
    confirmed_swings: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def reliable(self) -> bool:
        """Four confirmed swings is the minimum for a two-leg sequence."""
        return self.confirmed_swings >= 4

    def to_dict(self) -> dict[str, object]:
        return {
            "trend": self.trend.value,
            "breakout": self.breakout.value,
            "confirmed_swings": self.confirmed_swings,
            "reliable": self.reliable,
            "detail": self.detail or None,
            "notes": list(self.notes),
            "support": [
                {"price": lvl.price, "touches": lvl.touches} for lvl in self.support
            ],
            "resistance": [
                {"price": lvl.price, "touches": lvl.touches} for lvl in self.resistance
            ],
        }


# ---------------------------------------------------------------------------
# Swings
# ---------------------------------------------------------------------------


def find_swings(
    series: CandleSeries, lookback: int = DEFAULT_SWING_LOOKBACK
) -> list[SwingPoint]:
    """Confirmed swing highs and lows.

    A pivot needs ``lookback`` bars either side, so the final ``lookback`` bars
    can never contain one. That is a real constraint, not a rounding detail:
    calling a pivot before the confirming bars exist means using data that has
    not happened.
    """
    swings: list[SwingPoint] = []
    if len(series) < lookback * 2 + 1:
        return swings

    for index in range(lookback, len(series) - lookback):
        window = range(index - lookback, index + lookback + 1)
        high = series.highs[index]
        low = series.lows[index]

        if all(high >= series.highs[i] for i in window) and any(
            high > series.highs[i] for i in window if i != index
        ):
            swings.append(
                SwingPoint(SwingKind.HIGH, index, high, series.times[index])
            )
        elif all(low <= series.lows[i] for i in window) and any(
            low < series.lows[i] for i in window if i != index
        ):
            swings.append(SwingPoint(SwingKind.LOW, index, low, series.times[index]))

    return swings


def classify_trend(swings: list[SwingPoint]) -> tuple[TrendStructure, str]:
    """Higher-high/higher-low sequencing over the last two swings of each kind."""
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    lows = [s for s in swings if s.kind is SwingKind.LOW]

    if len(highs) < 2 or len(lows) < 2:
        return TrendStructure.UNDETERMINED, "need two confirmed swings of each kind"

    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price

    if higher_high and higher_low:
        return TrendStructure.UPTREND, "higher highs and higher lows"
    if lower_high and lower_low:
        return TrendStructure.DOWNTREND, "lower highs and lower lows"
    return TrendStructure.RANGE, "swing sequence is mixed"


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------


def build_levels(
    swings: list[SwingPoint],
    *,
    tolerance_pct: float = DEFAULT_LEVEL_TOLERANCE_PCT,
    min_touches: int = 2,
) -> tuple[list[Level], list[Level]]:
    """Cluster swing points into support and resistance levels.

    A level touched once is just a swing point; ``min_touches`` is what makes it
    a level worth naming.
    """
    def cluster(points: list[SwingPoint], is_support: bool) -> list[Level]:
        groups: list[list[SwingPoint]] = []
        for point in sorted(points, key=lambda p: p.price):
            if groups and _within(groups[-1][0].price, point.price, tolerance_pct):
                groups[-1].append(point)
            else:
                groups.append([point])

        levels = []
        for group in groups:
            if len(group) < min_touches:
                continue
            levels.append(
                Level(
                    price=sum(p.price for p in group) / len(group),
                    touches=len(group),
                    is_support=is_support,
                    last_touch=max(p.time for p in group),
                )
            )
        return sorted(levels, key=lambda lvl: lvl.touches, reverse=True)

    lows = [s for s in swings if s.kind is SwingKind.LOW]
    highs = [s for s in swings if s.kind is SwingKind.HIGH]
    return cluster(lows, True), cluster(highs, False)


def _within(a: float, b: float, tolerance_pct: float) -> bool:
    if a == 0:
        return False
    return abs(b - a) / abs(a) * 100.0 <= tolerance_pct


# ---------------------------------------------------------------------------
# Breakouts
# ---------------------------------------------------------------------------


def detect_breakout(
    series: CandleSeries,
    support: list[Level],
    resistance: list[Level],
    *,
    tolerance_pct: float = DEFAULT_LEVEL_TOLERANCE_PCT,
    confirm_bars: int = 3,
) -> tuple[BreakoutState, str]:
    """Classify recent price action against the nearest levels.

    A false breakout is only reported once price has actually come back inside.
    Calling one while price is still outside would be a forecast dressed up as
    an observation.
    """
    if len(series) < confirm_bars + 1:
        return BreakoutState.NONE, "not enough bars to judge a breakout"

    close = series.last_close
    recent_high = max(series.highs[-confirm_bars:])
    recent_low = min(series.lows[-confirm_bars:])

    # The *nearest* level to current price, not the most-touched one. Picking
    # by touch count alone selects a level that may sit far from where price
    # actually is, and then `close < level` is trivially true - which reports a
    # breakout on every asset at once.
    top = _nearest(resistance, close)
    bottom = _nearest(support, close)

    # A level only counts as challenged if price reached it inside the confirm
    # window. Without that, a distant level can still trigger a verdict.
    if top is not None and recent_high > top.price:
        if close > top.price and not _within(top.price, close, tolerance_pct):
            return BreakoutState.BREAKOUT_UP, f"closed above resistance {top.price:.2f}"
        if close < top.price and not _within(top.price, close, tolerance_pct):
            # Went through and came back: only now is it observable.
            return (
                BreakoutState.FALSE_BREAKOUT_UP,
                f"pierced resistance {top.price:.2f} then closed back below",
            )
        return BreakoutState.REJECTION, f"rejected at resistance {top.price:.2f}"

    if bottom is not None and recent_low < bottom.price:
        if close < bottom.price and not _within(bottom.price, close, tolerance_pct):
            return BreakoutState.BREAKOUT_DOWN, f"closed below support {bottom.price:.2f}"
        if close > bottom.price and not _within(bottom.price, close, tolerance_pct):
            return (
                BreakoutState.FALSE_BREAKOUT_DOWN,
                f"pierced support {bottom.price:.2f} then closed back above",
            )
        return BreakoutState.RETEST, f"retesting support {bottom.price:.2f}"

    return BreakoutState.NONE, "price is inside its recent range"


def _nearest(levels: list[Level], price: float) -> Level | None:
    """The level closest to ``price``, or None when there are none."""
    return min(levels, key=lambda lvl: abs(lvl.price - price), default=None)


def analyse_structure(
    series: CandleSeries,
    *,
    lookback: int = DEFAULT_SWING_LOOKBACK,
    tolerance_pct: float = DEFAULT_LEVEL_TOLERANCE_PCT,
) -> StructureReport:
    swings = find_swings(series, lookback)
    if not swings:
        return StructureReport(
            trend=TrendStructure.UNDETERMINED,
            breakout=BreakoutState.NONE,
            detail=f"no confirmed swings in {len(series)} bars",
        )

    trend, trend_detail = classify_trend(swings)
    support, resistance = build_levels(swings, tolerance_pct=tolerance_pct)
    breakout, breakout_detail = detect_breakout(
        series, support, resistance, tolerance_pct=tolerance_pct
    )

    notes = [trend_detail, breakout_detail]
    if series.excluded_open_bars:
        notes.append(
            f"{series.excluded_open_bars} unsettled bar(s) excluded (SPEC 24)"
        )

    return StructureReport(
        trend=trend,
        breakout=breakout,
        swings=tuple(swings[-12:]),
        support=tuple(support[:3]),
        resistance=tuple(resistance[:3]),
        detail=trend_detail,
        confirmed_swings=len(swings),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Compression / expansion / gaps
# ---------------------------------------------------------------------------


def compression(series: CandleSeries, period: int = 20) -> Reading:
    """Recent bar range against the prior window.

    Below 1.0 is compression (a coiling range), above is expansion.
    """
    need = period * 2
    if not series.has(need):
        return Reading.insufficient("compression", have=len(series), need=need)

    def average_range(highs, lows) -> float:
        spans = [h - low for h, low in zip(highs, lows, strict=True)]
        return sum(spans) / len(spans) if spans else 0.0

    recent = average_range(series.highs[-period:], series.lows[-period:])
    prior = average_range(series.highs[-need:-period], series.lows[-need:-period])
    if prior <= 0:
        return Reading(
            "compression", None, len(series), need, detail="no prior range to compare"
        )

    ratio = recent / prior
    if ratio <= 0.7:
        detail = "compression: range tightening"
    elif ratio >= 1.4:
        detail = "expansion: range widening"
    else:
        detail = "range broadly stable"

    return Reading(
        name="compression",
        value=ratio,
        sample_size=len(series),
        required=need,
        detail=detail,
    )


def gap(series: CandleSeries) -> Reading:
    """Opening gap between the last two bars, as a percentage.

    Common on IDX across sessions; on a 24/7 crypto venue a real gap is rare
    and worth noticing.
    """
    if not series.has(2):
        return Reading.insufficient("gap", have=len(series), need=2)

    previous_close = series.closes[-2]
    current_open = series.opens[-1]
    if previous_close == 0:
        return Reading("gap", None, len(series), 2, detail="zero reference close")

    change = (current_open - previous_close) / previous_close * 100.0
    if abs(change) < 0.1:
        detail = "no meaningful gap"
    else:
        detail = f"gap {'up' if change > 0 else 'down'} {abs(change):.2f}%"

    return Reading(
        name="gap", value=change, sample_size=len(series), required=2, detail=detail
    )


__all__ = [
    "BreakoutState",
    "Level",
    "StructureReport",
    "SwingKind",
    "SwingPoint",
    "TrendStructure",
    "analyse_structure",
    "build_levels",
    "classify_trend",
    "compression",
    "detect_breakout",
    "find_swings",
    "gap",
]
