"""Cross-protest and rebuttal (SPEC 14 rounds 2-4).

SPEC 14 makes protest **mandatory**, not optional: in round 2 every agent must
look for weaknesses in the others. For deterministic agents that means an
objection has to be a *checkable defect*, not a mood - shared evidence, a thin
sample, confidence the evidence cannot support, a stale input, or a substantive
disagreement about direction.

Round 3 lets the objected-to agent answer. It may DEFEND when the objection
does not actually apply, or ACCEPT_CORRECTION when it does - and accepting
costs it confidence, which is the point. An objection nobody can lose to would
be theatre.

Round 4 runs only when disagreement is high, exactly as SPEC 14 specifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.agents.analyst import OpinionPool
from aruna.agents.base import AgentOpinion
from aruna.agents.context import DecisionContext
from aruna.core.enums import AgentRole, CouncilRound, Decision, Stance

#: Confidence above this needs more than one reliable evidence item behind it.
OVERCONFIDENCE_THRESHOLD = 0.6

#: Weighted disagreement above this triggers the SPEC 14 round 4 review.
HIGH_DISAGREEMENT = 0.4

#: What an accepted correction costs the agent that conceded.
CORRECTION_PENALTY = 0.5


@dataclass(frozen=True, slots=True)
class Objection:
    """One agent's challenge to another (SPEC 14 round 2)."""

    accuser: AgentRole
    target: AgentRole
    stance: Stance
    ground: str
    detail: str
    #: Objections on checkable grounds carry more than a bare disagreement.
    severity: float = 0.5

    def to_dict(self) -> dict[str, object]:
        return {
            "accuser": self.accuser.value,
            "target": self.target.value,
            "stance": self.stance.value,
            "ground": self.ground,
            "detail": self.detail,
            "severity": round(self.severity, 3),
        }


@dataclass(frozen=True, slots=True)
class Rebuttal:
    """The target's answer (SPEC 14 round 3)."""

    target: AgentRole
    accuser: AgentRole
    ground: str
    stance: Stance
    detail: str

    @property
    def conceded(self) -> bool:
        return self.stance is Stance.ACCEPT_CORRECTION

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target.value,
            "accuser": self.accuser.value,
            "ground": self.ground,
            "stance": self.stance.value,
            "detail": self.detail,
            "conceded": self.conceded,
        }


@dataclass(frozen=True, slots=True)
class ProtestOutcome:
    objections: tuple[Objection, ...] = field(default_factory=tuple)
    rebuttals: tuple[Rebuttal, ...] = field(default_factory=tuple)
    supports: tuple[Objection, ...] = field(default_factory=tuple)
    disagreement: float = 0.0
    adversarial_review: tuple[str, ...] = field(default_factory=tuple)
    rounds_run: tuple[CouncilRound, ...] = field(default_factory=tuple)

    def against(self, role: AgentRole) -> tuple[Objection, ...]:
        return tuple(o for o in self.objections if o.target is role)

    def conceded_by(self, role: AgentRole) -> tuple[Rebuttal, ...]:
        return tuple(r for r in self.rebuttals if r.target is role and r.conceded)

    def to_dict(self) -> dict[str, object]:
        return {
            "objections": [o.to_dict() for o in self.objections],
            "rebuttals": [r.to_dict() for r in self.rebuttals],
            "supports": [o.to_dict() for o in self.supports],
            "disagreement": round(self.disagreement, 3),
            "adversarial_review": list(self.adversarial_review),
            "rounds_run": [r.value for r in self.rounds_run],
        }


def measure_disagreement(pool: OpinionPool) -> float:
    """0..1. How divided the confident opinions are.

    Weighted by confidence, so two hesitant agents disagreeing matters less
    than two certain ones. Used to decide whether SPEC 14's round 4 is needed.
    """
    participating = pool.participating
    if len(participating) < 2:
        return 0.0

    buy = pool.buy_weight
    sell = pool.sell_weight
    total = buy + sell
    if total <= 0:
        return 0.0
    # 1.0 when the two sides are exactly balanced, 0.0 when unanimous.
    return round(1.0 - abs(buy - sell) / total, 3)


