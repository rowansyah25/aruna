"""The evidence-weighted judge (SPEC 16).

**Never a simple majority.** SPEC 16 is explicit that a minority may win and a
majority may lose, so the judge scores each opinion on the quality of what
stands behind it and then compares totals - it never counts heads.

SPEC 16 lists ten things to weigh. Seven are available now:

===========================  ==========================================
evidence quality             share of cited items that met their sample
freshness                    feed delay and data-quality flags
sample size                  bars behind each reading
independence                 how much evidence is uniquely this agent's
feature redundancy           penalty for evidence others already cite
regime                       an unreadable regime discounts everything
risk                         elevated risk discounts directional views
horizon                      opinions off the decision horizon count less
===========================  ==========================================

Two more depend on realised outcomes, and are supplied - when they can be
measured - through :class:`HistoryFactors`:

* **historical reliability** - each agent's record on the calls it backed
  (SPEC 30);
* **confidence calibration** - stated confidence against actual accuracy
  (SPEC 29).

Both answer ``None`` until their sample thresholds are met, and ``None`` means a
neutral 1.0 multiplier *and* the factor listed as unavailable on the stored
decision. A judge that quietly invented values for them would look
better-grounded than it is, and would corrupt the very calibration that is
supposed to check it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from aruna.agents.analyst import OpinionPool
from aruna.agents.base import AgentOpinion
from aruna.agents.context import DecisionContext
from aruna.agents.risk import RiskAssessment, RiskLevel
from aruna.core.enums import AgentRole, Decision, Regime
from aruna.council.protest import CORRECTION_PENALTY, ProtestOutcome

#: Minimum weighted margin before the judge will name a direction.
DECISION_MARGIN = 0.15

#: Surviving weight at which the council may be fully confident. Weights are
#: heavily discounted by evidence quality, independence and risk, so reaching
#: this means several agents cleared cross-examination on distinct evidence.
FULL_CONVICTION_WEIGHT = 1.5

#: The council is never certain either. A unanimous roster still reads one
#: market through one set of indicators, and SPEC 29 will score stated
#: confidence against realised accuracy - a published 100% can only ever be
#: measured as overconfident. Agents are capped at the same figure
#: (``MAX_AGENT_CONFIDENCE``); the council weighing more evidence earns a wider
#: range below the cap, not the right to exceed it.
MAX_COUNCIL_CONFIDENCE = 0.95

#: The two SPEC 16 factors that need realised outcomes. PHASE 8 can supply them
#: through :class:`HistoryFactors`; until enough predictions have resolved, both
#: stay neutral and are reported as unavailable on every stored decision.
UNAVAILABLE_FACTORS: tuple[str, ...] = (
    "historical_reliability",
    "confidence_calibration",
)


class HistoryFactors(Protocol):
    """Source of the two factors that can only come from past outcomes.

    Both methods return ``None`` when the sample is too small to say anything.
    That is not a failure mode - it is the expected answer for most of a
    system's life, and it must stay distinguishable from "measured, and the
    answer happens to be 1.0".
    """

    def reliability(self, role: AgentRole) -> float | None:
        """Weight multiplier for an agent's track record (SPEC 30)."""
        ...

    def calibration(self, confidence: float) -> float | None:
        """Correction for a stated confidence level (SPEC 29)."""
        ...


def _unavailable_factors(history: HistoryFactors | None) -> tuple[str, ...]:
    """Which SPEC 16 factors could not be measured for this decision.

    Asked of the provider rather than assumed, because the answer changes as
    outcomes accumulate - and a decision record that claimed a factor was
    applied when it was not would corrupt every later audit.
    """
    if history is None:
        return UNAVAILABLE_FACTORS
    missing: list[str] = []
    if not any(history.reliability(role) is not None for role in AgentRole):
        missing.append("historical_reliability")
    # Probed at the middle of each bucket: if no bucket is measurable, no
    # decision can carry a calibration factor.
    if not any(
        history.calibration((low + high) / 2) is not None
        for low, high in ((0.35, 0.5), (0.5, 0.65), (0.65, 0.8), (0.8, 0.96))
    ):
        missing.append("confidence_calibration")
    return tuple(missing)


