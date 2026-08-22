"""Veto and veto review (SPEC 18, 19).

**PROTEST != VETO.** An agent that disagrees files an objection and the judge
weighs it. A veto is different in kind: it stops the decision outright, and
SPEC 18 restricts it to critical conditions - a data outage, a stale price, an
extreme spread, a trading halt, a severe anomaly. Disagreement about direction
can never be one, however strongly held.

SPEC 19 then requires every veto to be reviewed:

    VETO -> VETO REVIEW -> VALID?  no  -> VETO REJECTED
                                   yes -> NO TRADE

The review re-checks the stated reason against the evidence. A veto whose
grounds are not actually present is overturned, so a broken risk probe cannot
silently freeze the system. Both the veto and its review are recorded either
way (SPEC 42).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.agents.context import DecisionContext
from aruna.agents.risk import RiskAssessment, RiskLevel
from aruna.core.enums import (
    AgentRole,
    Decision,
    Market,
    Regime,
    VetoReason,
    VetoReviewOutcome,
)

#: SPEC 18 restricts veto grounds by market. Anything outside these sets is a
#: protest, not a veto.
CRYPTO_VETO_REASONS: frozenset[VetoReason] = frozenset({
    VetoReason.DATA_OUTAGE,
    VetoReason.STALE_PRICE,
    VetoReason.EXTREME_SPREAD,
    VetoReason.EXTREME_VOLATILITY,
    VetoReason.CRITICAL_SECURITY_EVENT,
    VetoReason.SEVERE_ANOMALY,
    VetoReason.SYSTEM_INTEGRITY_FAILURE,
})

IDX_VETO_REASONS: frozenset[VetoReason] = frozenset({
    VetoReason.STALE_PRICE,
    VetoReason.DATA_OUTAGE,
    VetoReason.TRADING_HALT,
    VetoReason.CRITICAL_CORPORATE_ACTION_UNCERTAINTY,
    VetoReason.SEVERE_ANOMALY,
    VetoReason.SYSTEM_INTEGRITY_FAILURE,
})


def permitted_reasons(market: Market) -> frozenset[VetoReason]:
    return CRYPTO_VETO_REASONS if market is Market.CRYPTO else IDX_VETO_REASONS


@dataclass(frozen=True, slots=True)
class Veto:
    """A raised veto. Always carries a reason, always reviewable (SPEC 18)."""

    raised_by: AgentRole
    reason: VetoReason
    detail: str
    #: What the reviewer re-checks the claim against.
    evidence_key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "raised_by": self.raised_by.value,
            "reason": self.reason.value,
            "detail": self.detail,
            "evidence_key": self.evidence_key or None,
        }


@dataclass(frozen=True, slots=True)
class VetoReview:
    """The SPEC 19 review of one veto."""

    veto: Veto
    outcome: VetoReviewOutcome
    rationale: str

    @property
    def upheld(self) -> bool:
        return self.outcome is VetoReviewOutcome.VETO_UPHELD_NO_TRADE

    def to_dict(self) -> dict[str, object]:
        return {
            "veto": self.veto.to_dict(),
            "outcome": self.outcome.value,
            "rationale": self.rationale,
            "upheld": self.upheld,
        }


@dataclass(frozen=True, slots=True)
class VetoOutcome:
    vetoes: tuple[Veto, ...] = field(default_factory=tuple)
    reviews: tuple[VetoReview, ...] = field(default_factory=tuple)

    @property
    def upheld(self) -> tuple[VetoReview, ...]:
        return tuple(r for r in self.reviews if r.upheld)

    @property
    def rejected(self) -> tuple[VetoReview, ...]:
        return tuple(r for r in self.reviews if not r.upheld)

    @property
    def blocks(self) -> bool:
        """Only an upheld veto stops the decision (SPEC 19)."""
        return bool(self.upheld)

    @property
    def decision(self) -> Decision:
        """An upheld veto means NO TRADE.

        NO_SIGNAL rather than WAIT: these grounds mean the inputs cannot be
        trusted, so there is nothing to say - not "no setup right now".
        """
        return Decision.NO_SIGNAL if self.blocks else Decision.WAIT

    def to_dict(self) -> dict[str, object]:
        return {
            "vetoes": [v.to_dict() for v in self.vetoes],
            "reviews": [r.to_dict() for r in self.reviews],
            "upheld": len(self.upheld),
            "rejected": len(self.rejected),
            "blocks": self.blocks,
        }

    def summary(self) -> str:
        if not self.vetoes:
            return "tidak ada veto yang diajukan"
        if not self.blocks:
            return f"{len(self.rejected)} veto diajukan, semuanya ditolak saat review"
        return ", ".join(r.veto.reason.value for r in self.upheld)


def raise_vetoes(context: DecisionContext, risk: RiskAssessment) -> list[Veto]:
    """Critical conditions only (SPEC 18).

    Deliberately narrow. Everything an agent merely disagrees about goes
    through the protest rounds instead.
    """
    allowed = permitted_reasons(context.market)
    vetoes: list[Veto] = []

    def raise_it(reason: VetoReason, detail: str, key: str) -> None:
        if reason in allowed:
            vetoes.append(
                Veto(
                    raised_by=AgentRole.RISK,
                    reason=reason,
                    detail=detail,
                    evidence_key=key,
                )
            )

    state = context.state

    if state.data_quality in ("MISSING", "PROVIDER_DISCONNECTED", "UNAVAILABLE"):
        raise_it(
            VetoReason.DATA_OUTAGE,
            f"data market {state.data_quality}"
            + (f": {state.quality_detail}" if state.quality_detail else ""),
            "state.data_quality",
        )
    elif state.data_quality in ("STALE", "DUPLICATE"):
        raise_it(
            VetoReason.STALE_PRICE,
            f"data market {state.data_quality}"
            + (f": {state.quality_detail}" if state.quality_detail else ""),
            "state.data_quality",
        )
    elif state.data_quality not in ("OK",):
        raise_it(
            VetoReason.SEVERE_ANOMALY,
            f"data market ditandai {state.data_quality}",
            "state.data_quality",
        )

    spread = risk.of("spread")
    if spread is not None and spread.level is RiskLevel.EXTREME:
        raise_it(VetoReason.EXTREME_SPREAD, spread.detail, "risk.spread")

    volatility = risk.of("volatility")
    if volatility is not None and volatility.level is RiskLevel.EXTREME:
        raise_it(VetoReason.EXTREME_VOLATILITY, volatility.detail, "risk.volatility")

    if context.market is Market.IDX and state.market_open is False:
        raise_it(
            VetoReason.TRADING_HALT,
            f"exchange tutup ({state.session or 'di luar sesi'})",
            "state.market_open",
        )

    verdict = context.regime
    if verdict is not None and verdict.regime is Regime.ANOMALY:
        raise_it(
            VetoReason.SEVERE_ANOMALY,
            "regime diklasifikasikan ANOMALY: " + "; ".join(verdict.reasons[:2]),
            "regime",
        )

    return vetoes


def review_veto(
    veto: Veto, context: DecisionContext, risk: RiskAssessment
) -> VetoReview:
    """SPEC 19: re-check the stated ground against the evidence.

    A veto stands only if the condition it names is genuinely present. This is
    what stops a faulty probe from freezing the system indefinitely.
    """
    allowed = permitted_reasons(context.market)
    if veto.reason not in allowed:
        return VetoReview(
            veto=veto,
            outcome=VetoReviewOutcome.VETO_REJECTED,
            rationale=(
                f"{veto.reason.value} bukan dasar veto yang diizinkan untuk "
                f"{context.market.value} (SPEC 18) - ini protes, bukan veto"
            ),
        )

    state = context.state
    supported = False
    rationale = ""

    if veto.reason is VetoReason.DATA_OUTAGE:
        supported = state.data_quality in ("MISSING", "PROVIDER_DISCONNECTED", "UNAVAILABLE")
        rationale = f"kualitas data adalah {state.data_quality}"
    elif veto.reason is VetoReason.STALE_PRICE:
        supported = state.data_quality in ("STALE", "DUPLICATE")
        rationale = f"kualitas data adalah {state.data_quality}"
    elif veto.reason is VetoReason.EXTREME_SPREAD:
        factor = risk.of("spread")
        supported = factor is not None and factor.level is RiskLevel.EXTREME
        rationale = factor.detail if factor else "tidak ada pengukuran spread yang tersedia"
    elif veto.reason is VetoReason.EXTREME_VOLATILITY:
        factor = risk.of("volatility")
        supported = factor is not None and factor.level is RiskLevel.EXTREME
        rationale = (
            factor.detail if factor else "tidak ada pengukuran volatility yang tersedia"
        )
    elif veto.reason is VetoReason.TRADING_HALT:
        supported = state.market_open is False
        rationale = (
            f"exchange open={state.market_open}, "
            f"session={state.session or 'tidak diketahui'}"
        )
    elif veto.reason is VetoReason.SEVERE_ANOMALY:
        verdict = context.regime
        supported = (verdict is not None and verdict.regime is Regime.ANOMALY) or (
            state.data_quality not in ("OK",)
        )
        rationale = (
            f"regime={verdict.regime.value if verdict else 'tidak diketahui'}, "
            f"kualitas data={state.data_quality}"
        )
    else:
        # An unrecognised ground cannot be verified, so it cannot be upheld.
        return VetoReview(
            veto=veto,
            outcome=VetoReviewOutcome.VETO_REJECTED,
            rationale=(
                f"{veto.reason.value} tidak punya verifikasi yang terdefinisi - "
                "tidak bisa dikuatkan"
            ),
        )

    if supported:
        return VetoReview(
            veto=veto,
            outcome=VetoReviewOutcome.VETO_UPHELD_NO_TRADE,
            rationale=f"kondisi terkonfirmasi: {rationale}",
        )
    return VetoReview(
        veto=veto,
        outcome=VetoReviewOutcome.VETO_REJECTED,
        rationale=f"kondisi tidak ditemukan saat review: {rationale}",
    )


def run_veto_stage(context: DecisionContext, risk: RiskAssessment) -> VetoOutcome:
    """Raise, then review every veto (SPEC 18, 19)."""
    vetoes = raise_vetoes(context, risk)
    reviews = tuple(review_veto(veto, context, risk) for veto in vetoes)
    return VetoOutcome(vetoes=tuple(vetoes), reviews=reviews)


__all__ = [
    "CRYPTO_VETO_REASONS",
    "IDX_VETO_REASONS",
    "Veto",
    "VetoOutcome",
    "VetoReview",
    "permitted_reasons",
    "raise_vetoes",
    "review_veto",
    "run_veto_stage",
]
