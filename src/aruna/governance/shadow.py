"""Shadow models (SPEC 44).

A shadow runs alongside the live rules and is never acted on. Its decisions are
recorded so a variant can accumulate a track record on the same evidence, at the
same instants, before anyone considers switching to it.

The trap this module is built around: **a shadow that mostly agrees with the
live model has told you almost nothing**, and the agreement rate is easy to read
as reassurance. If two models differ on 3 of 500 decisions, the comparison rests
on 3 observations no matter how impressive the other 497 look. That is reported
first, before any accuracy figure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.core.enums import Decision

#: Below this share of disagreements, a comparison is not measuring the
#: difference between the models - it is measuring the evidence they share.
MIN_DISAGREEMENT_RATE = 0.05


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """One instant, decided twice."""

    signal_id: str
    symbol: str
    live: Decision
    shadow: Decision
    live_confidence: float = 0.0
    shadow_confidence: float = 0.0
    #: None until the horizon has elapsed.
    live_correct: bool | None = None
    shadow_correct: bool | None = None

    @property
    def agreed(self) -> bool:
        return self.live is self.shadow


@dataclass(slots=True)
class ShadowComparison:
    """What running a variant alongside the live rules has shown."""

    proposal_key: str
    decisions: list[ShadowDecision] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.decisions)

    @property
    def disagreements(self) -> list[ShadowDecision]:
        return [d for d in self.decisions if not d.agreed]

    @property
    def disagreement_rate(self) -> float:
        return len(self.disagreements) / self.total if self.total else 0.0

    @property
    def resolved_disagreements(self) -> list[ShadowDecision]:
        return [
            d
            for d in self.disagreements
            if d.live_correct is not None and d.shadow_correct is not None
        ]

    @property
    def shadow_wins(self) -> int:
        return sum(
            1 for d in self.resolved_disagreements if d.shadow_correct and not d.live_correct
        )

    @property
    def live_wins(self) -> int:
        return sum(
            1 for d in self.resolved_disagreements if d.live_correct and not d.shadow_correct
        )

    @property
    def verdict(self) -> str:
        if not self.total:
            return "NO SHADOW DECISIONS: the variant has not run yet"
        if self.disagreement_rate < MIN_DISAGREEMENT_RATE:
            return (
                f"INDISTINGUISHABLE: the models differ on "
                f"{len(self.disagreements)} of {self.total} decisions "
                f"({self.disagreement_rate * 100:.1f}%). Whatever the variant "
                "changes, it barely changes what ARUNA does - and any accuracy "
                "difference rests on those few cases alone"
            )
        resolved = self.resolved_disagreements
        if not resolved:
            return (
                f"PENDING: {len(self.disagreements)} disagreement(s), none "
                "resolved yet - nothing can be compared"
            )
        return (
            f"{self.shadow_wins} shadow win(s) vs {self.live_wins} live win(s) "
            f"across {len(resolved)} resolved disagreement(s)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal_key,
            "decisions": self.total,
            "disagreements": len(self.disagreements),
            "disagreement_rate": round(self.disagreement_rate, 4),
            "resolved_disagreements": len(self.resolved_disagreements),
            "shadow_wins": self.shadow_wins,
            "live_wins": self.live_wins,
            "verdict": self.verdict,
            "note": (
                "only the disagreements carry information: where the models "
                "agreed, running two of them proved nothing. A comparison rests "
                "on the count of resolved disagreements, never on the total"
            ),
        }


def compare(proposal_key: str, decisions: list[ShadowDecision]) -> ShadowComparison:
    return ShadowComparison(proposal_key=proposal_key, decisions=list(decisions))


__all__ = [
    "MIN_DISAGREEMENT_RATE",
    "ShadowComparison",
    "ShadowDecision",
    "compare",
]