@dataclass(frozen=True, slots=True)
class AgentWeight:
    """Why one opinion counted for as much as it did."""

    role: AgentRole
    decision: Decision
    raw_confidence: float
    weight: float
    evidence_quality: float
    independence: float
    redundancy_penalty: float
    correction_penalty: float
    horizon_penalty: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "decision": self.decision.value,
            "raw_confidence": round(self.raw_confidence, 3),
            "weight": round(self.weight, 4),
            "evidence_quality": round(self.evidence_quality, 3),
            "independence": round(self.independence, 3),
            "redundancy_penalty": round(self.redundancy_penalty, 3),
            "correction_penalty": round(self.correction_penalty, 3),
            "horizon_penalty": round(self.horizon_penalty, 3),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class JudgeDecision:
    decision: Decision
    confidence: float
    weights: tuple[AgentWeight, ...] = field(default_factory=tuple)
    buy_weight: float = 0.0
    sell_weight: float = 0.0
    reasoning: tuple[str, ...] = field(default_factory=tuple)
    #: True when the winning side had fewer agents than the losing one.
    minority_prevailed: bool = False
    unavailable_factors: tuple[str, ...] = UNAVAILABLE_FACTORS

    def to_dict(self) -> dict[str, object]:
        missing = list(self.unavailable_factors)
        applied = [f for f in UNAVAILABLE_FACTORS if f not in missing]
        if missing:
            note = (
                "ditimbang dari bukti, tidak pernah dari jumlah kepala "
                f"(SPEC 16); {', '.join(missing)} belum punya cukup outcome "
                "yang sudah selesai untuk bisa diukur, jadi diterapkan netral"
            )
        else:
            note = (
                "ditimbang dari bukti, tidak pernah dari jumlah kepala "
                "(SPEC 16); semua faktor SPEC 16 terukur dan diterapkan"
            )
        return {
            "decision": self.decision.value,
            "confidence": round(self.confidence, 3),
            "buy_weight": round(self.buy_weight, 4),
            "sell_weight": round(self.sell_weight, 4),
            "minority_prevailed": self.minority_prevailed,
            "reasoning": list(self.reasoning),
            "weights": [w.to_dict() for w in self.weights],
            "unavailable_factors": missing,
            "applied_factors": applied,
            "note": note,
        }


