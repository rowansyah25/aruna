"""Decision replay (SPEC 39).

Re-runs a stored decision from its recorded inputs and checks that it comes out
the same. If it does not, the stored record is not an explanation of anything -
it is a description of a process that no longer exists.

Three things have to be reconstructed for a replay to be honest, and getting any
of them wrong turns a real divergence into a false alarm or, worse, hides one:

* **The evidence**, clipped to the decision's own ``as_of`` (SPEC 24);
* **The measured SPEC 16 factors as they stood then**, not as they stand now.
  Agent reliability and calibration move as outcomes accumulate, so replaying
  today's weights against yesterday's decision would diverge for a reason that
  has nothing to do with determinism. This is why PHASE 8 made those tables
  append-only;
* **The lock time**, so the horizon and the staleness check land identically.

A divergence is reported field by field. "The replay differed" is not
actionable; "confidence 0.62 became 0.58 because REGIME now weighs 0.31 instead
of 0.44" is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.core.enums import Decision
from aruna.core.logging import get_logger

log = get_logger("aruna.backtest.replay")

#: Confidence is stored to three decimals, so anything below this is storage
#: rounding rather than a behavioural difference.
CONFIDENCE_TOLERANCE = 0.0005


@dataclass(frozen=True, slots=True)
class Divergence:
    field: str
    stored: Any
    replayed: Any

    def describe(self) -> str:
        return f"{self.field}: stored {self.stored!r}, replayed {self.replayed!r}"


@dataclass(slots=True)
class ReplayResult:
    signal_id: str
    symbol: str
    reproduced: bool = False
    divergences: list[Divergence] = field(default_factory=list)
    #: Why a replay could not be attempted at all, when that is the case.
    unavailable: str | None = None

    @property
    def status(self) -> str:
        if self.unavailable:
            return "NOT_REPLAYABLE"
        return "REPRODUCED" if self.reproduced else "DIVERGED"

    def summary(self) -> str:
        if self.unavailable:
            return f"{self.symbol} {self.signal_id}: NOT REPLAYABLE - {self.unavailable}"
        if self.reproduced:
            return f"{self.symbol} {self.signal_id}: reproduced exactly"
        return (
            f"{self.symbol} {self.signal_id}: DIVERGED - "
            + "; ".join(d.describe() for d in self.divergences)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "status": self.status,
            "divergences": [
                {"field": d.field, "stored": str(d.stored), "replayed": str(d.replayed)}
                for d in self.divergences
            ],
            "unavailable": self.unavailable,
            "note": (
                "a decision that cannot be reproduced from its stored inputs "
                "cannot be audited, and every explanation attached to it is "
                "unverifiable"
            ),
        }


def compare(
    *,
    signal_id: str,
    symbol: str,
    stored_decision: Decision,
    stored_confidence: float,
    stored_as_of: datetime,
    replayed_decision: Decision,
    replayed_confidence: float,
    replayed_as_of: datetime,
    stored_reasoning: tuple[str, ...] = (),
    replayed_reasoning: tuple[str, ...] = (),
) -> ReplayResult:
    """Compare a stored decision against its replay, field by field."""
    result = ReplayResult(signal_id=signal_id, symbol=symbol)

    if stored_decision is not replayed_decision:
        result.divergences.append(
            Divergence("decision", stored_decision.value, replayed_decision.value)
        )
    if abs(stored_confidence - replayed_confidence) > CONFIDENCE_TOLERANCE:
        result.divergences.append(
            Divergence(
                "confidence", round(stored_confidence, 3), round(replayed_confidence, 3)
            )
        )
    if stored_as_of != replayed_as_of:
        # The evidence cutoff moved, so the replay did not read what the
        # original read - any other difference below is a consequence.
        result.divergences.append(
            Divergence("as_of", stored_as_of.isoformat(), replayed_as_of.isoformat())
        )
    if stored_reasoning and replayed_reasoning:
        stored_head = stored_reasoning[0] if stored_reasoning else ""
        replayed_head = replayed_reasoning[0] if replayed_reasoning else ""
        if stored_head != replayed_head:
            result.divergences.append(
                Divergence("reasoning[0]", stored_head, replayed_head)
            )

    result.reproduced = not result.divergences
    if not result.reproduced:
        log.warning(
            "replay.diverged",
            signal_id=signal_id,
            symbol=symbol,
            fields=[d.field for d in result.divergences],
        )
    return result


def summarise(results: list[ReplayResult]) -> dict[str, Any]:
    """Aggregate a replay run (SPEC 39).

    Reports the *fields* that diverge, not just a count: a hundred decisions
    that all differ in confidence point at one cause, while a hundred differing
    in different fields point at many.
    """
    attempted = [r for r in results if not r.unavailable]
    reproduced = [r for r in attempted if r.reproduced]
    fields: dict[str, int] = {}
    for result in attempted:
        for divergence in result.divergences:
            fields[divergence.field] = fields.get(divergence.field, 0) + 1

    return {
        "examined": len(results),
        "replayable": len(attempted),
        "not_replayable": len(results) - len(attempted),
        "reproduced": len(reproduced),
        "diverged": len(attempted) - len(reproduced),
        "reproduction_rate": (
            round(len(reproduced) / len(attempted), 4) if attempted else None
        ),
        "diverging_fields": dict(
            sorted(fields.items(), key=lambda kv: kv[1], reverse=True)
        ),
        "verdict": _verdict(attempted, reproduced),
    }


def _verdict(attempted: list[ReplayResult], reproduced: list[ReplayResult]) -> str:
    if not attempted:
        return "NO DECISIONS WERE REPLAYABLE: nothing can be said about determinism"
    if len(reproduced) == len(attempted):
        return (
            f"DETERMINISTIC: all {len(attempted)} replayed decision(s) reproduced "
            "exactly from their stored inputs"
        )
    return (
        f"NON-DETERMINISTIC: {len(attempted) - len(reproduced)} of "
        f"{len(attempted)} decision(s) did not reproduce. Stored explanations "
        "for those decisions cannot be verified."
    )


__all__ = [
    "CONFIDENCE_TOLERANCE",
    "Divergence",
    "ReplayResult",
    "compare",
    "summarise",
]