def run_protest(
    context: DecisionContext, pool: OpinionPool
) -> ProtestOutcome:
    """Rounds 2, 3 and - when warranted - 4."""
    participating = pool.participating
    rounds = [CouncilRound.INDEPENDENT]
    if len(participating) < 2:
        return ProtestOutcome(rounds_run=tuple(rounds))

    shared = pool.shared_evidence()
    objections: list[Objection] = []
    supports: list[Objection] = []

    # ---- Round 2: mandatory cross-examination -----------------------------
    #
    # Most grounds are properties of the target, not of the pair: whether an
    # agent leans on shared evidence is the same fact no matter who points it
    # out. Raising it once per accuser produced the same complaint five times
    # and, worse, charged the target five separate corrections for one defect.
    # Only a direction conflict is genuinely per-pair.
    rounds.append(CouncilRound.PROTEST)
    seen: set[tuple[AgentRole, str]] = set()
    for accuser in participating:
        for target in participating:
            if accuser.role is target.role:
                continue
            for objection in _examine(accuser, target, shared, context):
                key = (objection.target, objection.ground)
                if objection.ground != "opposite_direction":
                    if key in seen:
                        continue
                    seen.add(key)
                objections.append(objection)

            support = _corroborate(accuser, target)
            if support is not None:
                supports.append(support)

    # ---- Round 3: rebuttal -------------------------------------------------
    rounds.append(CouncilRound.REBUTTAL)
    by_role = {o.role: o for o in participating}
    rebuttals = [
        _rebut(by_role[objection.target], objection, shared)
        for objection in objections
        if objection.target in by_role
    ]

    # ---- Round 4: adversarial review, only if disagreement is high --------
    disagreement = measure_disagreement(pool)
    review: list[str] = []
    if disagreement >= HIGH_DISAGREEMENT:
        rounds.append(CouncilRound.ADVERSARIAL_REVIEW)
        review = _adversarial_review(pool, objections, rebuttals, disagreement)

    return ProtestOutcome(
        objections=tuple(objections),
        rebuttals=tuple(rebuttals),
        supports=tuple(supports),
        disagreement=disagreement,
        adversarial_review=tuple(review),
        rounds_run=tuple(rounds),
    )


def _examine(
    accuser: AgentOpinion,
    target: AgentOpinion,
    shared: dict[str, int],
    context: DecisionContext,
) -> list[Objection]:
    """Every checkable defect the accuser can raise against the target."""
    found: list[Objection] = []

    # Substantive disagreement about direction.
    if (
        accuser.decision.is_directional
        and target.decision.is_directional
        and accuser.decision is not target.decision
    ):
        found.append(
            Objection(
                accuser=accuser.role,
                target=target.role,
                stance=Stance.COUNTER_PROPOSE,
                ground="opposite_direction",
                detail=(
                    f"{accuser.role.value} membaca market yang sama sebagai "
                    f"{accuser.decision.value}: {accuser.reasoning[0]}"
                ),
                severity=min(1.0, accuser.confidence),
            )
        )

    # SPEC 17: the target is leaning on evidence others already cite.
    overlapping = sorted(target.evidence_keys & set(shared))
    if overlapping:
        found.append(
            Objection(
                accuser=accuser.role,
                target=target.role,
                stance=Stance.OBJECT,
                ground="shared_evidence",
                detail=(
                    f"{target.role.value} bersandar pada {', '.join(overlapping[:3])}, "
                    "yang sudah dihitung di tempat lain - bukan saksi independen"
                ),
                severity=min(1.0, 0.3 * len(overlapping)),
            )
        )

    # Confidence the evidence cannot support.
    if target.confidence >= OVERCONFIDENCE_THRESHOLD and target.reliable_evidence <= 1:
        found.append(
            Objection(
                accuser=accuser.role,
                target=target.role,
                stance=Stance.OBJECT,
                ground="overconfident",
                detail=(
                    f"confidence {target.confidence * 100:.0f}% hanya bertumpu pada "
                    f"{target.reliable_evidence} bukti yang andal"
                ),
                severity=0.7,
            )
        )

    # A thin sample behind a directional call.
    thin = [ref for ref in target.evidence if not ref.reliable]
    if thin and target.decision.is_directional:
        found.append(
            Objection(
                accuser=accuser.role,
                target=target.role,
                stance=Stance.OBJECT,
                ground="thin_sample",
                detail=(
                    f"{len(thin)} dari {len(target.evidence)} item yang dikutip tidak "
                    "memenuhi syarat sample-nya sendiri"
                ),
                severity=0.5,
            )
        )

    # A delayed feed undermines any entry reference (SPEC 3, 5).
    if not context.state.is_realtime and target.decision.is_directional:
        found.append(
            Objection(
                accuser=accuser.role,
                target=target.role,
                stance=Stance.OBJECT,
                ground="stale_feed",
                detail=(
                    f"price feed tertunda ~{context.state.declared_delay_sec // 60}m; "
                    "keputusan arah yang berdiri di atasnya sudah kedaluwarsa"
                ),
                severity=0.6,
            )
        )

    return found


