"""Multi-horizon reconciliation (SPEC 10).

SPEC 10 is explicit that horizons **must not be forced to agree**. Its worked
example has 5M BUY 82%, 15M SELL 71%, 1H SELL 64% and a final answer of
"BUY 5M ONLY" - the short-horizon call stands on its own merits while the
longer ones disagree.

That shapes the design: this module does not average horizons into one verdict.
It records each horizon's own conclusion, reports where they conflict, and
scopes the tradeable set to the horizons that actually earned it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.core.enums import Decision, Horizon
from aruna.council.session import CouncilVerdict


@dataclass(frozen=True, slots=True)
class HorizonView:
    horizon: Horizon
    decision: Decision
    confidence: float

    def summary(self) -> str:
        return f"{self.horizon.value} {self.decision.value} {self.confidence * 100:.0f}%"


@dataclass(frozen=True, slots=True)
class MultiHorizonView:
    """Every horizon's own conclusion, unreconciled by design."""

    symbol: str
    views: tuple[HorizonView, ...] = field(default_factory=tuple)

    @property
    def directional(self) -> tuple[HorizonView, ...]:
        return tuple(v for v in self.views if v.decision.is_directional)

    @property
    def conflicted(self) -> bool:
        """True when horizons point in opposite directions.

        Not a fault. SPEC 10 treats it as a legitimate reading of the market -
        a short-term bounce inside a longer downtrend is exactly this shape.
        """
        directions = {v.decision for v in self.directional}
        return Decision.BUY in directions and Decision.SELL in directions

    def tradeable(self, *, min_confidence: float = 0.35) -> tuple[HorizonView, ...]:
        """Horizons whose own case is strong enough to act on."""
        return tuple(v for v in self.directional if v.confidence >= min_confidence)

    def scope(self, *, min_confidence: float = 0.35) -> str:
        """The SPEC 10 style answer: which horizons, if any, carry the call."""
        qualifying = self.tradeable(min_confidence=min_confidence)
        if not qualifying:
            return "NO HORIZON QUALIFIES"

        by_decision: dict[Decision, list[str]] = {}
        for view in qualifying:
            by_decision.setdefault(view.decision, []).append(view.horizon.value)

        parts = [
            f"{decision.value} {', '.join(horizons)} ONLY"
            for decision, horizons in by_decision.items()
        ]
        return " | ".join(parts)

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "views": [
                {
                    "horizon": v.horizon.value,
                    "decision": v.decision.value,
                    "confidence": round(v.confidence, 3),
                }
                for v in self.views
            ],
            "conflicted": self.conflicted,
            "scope": self.scope(),
            "note": (
                "horizons are not reconciled into one verdict; SPEC 10 permits "
                "them to disagree"
            ),
        }


def build_view(symbol: str, verdicts: dict[Horizon, CouncilVerdict]) -> MultiHorizonView:
    return MultiHorizonView(
        symbol=symbol,
        views=tuple(
            HorizonView(
                horizon=horizon,
                decision=verdict.decision,
                confidence=verdict.confidence,
            )
            for horizon, verdict in sorted(
                verdicts.items(), key=lambda kv: kv[0].duration
            )
        ),
    )


__all__ = ["HorizonView", "MultiHorizonView", "build_view"]
