"""Drift detection (SPEC 44).

A model is validated against a period. Drift is the question of whether the
world still resembles that period - because if it does not, the validation has
quietly expired and every decision since has been resting on it.

Two kinds are worth separating, and conflating them sends you to fix the wrong
thing:

* **Performance drift** - the rules still see the same conditions but do worse.
  That points at the rules.
* **Condition drift** - the market itself has moved: different regimes,
  different volatility, different data quality. The rules may be unchanged and
  still inapplicable.

The honest default here is *silence*. Drift over a short recent window is
mostly noise, and a detector that cries drift every fortnight trains its
operator to ignore it - which is worse than not having one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Resolved predictions needed in each window before a comparison is reported.
MIN_WINDOW_SAMPLE = 60

#: Accuracy drop, in points, that counts as performance drift rather than a bad
#: fortnight.
ACCURACY_DRIFT_POINTS = 0.10

#: Share of a regime mix that must change before conditions count as different.
REGIME_DRIFT_SHARE = 0.25


@dataclass(frozen=True, slots=True)
class Window:
    """One period's behaviour, as measured."""

    label: str
    resolved: int = 0
    correct: int = 0
    regimes: dict[str, int] = field(default_factory=dict)

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.resolved if self.resolved else None

    @property
    def regime_mix(self) -> dict[str, float]:
        total = sum(self.regimes.values())
        if not total:
            return {}
        return {name: count / total for name, count in self.regimes.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "resolved": self.resolved,
            "accuracy": round(self.accuracy, 4) if self.accuracy is not None else None,
            "regime_mix": {k: round(v, 3) for k, v in self.regime_mix.items()},
        }


@dataclass(frozen=True, slots=True)
class DriftReport:
    baseline: Window
    recent: Window
    performance_drift: float | None = None
    regime_shift: float | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sufficient(self) -> bool:
        return (
            self.baseline.resolved >= MIN_WINDOW_SAMPLE
            and self.recent.resolved >= MIN_WINDOW_SAMPLE
        )

    @property
    def verdict(self) -> str:
        if not self.sufficient:
            return (
                f"INSUFFICIENT SAMPLE: {self.baseline.resolved} and "
                f"{self.recent.resolved} resolved predictions in the two "
                f"windows; {MIN_WINDOW_SAMPLE} needed in each. No drift claim "
                "is made - most short-window drift is noise, and a detector "
                "that cries wolf gets ignored"
            )
        if not self.findings:
            return "NO DRIFT DETECTED against the validation period"
        return "; ".join(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "recent": self.recent.to_dict(),
            "performance_drift_points": (
                round(self.performance_drift * 100, 2)
                if self.performance_drift is not None
                else None
            ),
            "regime_shift": (
                round(self.regime_shift, 3) if self.regime_shift is not None else None
            ),
            "sufficient_sample": self.sufficient,
            "verdict": self.verdict,
            "findings": list(self.findings),
            "note": (
                "performance drift points at the rules; condition drift points "
                "at the market. Fixing the wrong one is worse than waiting"
            ),
        }


def detect(baseline: Window, recent: Window) -> DriftReport:
    """Compare a recent window against the period a model was validated on."""
    if baseline.resolved < MIN_WINDOW_SAMPLE or recent.resolved < MIN_WINDOW_SAMPLE:
        return DriftReport(baseline=baseline, recent=recent)

    findings: list[str] = []
    performance = None
    if baseline.accuracy is not None and recent.accuracy is not None:
        performance = recent.accuracy - baseline.accuracy
        if performance <= -ACCURACY_DRIFT_POINTS:
            findings.append(
                f"PERFORMANCE DRIFT: accuracy fell {abs(performance) * 100:.0f} "
                f"points, {baseline.accuracy * 100:.0f}% to "
                f"{recent.accuracy * 100:.0f}% - the rules are doing worse"
            )

    shift = _regime_shift(baseline.regime_mix, recent.regime_mix)
    if shift is not None and shift >= REGIME_DRIFT_SHARE:
        findings.append(
            f"CONDITION DRIFT: the regime mix moved by {shift * 100:.0f} "
            "points - the market is not the one this was validated on"
        )

    return DriftReport(
        baseline=baseline,
        recent=recent,
        performance_drift=performance,
        regime_shift=shift,
        findings=tuple(findings),
    )


def _regime_shift(
    baseline: dict[str, float], recent: dict[str, float]
) -> float | None:
    """Total variation distance between two regime mixes.

    Half the sum of absolute differences: 0 when identical, 1 when they share
    no regime at all.
    """
    if not baseline or not recent:
        return None
    names = set(baseline) | set(recent)
    return sum(abs(baseline.get(n, 0.0) - recent.get(n, 0.0)) for n in names) / 2


__all__ = [
    "ACCURACY_DRIFT_POINTS",
    "MIN_WINDOW_SAMPLE",
    "REGIME_DRIFT_SHARE",
    "DriftReport",
    "Window",
    "detect",
]
