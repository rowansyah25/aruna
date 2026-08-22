"""Governance service (PHASE 10).

Derives research questions from what has been measured, keeps proposals, and
carries a human's decision to the record.

The one thing this service will not do is decide. :meth:`approve` takes an actor
and passes it to the approval gate, which refuses anything that is not a named
person. There is no path from a good number to an active change that does not go
through a human being.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from aruna.core.logging import get_logger
from aruna.db.repositories.governance import GovernanceRepository
from aruna.governance.approval import approve, reject, submit_for_approval
from aruna.governance.drift import DriftReport, Window, detect
from aruna.governance.proposal import (
    STATUS_TERMINAL,
    Arm,
    ModelProposal,
    ProposalStatus,
    Validation,
    validate,
)
from aruna.governance.research import (
    ResearchQuestion,
    questions_from_autopsies,
    questions_from_backtest,
    questions_from_calibration,
    questions_from_objections,
    rank,
)
from aruna.learning.autopsy import successful_objections

log = get_logger("aruna.governance")

#: Days of recent behaviour compared against everything before it.
DRIFT_WINDOW_DAYS = 30


@dataclass(slots=True)
class ResearchResult:
    questions: list[ResearchQuestion] = field(default_factory=list)
    drift: DriftReport | None = None
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"questions={len(self.questions)}"]
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


class GovernanceService:
    def __init__(
        self,
        *,
        store: GovernanceRepository,
        learning: Any = None,
        backtest: Any = None,
    ) -> None:
        self._store = store
        self._learning = learning
        self._backtest = backtest

    # ---- research (SPEC 31) ----------------------------------------------

    async def research(self, *, persist: bool = True) -> ResearchResult:
        """Derive questions from the record. Answers none of them."""
        result = ResearchResult()
        questions: list[ResearchQuestion] = []

        if self._learning is not None:
            calibration = await self._learning.latest_calibration()
            if calibration:
                questions += questions_from_calibration(calibration)
            questions += questions_from_autopsies(
                await self._learning.autopsies(limit=200)
            )
            # SPEC 26: an objection repeatedly overruled and repeatedly proved
            # right is the most specific signal the record produces - it names
            # an agent and a ground. Deriving it here rather than leaving it to
            # a caller is the difference between the pattern being found and
            # merely being findable.
            records = successful_objections(
                await self._learning.overruled_objections()
            )
            questions += questions_from_objections([r.to_dict() for r in records])

        if self._backtest is not None:
            for run in await self._backtest.recent_runs(limit=5):
                questions += questions_from_backtest(run)

        result.questions = rank(questions)
        if persist:
            for question in result.questions:
                await self._store.record_question(question)
            # A question the record no longer raises is closed rather than left
            # open forever. Without this the list only ever grows, and a fixed
            # problem keeps being reported as outstanding.
            closed = await self._store.close_absent(
                [q.key for q in result.questions]
            )
            if closed:
                log.info("governance.questions_closed", count=closed)

        log.info("governance.research", questions=len(result.questions))
        return result

    # ---- proposals (SPEC 44) ---------------------------------------------

    async def validate_proposal(
        self,
        proposal: ModelProposal,
        baseline: Arm,
        variant: Arm,
        *,
        out_of_sample: bool = False,
        persist: bool = True,
    ) -> Validation:
        """Compare a variant against the current rules.

        The count of previously tested variants comes from storage, not from the
        caller: a proposal cannot understate how many attempts preceded it.
        """
        tested = await self._store.variants_tested()
        validation = validate(
            baseline,
            variant,
            variants_tested=tested + 1,
            out_of_sample=out_of_sample,
        )
        if persist:
            # Status terminal tidak mundur. Terukur 2026-08-21:
            # `exit-at-target` disetujui 2026-08-15 11:39 oleh rowan, lalu
            # validasi ulang dua hari kemudian menetapkan VALIDATED tanpa
            # syarat dan membatalkan persetujuan itu diam-diam. Tabel keputusan
            # tetap berkata APPROVED - keduanya berbohong satu sama lain, dan
            # penjaga "already approved" kalah, sehingga perubahan yang sama
            # bisa disetujui dua kali.
            #
            # Kode ini sudah menyatakan sikapnya di `approval.reject()`:
            # membalikkan perubahan aktif adalah proposal BARU, bukan
            # pembatalan diam-diam.
            #
            # Validasinya tetap disimpan. Bukti baru justru yang paling
            # berharga - validasi ulang `exit-at-target` yang menemukan
            # NO_IMPROVEMENT dan PnL lebih buruk 463.540.
            sudah_diputuskan = proposal.status in STATUS_TERMINAL
            await self._store.record_proposal(
                replace(
                    proposal,
                    validation=validation,
                    status=(
                        proposal.status
                        if sudah_diputuskan
                        else ProposalStatus.VALIDATED
                    ),
                )
            )
        return validation

    async def submit(self, proposal: ModelProposal) -> ModelProposal:
        submitted = submit_for_approval(proposal)
        await self._store.record_proposal(submitted)
        return submitted

    async def approve(
        self, proposal: ModelProposal, *, actor: str, note: str = ""
    ) -> ModelProposal:
        """Record a human's approval. Raises unless ``actor`` is a person."""
        decided = approve(proposal, actor=actor, note=note)
        await self._store.record_proposal(decided)
        await self._store.record_decision(decided)
        return decided

    async def reject(
        self, proposal: ModelProposal, *, actor: str, note: str = ""
    ) -> ModelProposal:
        decided = reject(proposal, actor=actor, note=note)
        await self._store.record_proposal(decided)
        await self._store.record_decision(decided)
        return decided

    # ---- drift ------------------------------------------------------------

    async def check_drift(self, *, persist: bool = True) -> DriftReport:
        """Has the world moved away from what the rules were validated on?"""
        if self._learning is None:
            return detect(Window(label="baseline"), Window(label="recent"))

        baseline, recent = await self._learning.drift_windows(
            days=DRIFT_WINDOW_DAYS
        )
        report = detect(
            Window(
                label="before",
                resolved=baseline.get("resolved", 0),
                correct=baseline.get("correct", 0),
                regimes=baseline.get("regimes", {}),
            ),
            Window(
                label=f"last {DRIFT_WINDOW_DAYS} days",
                resolved=recent.get("resolved", 0),
                correct=recent.get("correct", 0),
                regimes=recent.get("regimes", {}),
            ),
        )
        if persist:
            await self._store.record_drift(report)
        return report


__all__ = ["DRIFT_WINDOW_DAYS", "GovernanceService", "ResearchResult"]