def _corroborate(accuser: AgentOpinion, target: AgentOpinion) -> Objection | None:
    """Recorded support, but only when it is independent (SPEC 17).

    Agreement built on the same evidence is not corroboration - it is the same
    observation counted twice, and recording it as support would inflate the
    apparent consensus.
    """
    if not accuser.decision.is_directional or accuser.decision is not target.decision:
        return None
    if accuser.evidence_keys & target.evidence_keys:
        return None
    return Objection(
        accuser=accuser.role,
        target=target.role,
        stance=Stance.SUPPORT,
        ground="independent_agreement",
        detail=(
            f"{accuser.role.value} sampai ke {accuser.decision.value} dari "
            "bukti yang berbeda"
        ),
        severity=min(1.0, accuser.confidence),
    )


def _rebut(
    target: AgentOpinion, objection: Objection, shared: dict[str, int]
) -> Rebuttal:
    """The target answers. It concedes only where the ground actually holds."""
    if objection.ground == "shared_evidence":
        overlapping = target.evidence_keys & set(shared)
        if overlapping:
            return Rebuttal(
                target=target.role,
                accuser=objection.accuser,
                ground=objection.ground,
                stance=Stance.ACCEPT_CORRECTION,
                detail=(
                    "mengakui: pandangan ini tidak independen dari "
                    f"{', '.join(sorted(overlapping)[:2])}"
                ),
            )

    if objection.ground == "overconfident" and target.reliable_evidence <= 1:
        return Rebuttal(
                target=target.role,
            accuser=objection.accuser,
            ground=objection.ground,
            stance=Stance.ACCEPT_CORRECTION,
            detail="mengakui: confidence melebihi apa yang didukung bukti",
        )

    if objection.ground == "thin_sample" and any(
        not ref.reliable for ref in target.evidence
    ):
        return Rebuttal(
                target=target.role,
            accuser=objection.accuser,
            ground=objection.ground,
            stance=Stance.ACCEPT_CORRECTION,
            detail="mengakui: sebagian bukti yang dikutip kurang sample",
        )

    if objection.ground == "stale_feed":
        return Rebuttal(
            target=target.role,
            accuser=objection.accuser,
            ground=objection.ground,
            stance=Stance.ACCEPT_CORRECTION,
            detail="mengakui: keterlambatan feed berlaku juga untuk pandangan ini",
        )

    if objection.ground == "opposite_direction":
        # A disagreement is not a defect. The target defends and lets the judge
        # weigh both readings.
        return Rebuttal(
            target=target.role,
            accuser=objection.accuser,
            ground=objection.ground,
            stance=Stance.COUNTER_PROPOSE,
            detail=(
                f"tetap pada {target.decision.value} dengan buktinya sendiri: "
                f"{target.reasoning[0]}"
            ),
        )

    return Rebuttal(
        target=target.role,
        accuser=objection.accuser,
        ground=objection.ground,
        stance=Stance.SUPPORT,
        detail="objection ini tidak berlaku untuk pandangan ini",
    )


def _adversarial_review(
    pool: OpinionPool,
    objections: list[Objection],
    rebuttals: list[Rebuttal],
    disagreement: float,
) -> list[str]:
    """SPEC 14 round 4, run only when the council is genuinely split."""
    notes = [
        f"disagreement {disagreement:.2f} melewati ambang {HIGH_DISAGREEMENT:.2f} "
        "- adversarial review tambahan dijalankan"
    ]

    conceded = [r for r in rebuttals if r.conceded]
    if conceded:
        notes.append(
            f"{len(conceded)} koreksi diterima: "
            + ", ".join(sorted({f"{r.target.value}/{r.ground}" for r in conceded})[:4])
        )

    unresolved = [o for o in objections if o.ground == "opposite_direction"]
    if unresolved:
        notes.append(
            f"{len(unresolved)} konflik arah belum selesai - "
            "judge harus menimbang bukti, bukan menghitung suara"
        )

    independence = pool.independence()
    if independence < 0.5:
        notes.append(
            f"independensi bukti {independence:.2f}: sebagian besar disagreement "
            "bertumpu pada observasi yang sama"
        )

    for decision in (Decision.BUY, Decision.SELL):
        side = pool.supporters(decision)
        if side:
            best = max(side, key=lambda o: o.confidence)
            notes.append(
                f"argumen {decision.value} terkuat - {best.role.value} di "
                f"{best.confidence * 100:.0f}%: {best.reasoning[0]}"
            )
    return notes


__all__ = [
    "CORRECTION_PENALTY",
    "HIGH_DISAGREEMENT",
    "OVERCONFIDENCE_THRESHOLD",
    "Objection",
    "ProtestOutcome",
    "Rebuttal",
    "measure_disagreement",
    "run_protest",
]
