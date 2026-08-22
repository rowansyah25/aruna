"""Council session: the full SPEC 14 sequence.

    round 1  independent opinions          (PHASE 5)
    round 2  mandatory cross-protest       (SPEC 14)
    round 3  rebuttal / defence            (SPEC 14)
    round 4  adversarial review, if split  (SPEC 14)
    veto     raise, then review            (SPEC 18, 19)
    judge    evidence-weighted verdict     (SPEC 16)
    gate     no-trade engine               (SPEC 33)

Order matters. The judge runs on opinions that have already survived
cross-examination, and the no-trade engine runs *after* the verdict so that a
well-argued decision on untrustworthy data is still refused.

A council verdict is still **not a signal**. Locking is a separate act,
performed by ``aruna.signals``: it attaches an entry, a target and an immutable
snapshot, and it may decline - stale evidence or confidence below the floor.
``CouncilVerdict`` carries ``is_locked_signal=False`` so a conclusion cannot be
mistaken for a published prediction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aruna.agents.analyst import ArunaAnalyst, OpinionPool, Prosecutor, SelfCritic
from aruna.agents.base import Agent, AgentOpinion
from aruna.agents.context import DecisionContext
from aruna.agents.deliberation import default_roster
from aruna.agents.notrade import NoTradeVerdict, evaluate_no_trade
from aruna.agents.risk import RiskAssessment, assess_risk
from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import CouncilRound, Decision
from aruna.core.logging import get_logger
from aruna.council.judge import HistoryFactors, JudgeDecision, judge
from aruna.council.protest import ProtestOutcome, run_protest
from aruna.council.veto import VetoOutcome, run_veto_stage

log = get_logger("aruna.council")


@dataclass(frozen=True, slots=True)
class CouncilVerdict:
    """The complete record of one council session."""

    symbol: str
    market: str
    interval: str
    as_of: datetime
    decided_at: datetime

    opinions: tuple[AgentOpinion, ...]
    proposal: AgentOpinion
    prosecutor: AgentOpinion
    protest: ProtestOutcome
    veto: VetoOutcome
    judgement: JudgeDecision
    risk: RiskAssessment
    no_trade: NoTradeVerdict

    decision: Decision
    confidence: float
    rounds_run: tuple[CouncilRound, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)

    #: A verdict is not a locked prediction: see ``aruna.signals.lock``.
    is_locked_signal: bool = False

    @property
    def participating(self) -> int:
        return sum(1 for o in self.opinions if not o.abstained)

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "market": self.market,
            "interval": self.interval,
            "as_of": isoformat(self.as_of),
            "decided_at": isoformat(self.decided_at),
            "decision": self.decision.value,
            "confidence": round(self.confidence, 3),
            "participating_agents": self.participating,
            "rounds_run": [r.value for r in self.rounds_run],
            "is_locked_signal": False,
            "phase_note": (
                "putusan council, bukan signal terkunci - lock SPEC 20, entry "
                "dan target dipasang terpisah oleh aruna.signals"
            ),
            "opinions": [o.to_dict() for o in self.opinions],
            "proposal": self.proposal.to_dict(),
            "prosecutor": self.prosecutor.to_dict(),
            "protest": self.protest.to_dict(),
            "veto": self.veto.to_dict(),
            "judgement": self.judgement.to_dict(),
            "risk": self.risk.to_dict(),
            "no_trade": self.no_trade.to_dict(),
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        parts = [
            f"{self.symbol} {self.interval}",
            self.decision.value,
            f"conf={self.confidence:.2f}",
            f"agents={self.participating}/{len(self.opinions)}",
            f"ronde={len(self.rounds_run)}",
            f"obj={len(self.protest.objections)}",
            f"risk={self.risk.overall.value}",
        ]
        if self.judgement.minority_prevailed:
            parts.append("MINORITAS-MENANG")
        if self.veto.blocks:
            parts.append(f"VETO[{self.veto.summary()}]")
        elif self.veto.vetoes:
            parts.append(f"veto-ditolak({len(self.veto.rejected)})")
        if self.no_trade.blocked:
            parts.append(f"diblokir[{self.no_trade.summary()}]")
        return "  ".join(parts)


class Council:
    """Runs the full SPEC 14 sequence for one decision."""

    def __init__(
        self,
        roster: list[Agent] | None = None,
        *,
        history: HistoryFactors | None = None,
    ) -> None:
        self._roster = roster if roster is not None else default_roster()
        # PHASE 8's measured SPEC 16 factors. A council convened without them -
        # or with a provider that has too few outcomes - keeps them neutral and
        # says so on every stored decision.
        self._history = history

    def convene(self, context: DecisionContext) -> CouncilVerdict:
        # ---- Round 1: independent opinions --------------------------------
        opinions = tuple(agent.safe_evaluate(context) for agent in self._roster)
        pool = OpinionPool(opinions=opinions)
        proposal = ArunaAnalyst(pool).safe_evaluate(context)
        prosecutor = Prosecutor(pool, proposal).safe_evaluate(context)
        critique = SelfCritic().critique(context, pool, proposal)

        # ---- Rounds 2-4: protest, rebuttal, adversarial review ------------
        protest = run_protest(context, pool)

        # ---- Veto, then review (SPEC 18, 19) ------------------------------
        risk = assess_risk(context)
        veto = run_veto_stage(context, risk)

        # ---- Judge (SPEC 16) ----------------------------------------------
        judgement = judge(context, pool, protest, risk, self._history)

        decision = judgement.decision
        confidence = judgement.confidence
        notes: list[str] = []

        if critique.reassessment_required:
            notes.append(
                f"SELF_CRITIC: bukti tandingan {critique.counter_weight:.2f} lawan "
                f"proposal {critique.proposal_weight:.2f} - judge menimbang keduanya"
            )

        if prosecutor.decision.is_directional and prosecutor.decision is not decision:
            notes.append(
                f"PROSECUTOR menyimpulkan {prosecutor.decision.value} secara "
                "independen; judge menimbang buktinya, bukan perbedaan pendapatnya"
            )

        # An upheld veto overrides the verdict outright (SPEC 19).
        if veto.blocks:
            notes.append(f"veto dikuatkan setelah ditinjau: {veto.summary()}")
            decision = veto.decision
            confidence = 0.0
        elif veto.rejected:
            notes.append(
                f"{len(veto.rejected)} veto diajukan tapi ditolak setelah "
                "ditinjau - kondisi yang disebutkan tidak terbukti ada"
            )

        # ---- No-trade gate (SPEC 33) --------------------------------------
        no_trade = evaluate_no_trade(
            context,
            risk=risk,
            council_confidence=confidence if decision.is_directional else None,
            participating_agents=len(pool.participating),
        )
        if no_trade.blocked:
            notes.append(f"no-trade engine memblokir: {no_trade.summary()}")
            decision = no_trade.decision
            confidence = 0.0

        # ---- Finalizer (bagian 25) ----------------------------------------
        #
        # Diimpor di sini, bukan di puncak berkas: `aruna.decision` menarik
        # `aruna.signals`, yang menarik `aruna.signals.lock`, yang menarik
        # `CouncilVerdict` dari modul ini - impor melingkar yang hanya muncul
        # saat berkas ini dimuat lebih dulu.
        #
        # Batas antara analisis internal dan keputusan yang tersimpan. Di atas
        # garis ini `WAIT` sah - ia suara agent dan hasil judge, dan bagian 25
        # mengizinkan uncertainty internal. Di bawahnya hanya ada tiga.
        #
        # Yang TIDAK berubah: apa yang operator lihat. `PUBLIC_DECISION` sudah
        # memetakan `WAIT` ke "NO SIGNAL" sejak lama. Yang berubah adalah
        # catatan tersimpan berhenti membelah "diam" menjadi dua ember yang
        # artinya sama bagi pembacanya - 3.871 dari 6.441 sesi, terukur.
        from aruna.decision.finalizer import finalkan

        final = finalkan(
            decision,
            diblokir_veto=veto.blocks,
            diblokir_no_trade=no_trade.blocked,
        )
        if final.keputusan is not decision:
            notes.append(f"keputusan final: NO SIGNAL ({final.sebab})")
        decision = final.keputusan

        verdict = CouncilVerdict(
            symbol=context.symbol,
            market=context.market.value,
            interval=context.interval.value,
            as_of=context.as_of,
            decided_at=now_utc(),
            opinions=opinions,
            proposal=proposal,
            prosecutor=prosecutor,
            protest=protest,
            veto=veto,
            judgement=judgement,
            risk=risk,
            no_trade=no_trade,
            decision=decision,
            confidence=confidence,
            rounds_run=protest.rounds_run,
            notes=tuple(notes),
        )
        log.info(
            "council.decided",
            symbol=context.symbol,
            interval=context.interval.value,
            decision=decision.value,
            confidence=round(confidence, 3),
            rounds=len(protest.rounds_run),
            objections=len(protest.objections),
            minority=judgement.minority_prevailed,
            veto_upheld=veto.blocks,
        )
        return verdict


__all__ = ["Council", "CouncilVerdict"]
