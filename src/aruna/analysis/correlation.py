"""Correlation between assets (SPEC 4, 17, 32).

Two jobs, both about not double-counting or mispricing risk:

* **SPEC 32 risk** - a portfolio of five assets that move together is one
  position wearing five names.
* **SPEC 17 evidence independence** - when later phases weigh agent evidence,
  two agents reading two highly-correlated assets are not independent
  witnesses, and the judge needs to know that.

Correlation is computed on **returns**, never on raw prices. Two assets that
both drift upward show a high price correlation that says nothing about whether
they move together day to day; that is the classic spurious result.

Pearson only, and only where the windows genuinely overlap in time - pairing the
Nth bar of one asset with the Nth of another is wrong the moment one has a gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from aruna.analysis.series import CandleSeries

#: Below this many overlapping returns, a coefficient is noise.
MIN_OVERLAP = 20

#: Conventional reading of |r|. Labels, not thresholds anything acts on.
STRONG = 0.7
MODERATE = 0.4


@dataclass(frozen=True, slots=True)
class CorrelationPair:
    left: str
    right: str
    coefficient: float
    overlap: int
    #: Time span the overlap actually covered.
    first: datetime | None = None
    last: datetime | None = None

    @property
    def reliable(self) -> bool:
        return self.overlap >= MIN_OVERLAP

    @property
    def strength(self) -> str:
        magnitude = abs(self.coefficient)
        if magnitude >= STRONG:
            return "STRONG"
        if magnitude >= MODERATE:
            return "MODERATE"
        return "WEAK"

    @property
    def direction(self) -> str:
        if self.coefficient > 0:
            return "POSITIVE"
        if self.coefficient < 0:
            return "NEGATIVE"
        return "FLAT"

    def to_dict(self) -> dict[str, object]:
        return {
            "left": self.left,
            "right": self.right,
            "coefficient": round(self.coefficient, 4),
            "overlap": self.overlap,
            "reliable": self.reliable,
            "strength": self.strength,
            "direction": self.direction,
        }


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    interval: str
    pairs: tuple[CorrelationPair, ...]
    computed_at: datetime
    skipped: tuple[str, ...] = ()

    def for_symbol(self, symbol: str) -> list[CorrelationPair]:
        return [p for p in self.pairs if symbol in (p.left, p.right)]

    def strongly_correlated(self, threshold: float = STRONG) -> list[CorrelationPair]:
        """Pairs that behave as one position (SPEC 32 concentration risk)."""
        return [
            p for p in self.pairs if p.reliable and abs(p.coefficient) >= threshold
        ]

    def average_absolute(self) -> float | None:
        usable = [abs(p.coefficient) for p in self.pairs if p.reliable]
        return sum(usable) / len(usable) if usable else None

    def to_dict(self) -> dict[str, object]:
        return {
            "interval": self.interval,
            "pairs": [p.to_dict() for p in self.pairs],
            "skipped": list(self.skipped),
            "average_absolute": self.average_absolute(),
        }


def returns_by_time(series: CandleSeries) -> dict[datetime, float]:
    """Bar-to-bar percentage returns, keyed by the bar's open time.

    Keying by time is what makes the later join correct: two assets are only
    compared where they actually have a bar for the same moment.
    """
    out: dict[datetime, float] = {}
    for index in range(1, len(series)):
        previous = series.closes[index - 1]
        if previous == 0:
            continue
        out[series.times[index]] = (series.closes[index] - previous) / previous * 100.0
    return out


def pearson(left: list[float], right: list[float]) -> float | None:
    """Pearson correlation. None when either side has no variation."""
    count = len(left)
    if count < 2 or count != len(right):
        return None

    mean_left = sum(left) / count
    mean_right = sum(right) / count
    covariance = sum(
        (a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True)
    )
    var_left = sum((a - mean_left) ** 2 for a in left)
    var_right = sum((b - mean_right) ** 2 for b in right)

    denominator = math.sqrt(var_left * var_right)
    if denominator == 0:
        # A flat series has no correlation with anything - it has no variance
        # to correlate. Returning 0 would imply "independent", which is a
        # different and unsupported claim.
        return None
    return covariance / denominator


def correlate(left: CandleSeries, right: CandleSeries) -> CorrelationPair | None:
    """Correlation between two series over their overlapping bars."""
    left_returns = returns_by_time(left)
    right_returns = returns_by_time(right)

    shared = sorted(set(left_returns) & set(right_returns))
    if not shared:
        return None

    coefficient = pearson(
        [left_returns[t] for t in shared], [right_returns[t] for t in shared]
    )
    if coefficient is None:
        return None

    return CorrelationPair(
        left=left.symbol,
        right=right.symbol,
        coefficient=coefficient,
        overlap=len(shared),
        first=shared[0],
        last=shared[-1],
    )


def build_matrix(
    series_by_symbol: dict[str, CandleSeries], *, interval: str, computed_at: datetime
) -> CorrelationMatrix:
    """Every distinct pair among the given series."""
    symbols = sorted(series_by_symbol)
    pairs: list[CorrelationPair] = []
    skipped: list[str] = []

    for index, left_symbol in enumerate(symbols):
        for right_symbol in symbols[index + 1 :]:
            pair = correlate(
                series_by_symbol[left_symbol], series_by_symbol[right_symbol]
            )
            if pair is None:
                skipped.append(
                    f"{left_symbol}/{right_symbol}: no overlapping bars with variance"
                )
                continue
            if not pair.reliable:
                skipped.append(
                    f"{left_symbol}/{right_symbol}: only {pair.overlap} overlapping "
                    f"bars, need {MIN_OVERLAP}"
                )
            pairs.append(pair)

    return CorrelationMatrix(
        interval=interval,
        pairs=tuple(pairs),
        computed_at=computed_at,
        skipped=tuple(skipped),
    )


def concentration_warning(matrix: CorrelationMatrix) -> str | None:
    """One line for SPEC 32, or None when nothing is unusually clustered."""
    clustered = matrix.strongly_correlated()
    if not clustered:
        return None
    names = ", ".join(f"{p.left}/{p.right}" for p in clustered[:5])
    return (
        f"{len(clustered)} strongly correlated pair(s) - these move as one "
        f"position, not several: {names}"
    )


__all__ = [
    "MIN_OVERLAP",
    "MODERATE",
    "STRONG",
    "CorrelationMatrix",
    "CorrelationPair",
    "build_matrix",
    "concentration_warning",
    "correlate",
    "pearson",
    "returns_by_time",
]
