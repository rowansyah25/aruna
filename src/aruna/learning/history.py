"""The measured SPEC 16 factors, supplied to the judge (SPEC 29, 30).

This is the seam PHASE 6 left open. The judge asks for historical reliability
and confidence calibration; from PHASE 8 there is somewhere to ask.

The important behaviour is the refusal. :class:`MeasuredHistory` answers
``None`` for anything it has not measured enough of, and ``None`` means the
judge keeps that factor neutral and keeps reporting it as unavailable. A system
that started re-weighting its agents after four resolved predictions would be
learning from noise, and - worse - would be doing it invisibly, since a weight
carries no label saying how much evidence set it.

The snapshot is deliberately immutable and taken once per run. Weights that
shifted between two decisions in the same batch would make those decisions
incomparable, and SPEC 39's replay guarantee would be lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.core.enums import AgentRole
from aruna.learning.calibration import Bucket, CalibrationReport, calibrate
from aruna.learning.reliability import (
    AgentRecord,
    ReliabilityReport,
    build_reliability,
)
from aruna.learning.weights import ApprovedWeights, propose_weights


@dataclass(frozen=True, slots=True)
class MeasuredHistory:
    """Reliability and calibration as measured at one instant."""

    reliability_report: ReliabilityReport
    calibration_report: CalibrationReport
    #: Bobot yang seorang manusia setujui (PASAL 11.11). Kosong = semua 1,0.
    approved_weights: ApprovedWeights = field(default_factory=ApprovedWeights)

    def reliability(self, role: AgentRole) -> float | None:
        """Bobot yang BERLAKU untuk agent ini, bukan yang menurut pengukuran pantas.

        Sebelumnya metode ini mengembalikan ``reliability_report.multiplier``
        langsung - pengali yang dihitung dari akurasi. Artinya begitu seorang
        agent melewati dua puluh lima opini terskor, bobotnya berubah sendiri
        dan putusan council ikut bergeser, tanpa proposal dan tanpa satu pun
        manusia menyetujuinya. PASAL 11.11 dan 11.16 melarang itu.

        Pengukurannya tidak dibuang - ia tetap dihitung dan tetap dilaporkan
        lewat ``reliability_report``, dan menjadi bahan proposal. Yang berubah
        hanya satu hal: ia tidak lagi berlaku dengan sendirinya.

        ``None`` untuk agent yang bobotnya belum pernah disetujui, dan itu
        disengaja. Judge memperlakukan ``None`` sebagai netral DAN mencatat
        faktornya sebagai tidak tersedia pada keputusan tersimpan - yang jujur:
        tidak ada penyesuaian keandalan yang diterapkan pada keputusan itu.
        Mengembalikan 1,0 akan membuat "tidak ada bobot yang disetujui" terlihat
        sama dengan "bobotnya disetujui dan kebetulan 1,0".
        """
        key = role.value
        if key not in self.approved_weights.weights:
            return None
        return self.approved_weights.for_role(role)

    def proposals(self) -> tuple[Any, ...]:
        """Perubahan bobot yang layak diusulkan ke manusia (PASAL 11.11)."""
        return propose_weights(
            self.reliability_report.records, self.approved_weights
        )

    def calibration(self, confidence: float) -> float | None:
        return self.calibration_report.multiplier(confidence)

    @property
    def measurable(self) -> bool:
        """True when at least one factor can actually be applied."""
        return bool(self.reliability_report.measured) or any(
            b.sufficient for b in self.calibration_report.buckets
        )

    def describe(self) -> dict[str, Any]:
        return {
            "reliability": self.reliability_report.to_dict(),
            "calibration": self.calibration_report.to_dict(),
            "measurable": self.measurable,
            "effect_on_judging": (
                "applied as weight multipliers"
                if self.measurable
                else "neutral - not enough resolved outcomes, and the judge "
                "reports both factors as unavailable"
            ),
        }


def empty_history() -> MeasuredHistory:
    """A history that knows nothing, and says so for every question."""
    return MeasuredHistory(
        reliability_report=build_reliability([]),
        calibration_report=calibrate([]),
    )


def history_from_snapshots(
    reliability_rows: list[dict[str, Any]],
    calibration_row: dict[str, Any] | None,
) -> MeasuredHistory:
    """Rebuild the factors from stored measurements (SPEC 39).

    Replay needs the weights that were in force when a decision was made, not
    the ones in force now. Both tables are append-only precisely so this
    reconstruction is possible; before PHASE 8 there was nothing to rebuild
    from, and a replay could only ever compare against today.

    Reads the *stored* multipliers rather than recomputing them from raw
    outcomes: recomputing would apply today's thresholds to yesterday's counts,
    which is a different question and would report a divergence that never
    happened.
    """
    records = tuple(
        AgentRecord(
            role=AgentRole(row["agent"]),
            scored=int(row["scored_opinions"]),
            correct=int(row["correct"]),
            vindicated=int(row.get("vindicated") or 0),
            overruled_correctly=int(row.get("overruled_correctly") or 0),
        )
        for row in reliability_rows
        if _known_role(row.get("agent"))
    )

    buckets: list[Bucket] = []
    total = correct = 0
    brier = None
    if calibration_row:
        total = int(calibration_row.get("total_resolved") or 0)
        correct = int(calibration_row.get("correct") or 0)
        brier = (
            float(calibration_row["brier_score"])
            if calibration_row.get("brier_score") is not None
            else None
        )
        for stored in calibration_row.get("buckets") or []:
            low, _, high = str(stored.get("bucket", "")).partition("-")
            try:
                low_f = float(low) / 100
                high_f = float(high.rstrip("%")) / 100
            except ValueError:
                continue
            buckets.append(
                Bucket(
                    low=low_f,
                    high=high_f,
                    predictions=int(stored.get("predictions") or 0),
                    correct=int(stored.get("correct") or 0),
                    mean_confidence=float(stored.get("mean_confidence") or 0),
                )
            )

    return MeasuredHistory(
        reliability_report=ReliabilityReport(records=records),
        calibration_report=CalibrationReport(
            buckets=tuple(buckets), total=total, correct=correct, brier=brier
        ),
    )


def _known_role(value: Any) -> bool:
    try:
        AgentRole(value)
    except (ValueError, TypeError):
        return False
    return True


__all__ = ["MeasuredHistory", "empty_history", "history_from_snapshots"]
