"""Confidence calibration (SPEC 29).

A system that says 80% should be right about 80% of the time. This module
measures that, and it is the reason every phase so far has refused to call a
confidence a probability: until the comparison exists, the number is an internal
score with no evidence behind it.

The hard part is not the arithmetic. It is refusing to answer.

With four resolved predictions, a bucket that went 3/4 does not mean "75%
accurate" - it means nothing at all, and a curve drawn through it is a fiction
with a chart attached. Every function here reports ``INSUFFICIENT_SAMPLE`` until
it has enough observations, and the thresholds are deliberately uncomfortable:
they are what it costs to say a number honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Confidence buckets. Edges chosen so the lock floor (0.35) starts the first
#: bucket and the ceiling (0.95) ends the last - a bucket ARUNA can never
#: populate would dilute the report with permanent blanks.
BUCKET_EDGES: tuple[tuple[float, float], ...] = (
    (0.35, 0.50),
    (0.50, 0.65),
    (0.65, 0.80),
    (0.80, 0.96),
)

#: Resolved predictions a bucket needs before its accuracy means anything.
#: At 20, a bucket that is truly 70% accurate still shows anywhere from about
#: 50% to 90% by luck alone. This is a floor for *reporting a number at all*,
#: not a claim that 20 is a good sample.
MIN_BUCKET_SAMPLE = 20

#: Resolved predictions needed before an overall calibration verdict is offered.
MIN_TOTAL_SAMPLE = 50


@dataclass(frozen=True, slots=True)
class Bucket:
    """One confidence band, and what actually happened in it."""

    low: float
    high: float
    predictions: int = 0
    correct: int = 0
    #: Mean stated confidence of the predictions that landed here.
    mean_confidence: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.low * 100:.0f}-{self.high * 100:.0f}%"

    @property
    def sufficient(self) -> bool:
        return self.predictions >= MIN_BUCKET_SAMPLE

    @property
    def accuracy(self) -> float | None:
        """Realised accuracy, or ``None`` when the sample is too small.

        ``None`` rather than a number is the whole point. A caller that wants
        to plot this has to decide what to do about the gap, which is better
        than being handed a figure that looks plottable.
        """
        if not self.sufficient or not self.predictions:
            return None
        return round(self.correct / self.predictions, 4)

    @property
    def gap(self) -> float | None:
        """Stated confidence minus realised accuracy.

        Positive means overconfident - the system claimed more than it
        delivered, which is the direction that costs money.
        """
        accuracy = self.accuracy
        if accuracy is None:
            return None
        return round(self.mean_confidence - accuracy, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.label,
            "predictions": self.predictions,
            "correct": self.correct,
            "mean_confidence": round(self.mean_confidence, 4),
            "accuracy": self.accuracy,
            "gap": self.gap,
            "status": "OK" if self.sufficient else "INSUFFICIENT_SAMPLE",
            "needs": max(0, MIN_BUCKET_SAMPLE - self.predictions),
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    buckets: tuple[Bucket, ...] = field(default_factory=tuple)
    total: int = 0
    correct: int = 0
    #: Mean squared error between stated confidence and outcome. Lower is
    #: better; 0.25 is what you get by always saying 50%.
    brier: float | None = None

    @property
    def sufficient(self) -> bool:
        return self.total >= MIN_TOTAL_SAMPLE

    @property
    def verdict(self) -> str:
        """A sentence about the system's honesty with itself."""
        if not self.sufficient:
            return (
                f"INSUFFICIENT SAMPLE: {self.total} resolved directional "
                f"prediction(s), {MIN_TOTAL_SAMPLE} needed before calibration "
                "can be assessed. No claim is made about confidence."
            )
        measured = [b for b in self.buckets if b.sufficient]
        if not measured:
            return (
                "INSUFFICIENT SAMPLE: no confidence bucket has reached "
                f"{MIN_BUCKET_SAMPLE} predictions, so the overall total says "
                "nothing about any particular confidence level."
            )
        overconfident = [b for b in measured if (b.gap or 0) > 0.10]
        underconfident = [b for b in measured if (b.gap or 0) < -0.10]
        if overconfident:
            bands = ", ".join(b.label for b in overconfident)
            return f"OVERCONFIDENT in {bands}: stated confidence exceeds accuracy"
        if underconfident:
            bands = ", ".join(b.label for b in underconfident)
            return f"UNDERCONFIDENT in {bands}: accuracy exceeds stated confidence"
        return "CALIBRATED within 10 points across every measured bucket"

    def multiplier(self, confidence: float) -> float | None:
        """Correction factor for a stated confidence (SPEC 16, 29).

        Returns ``None`` when this confidence level has not been measured
        enough to correct. The judge treats ``None`` as neutral and keeps
        declaring the factor unavailable - a correction invented from four
        observations would be worse than none.
        """
        for bucket in self.buckets:
            if bucket.low <= confidence < bucket.high and bucket.sufficient:
                accuracy = bucket.accuracy
                if accuracy is None or bucket.mean_confidence <= 0:
                    return None
                # Scale toward realised accuracy, clamped so a single bad
                # stretch cannot silence or inflate an agent outright.
                return round(min(1.5, max(0.5, accuracy / bucket.mean_confidence)), 4)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_resolved": self.total,
            "correct": self.correct,
            "raw_accuracy": (
                round(self.correct / self.total, 4) if self.total else None
            ),
            "brier_score": self.brier,
            "buckets": [b.to_dict() for b in self.buckets],
            "sufficient_sample": self.sufficient,
            "verdict": self.verdict,
            "note": (
                "raw_accuracy is a count, not a calibrated probability; a "
                "bucket reports accuracy only once it has "
                f"{MIN_BUCKET_SAMPLE} predictions"
            ),
        }


def calibrate(records: list[dict[str, Any]]) -> CalibrationReport:
    """Compare stated confidence against realised accuracy (SPEC 29).

    ``records`` need ``confidence`` and ``direction_correct``. Only directional
    predictions belong here: a WAIT has no direction to be right about, and
    including them would let a cautious system look accurate by never calling
    anything.
    """
    usable = [
        r
        for r in records
        if r.get("confidence") is not None and r.get("direction_correct") is not None
    ]
    if not usable:
        return CalibrationReport(
            buckets=tuple(Bucket(low, high) for low, high in BUCKET_EDGES)
        )

    tallies: dict[tuple[float, float], list[tuple[float, bool]]] = {
        edges: [] for edges in BUCKET_EDGES
    }
    squared_error = 0.0
    correct_total = 0

    for record in usable:
        confidence = float(record["confidence"])
        correct = bool(record["direction_correct"])
        correct_total += int(correct)
        squared_error += (confidence - float(correct)) ** 2
        for edges in BUCKET_EDGES:
            if edges[0] <= confidence < edges[1]:
                tallies[edges].append((confidence, correct))
                break

    buckets = []
    for (low, high), entries in tallies.items():
        if entries:
            buckets.append(
                Bucket(
                    low=low,
                    high=high,
                    predictions=len(entries),
                    correct=sum(1 for _, ok in entries if ok),
                    mean_confidence=sum(c for c, _ in entries) / len(entries),
                )
            )
        else:
            buckets.append(Bucket(low=low, high=high))

    return CalibrationReport(
        buckets=tuple(buckets),
        total=len(usable),
        correct=correct_total,
        brier=round(squared_error / len(usable), 4),
    )


__all__ = [
    "BUCKET_EDGES",
    "MIN_BUCKET_SAMPLE",
    "MIN_TOTAL_SAMPLE",
    "Bucket",
    "CalibrationReport",
    "calibrate",
]
