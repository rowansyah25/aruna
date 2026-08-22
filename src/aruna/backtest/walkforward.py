"""Walk-forward and out-of-sample (SPEC 37, 38).

An important thing to say plainly before any of this is used:

**ARUNA fits no parameters.** Every threshold in the system - the confidence
floor, the ATR multiple, the decision margin, the veto conditions - is a written
constant, chosen for a stated reason and changed only by editing code. Nothing
is optimised against history.

That changes what these tools mean. Walk-forward analysis exists to catch
parameters tuned to one period and useless in the next. With nothing tuned, a
walk-forward here measures something narrower and still worth knowing:
**whether the rules behave consistently across different market periods**. A
strategy that works only in the fold containing a trend is fragile even if
nobody fitted it.

The out-of-sample holdout is built now for a use that arrives later. PHASE 10
introduces model proposals, and the moment a human starts choosing between
variants on the strength of backtest numbers, the tuning problem is real. The
holdout is reserved and enforced from today so it is genuinely untouched when
that day comes - a holdout created after the tuning starts is worthless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aruna.core.errors import ArunaError

#: Share of the period reserved and never reported on alongside tuning work.
DEFAULT_HOLDOUT_FRACTION = 0.25

#: Fewer folds than this and "consistency across periods" means nothing.
MIN_FOLDS = 3

#: A fold with fewer resolved predictions than this reports no accuracy, for
#: the same reason a calibration bucket does not (SPEC 29).
MIN_FOLD_SAMPLE = 10


class HoldoutViolation(ArunaError):
    """An attempt to look at reserved data."""


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    start: datetime
    end: datetime

    @property
    def label(self) -> str:
        return f"fold {self.index}: {self.start:%Y-%m-%d} to {self.end:%Y-%m-%d}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold": self.index,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class Split:
    """A period divided into folds, with a reserved tail (SPEC 38)."""

    folds: tuple[Fold, ...]
    holdout_start: datetime
    holdout_end: datetime

    @property
    def holdout(self) -> Fold:
        return Fold(index=-1, start=self.holdout_start, end=self.holdout_end)

    def check_within_evaluation(self, moment: datetime) -> None:
        """Raise if ``moment`` falls in the reserved tail.

        Called by anything that evaluates during development. The holdout is
        not secret - it can be run deliberately and reported as such - but it
        must never be reached by accident while comparing variants.
        """
        if moment >= self.holdout_start:
            raise HoldoutViolation(
                f"{moment.isoformat()} is inside the reserved out-of-sample "
                f"period starting {self.holdout_start.isoformat()}. Evaluating "
                "here while choosing between variants would spend the only "
                "untouched data this system has."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": [f.to_dict() for f in self.folds],
            "holdout": {
                "start": self.holdout_start.isoformat(),
                "end": self.holdout_end.isoformat(),
            },
            "note": (
                "the holdout is reserved for PHASE 10, when model proposals "
                "start being chosen on the strength of backtest numbers; a "
                "holdout created after that point would be worthless"
            ),
        }


def split_period(
    start: datetime,
    end: datetime,
    *,
    folds: int = 4,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> Split:
    """Divide a period into folds plus a reserved tail (SPEC 37, 38).

    The holdout is the *most recent* slice, not a random one. Market regimes are
    serially correlated, so a random holdout leaks: neighbouring days resemble
    each other, and a model tuned on one has effectively seen the other.
    """
    if end <= start:
        raise ValueError("end must be after start")
    if folds < MIN_FOLDS:
        raise ValueError(
            f"{folds} fold(s) cannot show consistency across periods; "
            f"{MIN_FOLDS} is the minimum"
        )
    if not 0.0 < holdout_fraction < 0.5:
        raise ValueError("holdout fraction must be between 0 and 0.5")

    total = (end - start).total_seconds()
    holdout_seconds = total * holdout_fraction
    holdout_start = end - timedelta(seconds=holdout_seconds)

    evaluated = (holdout_start - start).total_seconds()
    step = evaluated / folds
    boundaries = [
        start + timedelta(seconds=step * i) for i in range(folds + 1)
    ]
    return Split(
        folds=tuple(
            Fold(index=i + 1, start=boundaries[i], end=boundaries[i + 1])
            for i in range(folds)
        ),
        holdout_start=holdout_start,
        holdout_end=end,
    )


@dataclass(slots=True)
class FoldResult:
    fold: Fold
    resolved: int = 0
    correct: int = 0
    published: int = 0
    net_pnl: str = "0"

    @property
    def sufficient(self) -> bool:
        return self.resolved >= MIN_FOLD_SAMPLE

    @property
    def accuracy(self) -> float | None:
        if not self.sufficient or not self.resolved:
            return None
        return round(self.correct / self.resolved, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.fold.to_dict(),
            "published": self.published,
            "resolved": self.resolved,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "net_pnl": self.net_pnl,
            "status": "OK" if self.sufficient else "INSUFFICIENT_SAMPLE",
        }


@dataclass(slots=True)
class WalkForwardReport:
    results: list[FoldResult] = field(default_factory=list)
    holdout: FoldResult | None = None

    @property
    def measured(self) -> list[FoldResult]:
        return [r for r in self.results if r.sufficient]

    @property
    def verdict(self) -> str:
        """What the spread across folds says, or why it says nothing."""
        measured = self.measured
        if len(measured) < MIN_FOLDS:
            return (
                f"INSUFFICIENT SAMPLE: {len(measured)} of {len(self.results)} "
                f"fold(s) reached {MIN_FOLD_SAMPLE} resolved predictions. "
                "Consistency across periods cannot be assessed."
            )
        accuracies = [r.accuracy or 0.0 for r in measured]
        spread = max(accuracies) - min(accuracies)
        if spread > 0.3:
            return (
                f"INCONSISTENT: accuracy ranges {min(accuracies) * 100:.0f}% to "
                f"{max(accuracies) * 100:.0f}% across folds - the rules behave "
                "very differently in different market periods"
            )
        return (
            f"CONSISTENT within {spread * 100:.0f} points across "
            f"{len(measured)} fold(s)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": [r.to_dict() for r in self.results],
            "holdout": self.holdout.to_dict() if self.holdout else None,
            "verdict": self.verdict,
            "note": (
                "ARUNA fits no parameters, so this measures consistency across "
                "market periods rather than guarding against curve-fitting. "
                "It becomes an overfitting guard in PHASE 10, when model "
                "variants start being chosen on backtest results"
            ),
        }


__all__ = [
    "DEFAULT_HOLDOUT_FRACTION",
    "MIN_FOLDS",
    "MIN_FOLD_SAMPLE",
    "Fold",
    "FoldResult",
    "HoldoutViolation",
    "Split",
    "WalkForwardReport",
    "split_period",
]
