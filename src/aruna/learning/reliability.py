"""Per-agent historical reliability (SPEC 30).

SPEC 16 asks the judge to weigh agents by how right they have been. Since
PHASE 6 that factor has been declared `unavailable` and applied as a neutral
1.0, because nothing had resolved yet. This module is what makes it available -
and, more often, what keeps it honestly unavailable.

Two rules govern the design:

* **An agent is judged on the calls it backed, not on the council's verdict.**
  An agent that argued SELL in a session the council resolved as BUY should not
  be credited when the BUY works out. Reliability is read from the stored
  per-agent weights, which record who backed what.
* **Silence is not failure.** Abstaining is a legitimate act, and an agent that
  correctly declines to read thin evidence must not be punished for it. Only
  directional opinions are scored.

The sample thresholds are the load-bearing part. A reliability multiplier
derived from six observations would let one unlucky week silence an agent
permanently, and the system would have no way back: the agent it muted would
stop accumulating the record that could clear it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aruna.core.enums import AgentRole

#: Scored opinions an agent needs before its record adjusts its weight.
MIN_RELIABILITY_SAMPLE = 25

#: Bounds on the multiplier. An agent is never silenced outright and never
#: doubled: the roster exists to disagree, and an agent that cannot be heard
#: cannot be proven right later.
MIN_MULTIPLIER = 0.7
MAX_MULTIPLIER = 1.2

#: Accuracy treated as the neutral point. Below it an agent loses weight, above
#: it gains. Not 0.5: a directional call in a market that mostly goes sideways
#: is harder than a coin flip, and 0.5 would flatter every agent.
NEUTRAL_ACCURACY = 0.5


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """One agent's track record on the calls it actually backed."""

    role: AgentRole
    scored: int = 0
    correct: int = 0
    #: Times this agent's view opposed the council's and the council was wrong.
    vindicated: int = 0
    #: Times it opposed and the council was right.
    overruled_correctly: int = 0

    @property
    def sufficient(self) -> bool:
        return self.scored >= MIN_RELIABILITY_SAMPLE

    @property
    def accuracy(self) -> float | None:
        if not self.sufficient or not self.scored:
            return None
        return round(self.correct / self.scored, 4)

    @property
    def multiplier(self) -> float | None:
        """Weight adjustment for the judge, or ``None`` while unproven."""
        accuracy = self.accuracy
        if accuracy is None:
            return None
        # Linear around the neutral point, then clamped.
        raw = 1.0 + (accuracy - NEUTRAL_ACCURACY)
        return round(min(MAX_MULTIPLIER, max(MIN_MULTIPLIER, raw)), 4)

    @property
    def status(self) -> str:
        if not self.sufficient:
            return "INSUFFICIENT_SAMPLE"
        accuracy = self.accuracy or 0.0
        if accuracy > NEUTRAL_ACCURACY + 0.1:
            return "ABOVE_NEUTRAL"
        if accuracy < NEUTRAL_ACCURACY - 0.1:
            return "BELOW_NEUTRAL"
        return "NEUTRAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.role.value,
            "scored_opinions": self.scored,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "multiplier": self.multiplier,
            "vindicated_against_council": self.vindicated,
            "overruled_and_council_was_right": self.overruled_correctly,
            "status": self.status,
            "needs": max(0, MIN_RELIABILITY_SAMPLE - self.scored),
        }


@dataclass(frozen=True, slots=True)
class ReliabilityReport:
    records: tuple[AgentRecord, ...] = field(default_factory=tuple)

    @property
    def measured(self) -> tuple[AgentRecord, ...]:
        return tuple(r for r in self.records if r.sufficient)

    def multiplier(self, role: AgentRole) -> float | None:
        for record in self.records:
            if record.role is role:
                return record.multiplier
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agents": [r.to_dict() for r in self.records],
            "measured": len(self.measured),
            "total": len(self.records),
            "note": (
                f"an agent's weight is adjusted only after "
                f"{MIN_RELIABILITY_SAMPLE} scored opinions; until then its "
                "reliability is reported as unavailable, not as average"
            ),
        }


def build_reliability(rows: list[dict[str, Any]]) -> ReliabilityReport:
    """Aggregate per-agent outcomes (SPEC 30).

    Each row is one agent's directional opinion in one resolved session:
    ``agent``, ``agent_decision``, ``council_decision``, ``direction_correct``.

    An agent is scored on whether *its own* call matched what the market did,
    which is reconstructed from the council's decision and whether the council
    was right. When the agent agreed with the council, it shares the council's
    result; when it dissented, it was right exactly when the council was wrong.
    """
    tallies: dict[AgentRole, dict[str, int]] = {}

    for row in rows:
        role = _role(row.get("agent"))
        if role is None:
            continue
        agent_decision = row.get("agent_decision")
        council_decision = row.get("council_decision")
        correct = row.get("direction_correct")
        if correct is None or agent_decision not in ("BUY", "SELL"):
            # Abstentions and non-directional views are not scored: declining
            # to read thin evidence is a legitimate act, not a failure.
            continue

        council_was_right = bool(correct)
        agreed = agent_decision == council_decision
        agent_was_right = council_was_right if agreed else not council_was_right

        tally = tallies.setdefault(
            role, {"scored": 0, "correct": 0, "vindicated": 0, "overruled": 0}
        )
        tally["scored"] += 1
        tally["correct"] += int(agent_was_right)
        if not agreed:
            if council_was_right:
                tally["overruled"] += 1
            else:
                tally["vindicated"] += 1

    records = tuple(
        AgentRecord(
            role=role,
            scored=tally["scored"],
            correct=tally["correct"],
            vindicated=tally["vindicated"],
            overruled_correctly=tally["overruled"],
        )
        for role, tally in sorted(tallies.items(), key=lambda kv: kv[0].value)
    )
    return ReliabilityReport(records=records)


def _role(value: Any) -> AgentRole | None:
    if isinstance(value, AgentRole):
        return value
    try:
        return AgentRole(value)
    except (ValueError, TypeError):
        return None


__all__ = [
    "MAX_MULTIPLIER",
    "MIN_MULTIPLIER",
    "MIN_RELIABILITY_SAMPLE",
    "NEUTRAL_ACCURACY",
    "AgentRecord",
    "ReliabilityReport",
    "build_reliability",
]
