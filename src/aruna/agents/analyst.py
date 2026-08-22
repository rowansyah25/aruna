"""ARUNA ANALYST, PROSECUTOR (SPEC 13) and SELF-CRITIC (SPEC 15).

These three are different in kind from the market agents: they read the *other
agents' opinions*, not the raw evidence.

The prosecutor is the one most easily got wrong. SPEC 13 requires it to hunt
for weaknesses in the proposal **and** to reach an independent conclusion - the
worked examples show it agreeing (BUY 76% against ARUNA's BUY 82%), disagreeing
(SELL 91%), and standing aside (WAIT 84%), all valid. So it is not an
opposition role. It attacks the argument, then says what *it* thinks, and those
are allowed to be the same direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.agents.base import Agent, AgentOpinion, EvidenceRef, abstain
from aruna.agents.context import DecisionContext
from aruna.core.enums import AgentRole, Decision

#: Evidence keys shared by this many agents stop counting as independent
#: witnesses (SPEC 17).
SHARED_EVIDENCE_THRESHOLD = 2


@dataclass(frozen=True, slots=True)
class OpinionPool:
    """The round-one opinions, with the arithmetic the later roles need."""

    opinions: tuple[AgentOpinion, ...] = field(default_factory=tuple)

    @property
    def participating(self) -> tuple[AgentOpinion, ...]:
        return tuple(o for o in self.opinions if not o.abstained)

    def weight_for(self, decision: Decision) -> float:
        return sum(
            o.confidence for o in self.participating if o.decision is decision
        )

    @property
    def buy_weight(self) -> float:
        return self.weight_for(Decision.BUY)

    @property
    def sell_weight(self) -> float:
        return self.weight_for(Decision.SELL)

    def supporters(self, decision: Decision) -> tuple[AgentOpinion, ...]:
        return tuple(o for o in self.participating if o.decision is decision)

    def shared_evidence(self) -> dict[str, int]:
        """How many agents cited each evidence key (SPEC 17)."""
        counts: dict[str, int] = {}
        for opinion in self.participating:
            for key in opinion.evidence_keys:
                counts[key] = counts.get(key, 0) + 1
        return {k: v for k, v in counts.items() if v >= SHARED_EVIDENCE_THRESHOLD}

    def independence(self) -> float:
        """0..1. How much of the cited evidence was distinct.

        Six agents all reading RSI is one witness repeated six times, and
        SPEC 17 says the judge must not count that as six.
        """
        participating = self.participating
        if not participating:
            return 0.0
        distinct = {key for o in participating for key in o.evidence_keys}
        total = sum(len(o.evidence_keys) for o in participating)
        return round(len(distinct) / total, 3) if total else 0.0


class ArunaAnalyst(Agent):
    """Synthesises the roster into a proposal.

    Weighted by confidence rather than counted by head: SPEC 16 forbids a
    simple majority, and a minority holding strong evidence must be able to
    outweigh a confident-looking crowd.
    """

    role = AgentRole.ARUNA_ANALYST

    def __init__(self, pool: OpinionPool) -> None:
        self._pool = pool

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        pool = self._pool
        participating = pool.participating
        if not participating:
            return abstain(self.role, "tidak ada agent yang membentuk pandangan")

        buy, sell = pool.buy_weight, pool.sell_weight
        total = buy + sell
        reasons: list[str] = []

        for opinion in sorted(participating, key=lambda o: -o.confidence)[:6]:
            reasons.append(
                f"{opinion.role.value} {opinion.decision.value} "
                f"{opinion.confidence * 100:.0f}%"
            )

        independence = pool.independence()
        shared = pool.shared_evidence()
        if shared:
            reasons.append(
                f"{len(shared)} butir bukti dipakai bersama antar agent - "
                "bukan saksi yang independen (SPEC 17)"
            )

        # Agents that participated but declined to take a side are evidence
        # against a confident directional call. Counting only the two
        # directional camps let a single strong vote look unanimous while most
        # of the roster was explicitly unconvinced.
        undecided = sum(1 for o in participating if not o.decision.is_directional)
        directional = len(participating) - undecided

        if total <= 0 or abs(buy - sell) < 0.3:
            decision, confidence = Decision.WAIT, 0.0
            reasons.append("opini terbobot seimbang - tidak ada usulan")
        else:
            decision = Decision.BUY if buy > sell else Decision.SELL
            agreement = abs(buy - sell) / total
            # Share of participating agents that actually took a side.
            commitment = directional / len(participating)
            # Scaled by independence too, so a chorus reading one indicator
            # cannot look like a consensus (SPEC 17).
            confidence = round(agreement * commitment * independence, 3)
            reasons.append(
                f"bobot {buy:.2f} buy vs {sell:.2f} sell; "
                f"{directional} dari {len(participating)} agent memilih arah; "
                f"independensi bukti {independence:.2f}"
            )
            if undecided:
                reasons.append(
                    f"{undecided} agent yang ikut menolak menentukan sikap"
                )

        evidence = tuple(
            EvidenceRef(
                key=f"opinion:{o.role.value}",
                value=o.confidence,
                sample_size=o.reliable_evidence,
                reliable=not o.abstained,
                detail=o.decision.value,
            )
            for o in participating
        )
        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=confidence,
            reasoning=tuple(reasons),
            evidence=evidence,
            horizon=context.interval,
        )


class Prosecutor(Agent):
    """Attacks the proposal, then states its own independent conclusion.

    SPEC 13: it must find the weaknesses *and* reach a conclusion of its own,
    which may agree with the proposal. It is not permanent opposition.
    """

    role = AgentRole.PROSECUTOR

    def __init__(self, pool: OpinionPool, proposal: AgentOpinion) -> None:
        self._pool = pool
        self._proposal = proposal

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        pool = self._pool
        proposal = self._proposal
        participating = pool.participating
        if not participating:
            return abstain(
                self.role,
                "tidak ada yang bisa diperiksa - tidak ada agent yang "
                "membentuk pandangan",
            )

        objections: list[str] = []

        # ---- attack the proposal (SPEC 13) --------------------------------
        independence = pool.independence()
        if independence < 0.5:
            objections.append(
                f"independensi bukti hanya {independence:.2f} - agent yang "
                "sepakat sebagian besar membaca input yang sama"
            )
        shared = pool.shared_evidence()
        for key, count in sorted(shared.items(), key=lambda kv: -kv[1])[:3]:
            objections.append(f"{count} agent sama-sama mengutip {key}")

        if proposal.decision.is_directional:
            dissent = pool.supporters(
                Decision.SELL if proposal.decision is Decision.BUY else Decision.BUY
            )
            for opinion in dissent:
                objections.append(
                    f"{opinion.role.value} sampai pada kesimpulan berlawanan di "
                    f"{opinion.confidence * 100:.0f}%: {opinion.reasoning[0]}"
                )

        thin = [o for o in participating if o.reliable_evidence == 0]
        if thin:
            objections.append(
                f"{len(thin)} agent memilih tanpa bukti andal di belakangnya"
            )

        if not context.state.is_realtime:
            objections.append(
                f"feed harga tertunda ~{context.state.declared_delay_sec // 60}m; "
                "acuan entry apa pun sudah basi"
            )

        # ---- reach an independent conclusion (SPEC 13) --------------------
        # Deliberately not the inverse of the proposal: the prosecutor
        # re-weighs the pool itself, discounting agents whose evidence was
        # thin, and may well land on the same side.
        buy = sum(
            o.confidence for o in pool.supporters(Decision.BUY) if o.reliable_evidence
        )
        sell = sum(
            o.confidence for o in pool.supporters(Decision.SELL) if o.reliable_evidence
        )
        total = buy + sell

        if total <= 0:
            decision, confidence = Decision.WAIT, 0.6
            objections.append(
                "tidak ada agent yang memegang pandangan berarah dengan "
                "dukungan bukti andal"
            )
        elif abs(buy - sell) < 0.3:
            decision, confidence = Decision.WAIT, 0.7
            objections.append(
                "opini yang didukung bukti terlalu ketat untuk diputuskan"
            )
        else:
            decision = Decision.BUY if buy > sell else Decision.SELL
            confidence = round(min(1.0, abs(buy - sell) / total) * independence, 3)

        agreement = (
            "sepakat dengan usulan"
            if decision is proposal.decision
            else f"berbeda dari usulan ({proposal.decision.value})"
        )
        reasoning = (
            f"kesimpulan independen {decision.value}: {agreement}",
            *objections,
        )

        return AgentOpinion(
            role=self.role,
            decision=decision,
            confidence=confidence,
            reasoning=reasoning,
            evidence=(
                EvidenceRef(
                    key="proposal",
                    value=proposal.confidence,
                    sample_size=len(participating),
                    reliable=True,
                    detail=proposal.decision.value,
                ),
            ),
            horizon=context.interval,
        )


@dataclass(frozen=True, slots=True)
class CritiqueResult:
    """SPEC 15: the strongest case that the decision is wrong."""

    opinion: AgentOpinion
    #: True when the counter-evidence outweighs the proposal and SPEC 15
    #: requires ARUNA to reassess.
    reassessment_required: bool
    counter_weight: float
    proposal_weight: float

    def to_dict(self) -> dict[str, object]:
        return {
            "opinion": self.opinion.to_dict(),
            "reassessment_required": self.reassessment_required,
            "counter_weight": round(self.counter_weight, 3),
            "proposal_weight": round(self.proposal_weight, 3),
        }


class SelfCritic:
    """Answers SPEC 15: what is the strongest evidence this is wrong?

    Not a member of the SPEC 12 roster - it examines a proposal rather than
    voting on the market - so it returns a :class:`CritiqueResult` carrying the
    reassessment flag alongside its opinion.
    """

    role = AgentRole.ARUNA_ANALYST

    def critique(
        self,
        context: DecisionContext,
        pool: OpinionPool,
        proposal: AgentOpinion,
    ) -> CritiqueResult:
        counter_points: list[str] = []

        if not proposal.decision.is_directional:
            counter_points.append(
                "usulannya sudah WAIT - pertanyaannya apakah ada peluang "
                "nyata yang justru terlewat"
            )
            missed = max(
                (o for o in pool.participating if o.decision.is_directional),
                key=lambda o: o.confidence,
                default=None,
            )
            if missed is not None:
                counter_points.append(
                    f"{missed.role.value} melihat {missed.decision.value} di "
                    f"{missed.confidence * 100:.0f}%: {missed.reasoning[0]}"
                )
            return CritiqueResult(
                opinion=AgentOpinion(
                    role=self.role,
                    decision=Decision.WAIT,
                    confidence=0.0,
                    reasoning=tuple(counter_points),
                    horizon=context.interval,
                    abstained=True,
                ),
                reassessment_required=False,
                counter_weight=0.0,
                proposal_weight=proposal.confidence,
            )

        opposite = Decision.SELL if proposal.decision is Decision.BUY else Decision.BUY
        against = pool.supporters(opposite)
        counter_weight = sum(o.confidence for o in against)
        proposal_weight = sum(o.confidence for o in pool.supporters(proposal.decision))

        for opinion in sorted(against, key=lambda o: -o.confidence)[:4]:
            counter_points.append(
                f"{opinion.role.value} berargumen {opposite.value} di "
                f"{opinion.confidence * 100:.0f}%: {opinion.reasoning[0]}"
            )

        waiters = pool.supporters(Decision.WAIT)
        for opinion in waiters[:2]:
            counter_points.append(
                f"{opinion.role.value} menolak menentukan sikap: "
                f"{opinion.reasoning[0]}"
            )

        independence = pool.independence()
        if independence < 0.5:
            counter_points.append(
                f"argumen pendukung bertumpu pada bukti yang tumpang tindih "
                f"(independensi {independence:.2f})"
            )

        if not counter_points:
            counter_points.append("tidak ditemukan bukti tandingan yang material")

        # SPEC 15: when the counter-evidence is stronger, ARUNA must reassess.
        reassess = counter_weight > proposal_weight
        if reassess:
            counter_points.insert(
                0,
                f"bukti tandingan ({counter_weight:.2f}) mengalahkan bobot "
                f"usulan ({proposal_weight:.2f}) - wajib dinilai ulang",
            )

        return CritiqueResult(
            opinion=AgentOpinion(
                role=self.role,
                decision=opposite if reassess else Decision.WAIT,
                confidence=round(min(1.0, counter_weight), 3) if reassess else 0.0,
                reasoning=tuple(counter_points),
                evidence=tuple(
                    EvidenceRef(
                        key=f"counter:{o.role.value}",
                        value=o.confidence,
                        sample_size=o.reliable_evidence,
                        detail=o.decision.value,
                    )
                    for o in against
                ),
                horizon=context.interval,
                abstained=not reassess,
            ),
            reassessment_required=reassess,
            counter_weight=counter_weight,
            proposal_weight=proposal_weight,
        )


__all__ = [
    "SHARED_EVIDENCE_THRESHOLD",
    "ArunaAnalyst",
    "CritiqueResult",
    "OpinionPool",
    "Prosecutor",
    "SelfCritic",
]
