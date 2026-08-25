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
        """Pengali yang DIUKUR untuk agent ini, berlaku tanpa persetujuan.

        **PASAL 11.11 dan 11.16 ditimpa keputusan operator pada 2026-08-25**,
        dinyatakan langsung: ARUNA harus belajar dari kesalahannya dan
        memperbaiki dirinya sendiri tanpa persetujuan per perubahan. Ini bukan
        kelalaian dan bukan penyederhanaan - ini instruksi pemilik sistem, dan
        dicatat di sini supaya siapa pun yang menemukannya nanti tahu bahwa
        pasal itu sengaja dilewati, bukan terlupa.

        Versi sebelumnya memulangkan bobot dari ``approved_weights``. Tabel yang
        mengisinya tidak pernah ada, jadi jawabannya SELALU ``None`` - dan
        terukur di ``judge_decisions``: ``historical_reliability`` tercatat
        tidak tersedia pada 100% keputusan, di setiap hari yang tersimpan.
        Seluruh mesin keandalan berjalan, mengukur, dan menulis snapshot yang
        tidak pernah menyentuh satu pun putusan.

        **Yang menggantikan persetujuan adalah pagar yang bisa diperiksa**, dan
        ketiganya sudah berdiri sebelum perubahan ini:

        * :data:`~aruna.learning.reliability.MIN_RELIABILITY_SAMPLE` - di bawah
          dua puluh lima opini terskor, :attr:`AgentRecord.multiplier` tetap
          ``None`` dan tidak ada yang bergerak. Bukan bergerak sedikit: nol.
        * :data:`~aruna.learning.reliability.MIN_MULTIPLIER` dan ``MAX_`` -
          0,7 sampai 1,2, jadi satu jendela buruk tidak bisa membungkam sebuah
          agent. Agent yang tidak bisa didengar tidak bisa dibuktikan benar
          belakangan.
        * titik netral DIUKUR dari baris yang sama, bukan 0,5 mati - tanpa itu
          agent yang selalu bilang BUY di pasar yang naik mendapat bobot
          tambahan karena mengikuti arus.

        ``None`` tetap berarti "belum terukur", dan judge tetap mencatatnya
        sebagai faktor yang tidak tersedia. Yang hilang hanya satu: ``None``
        tidak lagi berarti "belum ada manusia yang menyetujui".
        """
        return self.reliability_report.multiplier(role)

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
