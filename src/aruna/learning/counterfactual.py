"""Counterfactuals and ghost signals (SPEC 27, 28).

Two questions a track record cannot answer on its own:

* **What would the other decision have produced?** A BUY that lost 1% looks bad
  until you notice the SELL would have lost 3%. Direction is not the only thing
  being chosen.
* **What did standing aside cost?** A system that never trades has a perfect
  loss record. SPEC 28 calls these ghost signals: the WAITs where a position
  would have paid, which are invisible in every conventional performance report.

This is also where looking *past* the horizon becomes legitimate. Scoring a
prediction may only use data from inside its window (SPEC 24) - that rule
protects the score. A counterfactual is not a score; it is a question about what
happened next, and answering it needs exactly the data the scoring rule
excludes. The two are kept in separate modules so the boundary is visible, and
every extended-window figure below is labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.core.enums import Decision
from aruna.signals.models import LockedSignal, OutcomeClass

#: How far past the horizon a counterfactual may look, as a multiple of the
#: horizon. Bounded because "it would have worked eventually" is not a finding -
#: given enough time every direction is right at some point.
LOOKAHEAD_MULTIPLE = 1.0

#: A ghost signal needs a move worth having. Below this the WAIT cost nothing
#: worth naming, and reporting it would bury the real misses.
GHOST_THRESHOLD_PCT = 1.0


@dataclass(frozen=True, slots=True)
class Counterfactual:
    """What the alternative decision would have produced (SPEC 27)."""

    signal_id: str
    symbol: str
    taken: Decision
    taken_move_pct: float
    alternative: Decision
    alternative_move_pct: float
    #: True when the road not taken was the better one.
    alternative_was_better: bool = False

    def summary(self) -> str:
        return (
            f"{self.symbol}: {self.taken.value} returned "
            f"{self.taken_move_pct:+.2f}%, {self.alternative.value} would have "
            f"returned {self.alternative_move_pct:+.2f}%"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "taken": self.taken.value,
            "taken_move_pct": round(self.taken_move_pct, 4),
            "alternative": self.alternative.value,
            "alternative_move_pct": round(self.alternative_move_pct, 4),
            "alternative_was_better": self.alternative_was_better,
            "note": "gross price move, before the costs a real position pays",
        }


def counterfactual(
    signal: LockedSignal, final_price: Decimal
) -> Counterfactual | None:
    """The mirror of a directional call (SPEC 27).

    Returns ``None`` for a WAIT: the alternative to standing aside is a whole
    family of positions rather than one mirror, and :func:`ghost_signal` asks
    that question properly.
    """
    if not signal.is_directional or signal.reference_price <= 0:
        return None

    move = float(
        (final_price - signal.reference_price) / signal.reference_price * 100
    )
    taken_return = move if signal.direction is Decision.BUY else -move
    other = Decision.SELL if signal.direction is Decision.BUY else Decision.BUY

    return Counterfactual(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        taken=signal.direction,
        taken_move_pct=round(taken_return, 6),
        alternative=other,
        alternative_move_pct=round(-taken_return, 6),
        alternative_was_better=taken_return < 0,
    )


@dataclass(frozen=True, slots=True)
class GhostSignal:
    """A WAIT the market punished (SPEC 28)."""

    signal_id: str
    symbol: str
    horizon: str
    #: The best move a position could have caught inside the horizon.
    missed_move_pct: float
    #: Which direction would have caught it.
    direction: Decision
    reasoning: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        return (
            f"{self.symbol} {self.horizon}: stood aside through a "
            f"{self.missed_move_pct:+.2f}% move that a "
            f"{self.direction.value} would have caught"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "missed_move_pct": round(self.missed_move_pct, 4),
            "direction": self.direction.value,
            "why_we_waited": list(self.reasoning),
            "note": (
                "a missed move is not automatically a mistake - the evidence "
                "at the time may still have justified standing aside"
            ),
        }


def ghost_signal(
    signal: LockedSignal,
    max_favourable_pct: float,
    max_adverse_pct: float,
    *,
    threshold: float = GHOST_THRESHOLD_PCT,
) -> GhostSignal | None:
    """A WAIT that passed up a real move (SPEC 28).

    Uses the excursion range recorded for non-directional outcomes: the highest
    and lowest the market went during the horizon. Returns ``None`` when neither
    side moved enough to be worth catching.

    Deliberately not framed as an error. Standing aside on thin data is what the
    no-trade engine is *for*, and a system that treated every missed move as a
    failure would learn to trade everything.
    """
    if signal.is_directional:
        return None

    up, down = max_favourable_pct, abs(max_adverse_pct)
    if max(up, down) < threshold:
        return None

    if up >= down:
        move, direction = up, Decision.BUY
    else:
        move, direction = -down, Decision.SELL

    return GhostSignal(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        horizon=signal.horizon.value,
        missed_move_pct=round(move, 6),
        direction=direction,
        reasoning=signal.reasoning[:4],
    )


def reclassify_with_lookahead(
    signal: LockedSignal,
    outcome_class: OutcomeClass,
    prices_after: list[tuple[datetime, Decimal]],
) -> tuple[OutcomeClass, str | None]:
    """Detect a prediction that was right on a longer timescale (SPEC 23, 27).

    Returns the class and, when it changed, why. This is the only place
    ``HORIZON_MISMATCH`` can be assigned: deciding that a call was right but
    early requires data from after its horizon, which the scoring path is
    forbidden to touch.

    The reclassification never rescues a prediction's *score*. The stored
    outcome keeps the class it earned inside its own window; this is a separate
    observation, recorded alongside, saying the horizon was the part that was
    wrong. A system that let a later move upgrade a past loss would be marking
    its own homework with the answers in hand.
    """
    if outcome_class is not OutcomeClass.WRONG_FROM_START:
        return outcome_class, None
    if not signal.is_directional or signal.target_price is None or not prices_after:
        return outcome_class, None

    horizon_end = signal.resolves_at
    limit = horizon_end + signal.horizon.duration * LOOKAHEAD_MULTIPLE
    within = [
        price for moment, price in prices_after if horizon_end < moment <= limit
    ]
    if not within:
        return outcome_class, None

    reached = (
        any(p >= signal.target_price for p in within)
        if signal.direction is Decision.BUY
        else any(p <= signal.target_price for p in within)
    )
    if not reached:
        return outcome_class, None

    return OutcomeClass.HORIZON_MISMATCH, (
        f"target reached within {LOOKAHEAD_MULTIPLE:g}x the horizon after it "
        "expired - the direction was right and the horizon was too short. "
        "The recorded score is unchanged."
    )


def summarise_ghosts(ghosts: list[GhostSignal]) -> dict[str, Any]:
    """Aggregate what standing aside cost (SPEC 28)."""
    if not ghosts:
        return {
            "ghost_signals": 0,
            "note": "no WAIT passed up a move above the threshold",
        }
    moves = [abs(g.missed_move_pct) for g in ghosts]
    return {
        "ghost_signals": len(ghosts),
        "largest_missed_pct": round(max(moves), 4),
        "mean_missed_pct": round(sum(moves) / len(moves), 4),
        "threshold_pct": GHOST_THRESHOLD_PCT,
        "note": (
            "these are moves ARUNA did not take a position on. Whether each "
            "was a mistake depends on the evidence at the time, which is "
            "recorded on the signal"
        ),
    }


__all__ = [
    "GHOST_THRESHOLD_PCT",
    "LOOKAHEAD_MULTIPLE",
    "Counterfactual",
    "GhostSignal",
    "counterfactual",
    "ghost_signal",
    "reclassify_with_lookahead",
    "summarise_ghosts",
]
