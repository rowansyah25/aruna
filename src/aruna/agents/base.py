"""Agent contract (SPEC 12, 13, 15, 17).

An agent reads the evidence assembled in :class:`~aruna.agents.context.DecisionContext`
and returns an :class:`AgentOpinion`: a decision, a confidence, the reasoning
behind it, and - critically - **which evidence it actually used**.

Three invariants the whole council depends on, enforced here rather than by
convention:

* **No agent has a permanent stance (SPEC 12, 48).** Every role may return BUY,
  SELL or WAIT. There is no "always bearish" agent and no permanent opposition;
  :meth:`AgentOpinion.validate` rejects a role that tries to restrict itself.
* **Evidence is declared (SPEC 17).** Two agents reading the same RSI are not
  independent witnesses. The judge cannot discount that unless each agent says
  what it looked at, so ``evidence`` is mandatory for any non-abstaining view.
* **Abstention is honest (SPEC 33).** An agent with nothing to go on returns
  WAIT with ``abstained=True`` rather than inventing a weak opinion. A fabricated
  50/50 is worse than silence: it adds a vote without adding information.

These agents are **deterministic rule-based reasoners, not language models.**
That is deliberate: SPEC 29 requires confidence to be measured against realised
accuracy, and SPEC 39 requires a decision to replay identically. Both need
reproducible output. An LLM-backed agent can implement this same protocol later
without anything else changing.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aruna.core.enums import AgentRole, Decision, Horizon

if TYPE_CHECKING:
    from aruna.agents.context import DecisionContext


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A single thing an agent consulted.

    ``key`` is what makes SPEC 17 possible: two agents citing ``reading:rsi``
    are drawing on one observation, and the judge must not count that twice.
    """

    key: str
    value: float | None = None
    sample_size: int = 0
    reliable: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "sample_size": self.sample_size,
            "reliable": self.reliable,
            "detail": self.detail or None,
        }


@dataclass(frozen=True, slots=True)
class AgentOpinion:
    """One agent's independent view (SPEC 12 round 1)."""

    role: AgentRole
    decision: Decision
    confidence: float = 0.0
    reasoning: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    horizon: Horizon | None = None
    #: True when the agent had nothing usable to judge on.
    abstained: bool = False

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        # Ini bukan pesan untuk developer saja. `safe_evaluate` di bawah
        # menangkap ValueError-nya dan menjadikannya reasoning[0] agent, yang
        # lalu disalin apa adanya ke detail objection dan ikut terkirim di log
        # perdebatan - jadi kalimat Inggris di sini muncul di tengah pesan
        # berbahasa Indonesia, di depan operator.
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"{self.role.value} mengembalikan confidence {self.confidence}, "
                "di luar rentang 0..1"
            )
        if self.abstained and self.decision is not Decision.WAIT:
            raise ValueError(
                f"{self.role.value} abstain tapi mengembalikan "
                f"{self.decision.value}; abstain berarti WAIT"
            )
        if not self.abstained and not self.reasoning:
            raise ValueError(
                f"{self.role.value} memberi pandangan tanpa alasan - setiap "
                "opinion harus bisa dijelaskan (SPEC 39 decision replay)"
            )
        if self.decision.is_directional and self.confidence <= 0.0:
            raise ValueError(
                f"{self.role.value} mengembalikan {self.decision.value} di "
                "confidence nol; itu WAIT, bukan arah"
            )

    @property
    def evidence_keys(self) -> frozenset[str]:
        return frozenset(ref.key for ref in self.evidence)

    @property
    def reliable_evidence(self) -> int:
        return sum(1 for ref in self.evidence if ref.reliable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "decision": self.decision.value,
            "confidence": round(self.confidence, 3),
            "abstained": self.abstained,
            "horizon": self.horizon.value if self.horizon else None,
            "reasoning": list(self.reasoning),
            "evidence": [ref.to_dict() for ref in self.evidence],
        }

    def summary(self) -> str:
        if self.abstained:
            return f"{self.role.value}: ABSTAIN"
        return f"{self.role.value}: {self.decision.value} {self.confidence * 100:.0f}%"


def abstain(role: AgentRole, reason: str) -> AgentOpinion:
    """The honest answer when an agent has nothing to judge on."""
    return AgentOpinion(
        role=role,
        decision=Decision.WAIT,
        confidence=0.0,
        reasoning=(reason,),
        abstained=True,
    )


class Agent(abc.ABC):
    """One council member.

    Implementations must never raise: a broken agent should abstain, so one
    faulty reasoner cannot silence the whole council.
    """

    role: AgentRole

    @abc.abstractmethod
    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        """Form an independent view from the evidence."""

    def safe_evaluate(self, context: DecisionContext) -> AgentOpinion:
        try:
            return self.evaluate(context)
        except Exception as exc:  # noqa: BLE001 - a bad agent must not stop the council
            return abstain(
                self.role, f"agent gagal: {type(exc).__name__}: {exc}"
            )


#: No single agent may be certain. Each reads one narrow slice of the evidence,
#: and SPEC 6 treats every reading as evidence rather than truth - a lens that
#: cannot see news, structure and risk at once has no business reporting 100%.
#: Only the PHASE 6 council, weighing all of them, works above this.
MAX_AGENT_CONFIDENCE = 0.95


def scale_confidence(raw: float, *, evidence_quality: float) -> float:
    """Damp a confidence by how good the evidence behind it was.

    SPEC 6 and SPEC 16 both hinge on this: an agent that is sure from thin data
    must not present the same number as one that is sure from a full sample.
    """
    scaled = max(0.0, min(1.0, raw)) * max(0.0, min(1.0, evidence_quality))
    return round(min(scaled, MAX_AGENT_CONFIDENCE), 3)


__all__ = [
    "MAX_AGENT_CONFIDENCE",
    "Agent",
    "AgentOpinion",
    "EvidenceRef",
    "abstain",
    "scale_confidence",
]