def judge(
    context: DecisionContext,
    pool: OpinionPool,
    protest: ProtestOutcome,
    risk: RiskAssessment,
    history: HistoryFactors | None = None,
) -> JudgeDecision:
    """Weigh the surviving opinions and reach a verdict.

    ``history`` supplies the two SPEC 16 factors that need realised outcomes.
    Passing ``None`` - or a provider that has not yet seen enough resolved
    predictions - leaves both neutral and keeps them listed as unavailable.
    """
    participating = pool.participating
    if not participating:
        return JudgeDecision(
            decision=Decision.WAIT,
            confidence=0.0,
            reasoning=("tidak ada agent yang membentuk pendapat",),
        )

    shared = pool.shared_evidence()
    weights = [
        _weigh(opinion, context, protest, risk, shared, history)
        for opinion in participating
    ]

    buy = sum(w.weight for w in weights if w.decision is Decision.BUY)
    sell = sum(w.weight for w in weights if w.decision is Decision.SELL)
    total = buy + sell

    buy_count = sum(1 for w in weights if w.decision is Decision.BUY)
    sell_count = sum(1 for w in weights if w.decision is Decision.SELL)

    reasoning: list[str] = [
        f"bobot BUY {buy:.3f} ({buy_count} agent) vs SELL {sell:.3f} ({sell_count} agent)",
        "ditimbang dari kualitas bukti dan independensi, bukan dari jumlah kepala (SPEC 16)",
    ]

    if protest.adversarial_review:
        reasoning.append(
            f"adversarial review berjalan pada disagreement {protest.disagreement:.2f}"
        )
    conceded = [r for r in protest.rebuttals if r.conceded]
    if conceded:
        reasoning.append(f"{len(conceded)} koreksi yang diterima memangkas bobot")

    unavailable = _unavailable_factors(history)
    for name in unavailable:
        reasoning.append(
            f"{name}: outcome yang sudah selesai belum cukup untuk diukur - diterapkan netral"
        )
    for name in (f for f in UNAVAILABLE_FACTORS if f not in unavailable):
        reasoning.append(f"{name}: terukur dan diterapkan (SPEC 16)")

    if total <= 0:
        return JudgeDecision(
            decision=Decision.WAIT,
            confidence=0.0,
            weights=tuple(weights),
            reasoning=(*reasoning, "tidak ada pendapat berarah yang bertahan setelah ditimbang"),
            unavailable_factors=unavailable,
        )

    margin = abs(buy - sell) / total
    if margin < DECISION_MARGIN:
        return JudgeDecision(
            decision=Decision.WAIT,
            confidence=0.0,
            weights=tuple(weights),
            buy_weight=buy,
            sell_weight=sell,
            reasoning=(
                *reasoning,
                f"margin berbobot {margin:.3f} ada di bawah lantai "
                f"{DECISION_MARGIN:.2f} - terlalu tipis untuk diputuskan",
            ),
            unavailable_factors=unavailable,
        )

    decision = Decision.BUY if buy > sell else Decision.SELL
    winning = max(buy, sell)
    winning_count = buy_count if decision is Decision.BUY else sell_count
    losing_count = sell_count if decision is Decision.BUY else buy_count
    minority = winning_count < losing_count

    if minority:
        reasoning.append(
            f"minoritas menang: {winning_count} agent mengungguli bobot "
            f"{losing_count} agent atas dasar kualitas bukti (SPEC 16)"
        )

    # Confidence combines three things. *Margin* is how one-sided the weighted
    # vote was; *conviction* is how much surviving weight was actually behind
    # it; and unanswered objections damp the result. Margin alone reaches 1.0
    # whenever only one side has any weight at all - which reported total
    # certainty from two agents carrying 0.288 between them.
    conviction = min(1.0, winning / FULL_CONVICTION_WEIGHT)
    unresolved = _unresolved_severity(protest, decision, weights)
    confidence = round(
        min(
            MAX_COUNCIL_CONFIDENCE,
            margin * conviction * (1.0 - min(0.5, unresolved)),
        ),
        3,
    )
    reasoning.append(
        f"conviction {conviction:.2f} dari {winning:.3f} bobot yang bertahan"
    )
    if unresolved > 0:
        reasoning.append(
            f"objection yang tidak terjawab terhadap sisi yang menang memangkas "
            f"confidence sebesar {min(0.5, unresolved) * 100:.0f}%"
        )

    return JudgeDecision(
        decision=decision,
        confidence=confidence,
        weights=tuple(weights),
        buy_weight=buy,
        sell_weight=sell,
        reasoning=tuple(reasoning),
        minority_prevailed=minority,
        unavailable_factors=unavailable,
    )


