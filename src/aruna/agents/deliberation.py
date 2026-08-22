"""Round one: every agent forms an independent view (SPEC 14 round 1).

This is deliberately **not** the council. SPEC 14 requires four rounds -
independent opinions, mandatory cross-protest, rebuttal, and adversarial review
when disagreement is high - plus a veto path (SPEC 18, 19) and an
evidence-weighted judge (SPEC 16). Those are PHASE 6.

What PHASE 5 delivers is round one plus the prosecutor, the self-critic, the
risk engine and the no-trade engine. :class:`Deliberation` carries
``is_council_decision=False`` so nothing downstream can mistake this for a
final verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aruna.agents.analyst import (
    ArunaAnalyst,
    CritiqueResult,
    OpinionPool,
    Prosecutor,
    SelfCritic,
)
from aruna.agents.base import Agent, AgentOpinion
from aruna.agents.context import DecisionContext
from aruna.agents.context_agents import FundamentalAgent, NewsAgent
from aruna.agents.market import (
    MomentumAgent,
    RegimeAgent,
    ReversalAgent,
    StructureAgent,
    TechnicalAgent,
    VolumeAgent,
)
from aruna.agents.notrade import NoTradeVerdict, evaluate_no_trade
from aruna.agents.risk import RiskAgent, RiskAssessment, assess_risk
from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import Decision
from aruna.core.logging import get_logger

log = get_logger("aruna.agents")


def default_roster() -> list[Agent]:
    """The SPEC 12 market-facing roster.

    ARUNA_ANALYST, PROSECUTOR and COUNCIL_JUDGE are excluded: the first two read
    these opinions rather than the market, and the judge belongs to PHASE 6.
    """
    return [
        TechnicalAgent(),
        StructureAgent(),
        MomentumAgent(),
        VolumeAgent(),
        ReversalAgent(),
        RegimeAgent(),
        NewsAgent(),
        FundamentalAgent(),
        RiskAgent(),
    ]


@dataclass(frozen=True, slots=True)
class Deliberation:
    """The full round-one record for one asset at one instant."""

    symbol: str
    market: str
    interval: str
    as_of: datetime
    decided_at: datetime
    opinions: tuple[AgentOpinion, ...]
    proposal: AgentOpinion
    prosecutor: AgentOpinion
    critique: CritiqueResult
    risk: RiskAssessment
    no_trade: NoTradeVerdict
    outcome: Decision
    confidence: float
    independence: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    #: PHASE 5 has no council. Present so no later phase can read this as one.
    is_council_decision: bool = False

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
            "outcome": self.outcome.value,
            "confidence": round(self.confidence, 3),
            "independence": round(self.independence, 3),
            "participating_agents": self.participating,
            "is_council_decision": False,
            "phase_note": (
                "hanya round satu - cross-protest, veto dan judge baru ada di PHASE 6"
            ),
            "opinions": [o.to_dict() for o in self.opinions],
            "proposal": self.proposal.to_dict(),
            "prosecutor": self.prosecutor.to_dict(),
            "critique": self.critique.to_dict(),
            "risk": self.risk.to_dict(),
            "no_trade": self.no_trade.to_dict(),
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        parts = [
            f"{self.symbol} {self.interval}",
            f"{self.outcome.value}",
            f"conf={self.confidence:.2f}",
            f"agents={self.participating}/{len(self.opinions)}",
            f"risk={self.risk.overall.value}",
        ]
        if self.no_trade.blocked:
            parts.append(f"diblokir[{self.no_trade.summary()}]")
        return "  ".join(parts)


class DeliberationEngine:
    """Runs the roster, the prosecutor, the critic, and the gates."""

    def __init__(self, roster: list[Agent] | None = None) -> None:
        self._roster = roster if roster is not None else default_roster()

    def deliberate(self, context: DecisionContext) -> Deliberation:
        # Round 1: every agent forms a view without seeing the others (SPEC 14).
        opinions = tuple(agent.safe_evaluate(context) for agent in self._roster)
        pool = OpinionPool(opinions=opinions)

        proposal = ArunaAnalyst(pool).safe_evaluate(context)
        prosecutor = Prosecutor(pool, proposal).safe_evaluate(context)
        critique = SelfCritic().critique(context, pool, proposal)

        risk = assess_risk(context)
        notes: list[str] = []

        outcome = proposal.decision
        confidence = proposal.confidence

        # SPEC 15: stronger counter-evidence forces a reassessment. PHASE 5 has
        # no judge to arbitrate, so the honest reassessment is to stand down
        # rather than to flip - flipping on the critic's word alone would just
        # replace one unexamined verdict with another.
        if critique.reassessment_required:
            notes.append(
                f"self-critic menemukan counter-evidence yang lebih kuat "
                f"({critique.counter_weight:.2f} vs {critique.proposal_weight:.2f}); "
                "mundur dulu menunggu council PHASE 6"
            )
            outcome = Decision.WAIT
            confidence = 0.0

        # SPEC 13: the prosecutor disagreeing does not overrule, but it does
        # reduce how much the proposal can claim.
        if (
            prosecutor.decision.is_directional
            and outcome.is_directional
            and prosecutor.decision is not outcome
        ):
            notes.append(
                f"prosecutor secara independen menyimpulkan {prosecutor.decision.value} "
                f"di {prosecutor.confidence * 100:.0f}% - confidence dipotong setengah"
            )
            confidence = round(confidence * 0.5, 3)

        no_trade = evaluate_no_trade(
            context,
            risk=risk,
            council_confidence=confidence if outcome.is_directional else None,
            participating_agents=(pool.participating and len(pool.participating)) or 0,
        )
        if no_trade.blocked:
            outcome = no_trade.decision
            confidence = 0.0
            notes.append(f"mesin no-trade: {no_trade.summary()}")

        result = Deliberation(
            symbol=context.symbol,
            market=context.market.value,
            interval=context.interval.value,
            as_of=context.as_of,
            decided_at=now_utc(),
            opinions=opinions,
            proposal=proposal,
            prosecutor=prosecutor,
            critique=critique,
            risk=risk,
            no_trade=no_trade,
            outcome=outcome,
            confidence=confidence,
            independence=pool.independence(),
            notes=tuple(notes),
        )
        log.info(
            "agents.deliberated",
            symbol=context.symbol,
            interval=context.interval.value,
            outcome=outcome.value,
            confidence=round(confidence, 3),
            participating=result.participating,
            risk=risk.overall.value,
            blocked=no_trade.blocked,
        )
        return result


__all__ = ["Deliberation", "DeliberationEngine", "default_roster"]