def _weigh(
    opinion: AgentOpinion,
    context: DecisionContext,
    protest: ProtestOutcome,
    risk: RiskAssessment,
    shared: dict[str, int],
    history: HistoryFactors | None = None,
) -> AgentWeight:
    notes: list[str] = []

    # ---- evidence quality: did the cited items meet their own sample need?
    cited = len(opinion.evidence)
    quality = (opinion.reliable_evidence / cited) if cited else 0.0
    if cited == 0:
        notes.append("tidak ada bukti yang dikutip")

    # ---- independence and redundancy (SPEC 17) --------------------------
    overlapping = opinion.evidence_keys & set(shared)
    independence = (
        1.0 - len(overlapping) / len(opinion.evidence_keys)
        if opinion.evidence_keys
        else 0.0
    )
    redundancy = min(0.5, 0.2 * len(overlapping))
    if overlapping:
        notes.append(f"{len(overlapping)} butir bukti dipakai bersama agent lain")

    # ---- accepted corrections (SPEC 14 round 3) --------------------------
    conceded = protest.conceded_by(opinion.role)
    correction = min(0.8, CORRECTION_PENALTY * len(conceded))
    if conceded:
        notes.append(
            f"menerima {len(conceded)} koreksi: "
            + ", ".join(sorted({r.ground for r in conceded}))
        )

    # ---- horizon: an opinion about a different horizon counts less -------
    horizon_penalty = 0.0
    if opinion.horizon is not None and opinion.horizon is not context.interval:
        horizon_penalty = 0.5
        notes.append(f"pendapat ini soal {opinion.horizon.value}, bukan horizon keputusan")
    elif opinion.horizon is None and opinion.decision.is_directional:
        horizon_penalty = 0.3
        notes.append("pendapat ini tidak menyebut horizon")

    # ---- freshness (SPEC 5) ----------------------------------------------
    freshness = 1.0
    if not context.state.is_realtime:
        freshness = 0.6
        notes.append("price feed tertunda")
    if context.state.data_quality != "OK":
        freshness *= 0.5
        notes.append(f"kualitas data {context.state.data_quality}")

    # ---- regime and risk --------------------------------------------------
    regime_factor = 1.0
    verdict = context.regime
    if verdict is not None:
        if verdict.regime in (Regime.UNCERTAIN, Regime.ANOMALY):
            regime_factor = 0.4
            notes.append(f"regime {verdict.regime.value} memangkas semua pandangan berarah")
        else:
            # A confidently-read regime lends weight; a hesitant one lends less.
            regime_factor = 0.6 + 0.4 * verdict.confidence

    risk_factor = 1.0
    if opinion.decision.is_directional:
        if risk.overall is RiskLevel.EXTREME:
            risk_factor = 0.2
            notes.append("risiko EXTREME memangkas pandangan berarah habis-habisan")
        elif risk.overall is RiskLevel.HIGH:
            risk_factor = 0.6
            notes.append("risiko HIGH memangkas pandangan berarah")

    # ---- SPEC 16 factors that need history (SPEC 29, 30) ------------------
    # Neutral 1.0 until PHASE 8 has enough resolved outcomes to say otherwise.
    # `None` from either provider means "not measured", which is different from
    # "measured and average" - and the difference is reported, not hidden.
    reliability_factor = 1.0
    calibration_factor = 1.0
    if history is not None:
        measured = history.reliability(opinion.role)
        if measured is not None:
            reliability_factor = measured
            notes.append(f"reliabilitas historis {measured:.2f} (SPEC 30)")
        adjusted = history.calibration(opinion.confidence)
        if adjusted is not None:
            calibration_factor = adjusted
            notes.append(f"kalibrasi confidence {adjusted:.2f} (SPEC 29)")

    weight = (
        opinion.confidence
        * quality
        * max(0.1, independence)
        * (1.0 - redundancy)
        * (1.0 - correction)
        * (1.0 - horizon_penalty)
        * freshness
        * regime_factor
        * risk_factor
        * reliability_factor
        * calibration_factor
    )

    return AgentWeight(
        role=opinion.role,
        decision=opinion.decision,
        raw_confidence=opinion.confidence,
        weight=round(max(0.0, weight), 4),
        evidence_quality=quality,
        independence=independence,
        redundancy_penalty=redundancy,
        correction_penalty=correction,
        horizon_penalty=horizon_penalty,
        notes=tuple(notes),
    )


def _unresolved_severity(
    protest: ProtestOutcome, decision: Decision, weights: list[AgentWeight]
) -> float:
    """Severity of objections against the winning side that were not conceded."""
    winners = {w.role for w in weights if w.decision is decision}
    conceded = {(r.target, r.ground) for r in protest.rebuttals if r.conceded}
    unresolved = [
        objection
        for objection in protest.objections
        if objection.target in winners
        and objection.ground != "opposite_direction"
        and (objection.target, objection.ground) not in conceded
    ]
    if not unresolved:
        return 0.0
    return min(1.0, sum(o.severity for o in unresolved) / max(len(winners), 1) / 3)


__all__ = [
    "DECISION_MARGIN",
    "FULL_CONVICTION_WEIGHT",
    "MAX_COUNCIL_CONFIDENCE",
    "UNAVAILABLE_FACTORS",
    "AgentWeight",
    "JudgeDecision",
    "judge",
]
