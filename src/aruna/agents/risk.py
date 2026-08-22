"""Risk engine (SPEC 32) and the RISK agent.

SPEC 32 names the factors: volatility, drawdown risk, liquidity, spread, gap
risk, event risk, concentration, correlation - plus exchange risk and extreme
volatility for crypto, and halt, corporate action and gap risk for IDX.

The RISK agent is unusual in the roster and worth being explicit about: it
almost never says BUY. That is not a permanent bearish stance (which SPEC 12
forbids) - it is that *risk* is not a directional opinion. It answers "is this
a sane moment to take a position", so its vocabulary is WAIT when conditions
are dangerous and abstention when they are ordinary. It never manufactures a
direction it has no basis for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from aruna.agents.base import Agent, AgentOpinion, EvidenceRef, abstain
from aruna.agents.context import DecisionContext
from aruna.core.enums import AgentRole, Decision, Market, Regime


class RiskLevel(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"

    @property
    def severity(self) -> int:
        return {"LOW": 0, "MODERATE": 1, "HIGH": 2, "EXTREME": 3}[self.value]


@dataclass(frozen=True, slots=True)
class RiskFactor:
    name: str
    level: RiskLevel
    detail: str
    value: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "level": self.level.value,
            "detail": self.detail,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    factors: tuple[RiskFactor, ...] = field(default_factory=tuple)

    @property
    def overall(self) -> RiskLevel:
        """Worst factor wins. Risk does not average out."""
        if not self.factors:
            return RiskLevel.LOW
        return max((f.level for f in self.factors), key=lambda level: level.severity)

    @property
    def blocking(self) -> tuple[RiskFactor, ...]:
        return tuple(f for f in self.factors if f.level is RiskLevel.EXTREME)

    def of(self, name: str) -> RiskFactor | None:
        return next((f for f in self.factors if f.name == name), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "overall": self.overall.value,
            "factors": [f.to_dict() for f in self.factors],
            "blocking": [f.name for f in self.blocking],
        }


# Thresholds, named so they can be argued with in one place.
ATR_HIGH_PCT, ATR_EXTREME_PCT = 3.0, 6.0
SPREAD_HIGH_BPS, SPREAD_EXTREME_BPS = 50.0, 150.0
GAP_HIGH_PCT, GAP_EXTREME_PCT = 2.0, 5.0
THIN_VOLUME_RATIO = 0.3
CORRELATION_CONCENTRATION = 0.7


def assess_risk(context: DecisionContext) -> RiskAssessment:
    """Evaluate every SPEC 32 factor that the available evidence supports."""
    factors: list[RiskFactor] = []
    state = context.state

    # ---- volatility ------------------------------------------------------
    atr = context.reading("atr")
    if atr is not None and atr.reliable:
        atr_pct = atr.components.get("atr_pct")
        if atr_pct is not None:
            if atr_pct >= ATR_EXTREME_PCT:
                level = RiskLevel.EXTREME
            elif atr_pct >= ATR_HIGH_PCT:
                level = RiskLevel.HIGH
            else:
                level = RiskLevel.LOW
            factors.append(
                RiskFactor(
                    "volatility", level, f"ATR sebesar {atr_pct:.2f}% dari harga", atr_pct
                )
            )

    # ---- spread ----------------------------------------------------------
    if state.spread_bps is not None:
        spread = float(state.spread_bps)
        if spread >= SPREAD_EXTREME_BPS:
            level = RiskLevel.EXTREME
        elif spread >= SPREAD_HIGH_BPS:
            level = RiskLevel.HIGH
        else:
            level = RiskLevel.LOW
        factors.append(
            RiskFactor("spread", level, f"spread bid/ask {spread:.1f} bps", spread)
        )

    # ---- liquidity -------------------------------------------------------
    volume_ratio = context.value("volume_anomaly")
    if volume_ratio is not None and volume_ratio <= THIN_VOLUME_RATIO:
        factors.append(
            RiskFactor(
                "liquidity",
                RiskLevel.HIGH,
                f"volume cuma {volume_ratio:.2f}x rata-ratanya - order book tipis",
                volume_ratio,
            )
        )

    if state.bid_depth is not None and state.ask_depth is not None:
        total = float(state.bid_depth) + float(state.ask_depth)
        if total <= 0:
            factors.append(
                RiskFactor("liquidity", RiskLevel.EXTREME, "order book kosong")
            )

    # ---- gap risk --------------------------------------------------------
    gap = context.value("gap")
    if gap is not None:
        magnitude = abs(gap)
        if magnitude >= GAP_EXTREME_PCT:
            level = RiskLevel.EXTREME
        elif magnitude >= GAP_HIGH_PCT:
            level = RiskLevel.HIGH
        else:
            level = RiskLevel.LOW
        factors.append(
            RiskFactor("gap", level, f"gap pembukaan {gap:+.2f}%", gap)
        )

    # ---- regime / event --------------------------------------------------
    verdict = context.regime
    if verdict is not None:
        if verdict.regime is Regime.ANOMALY:
            factors.append(
                RiskFactor(
                    "event", RiskLevel.EXTREME, "regime terklasifikasi ANOMALY"
                )
            )
        elif verdict.regime is Regime.HIGH_VOLATILITY:
            factors.append(
                RiskFactor("event", RiskLevel.HIGH, "regime volatilitas tinggi")
            )
        elif verdict.regime is Regime.UNCERTAIN:
            factors.append(
                RiskFactor(
                    "event", RiskLevel.MODERATE, "regime tidak bisa diklasifikasi"
                )
            )

    # ---- correlation / concentration (SPEC 17, 32) ------------------------
    if context.correlation is not None:
        clustered = context.correlation.strongly_correlated(CORRELATION_CONCENTRATION)
        related = [
            p for p in clustered if context.symbol in (p.left, p.right)
        ]
        if related:
            names = ", ".join(
                p.right if p.left == context.symbol else p.left for p in related[:3]
            )
            factors.append(
                RiskFactor(
                    "concentration",
                    RiskLevel.HIGH,
                    f"bergerak sebagai satu posisi bersama {names}",
                    max(abs(p.coefficient) for p in related),
                )
            )

    # ---- data quality ----------------------------------------------------
    if state.data_quality != "OK":
        factors.append(
            RiskFactor(
                "data_quality",
                RiskLevel.EXTREME,
                f"data market ditandai {state.data_quality}"
                + (f": {state.quality_detail}" if state.quality_detail else ""),
            )
        )
    if not state.is_realtime:
        factors.append(
            RiskFactor(
                "data_freshness",
                RiskLevel.HIGH,
                f"feed tertunda ~{state.declared_delay_sec // 60} menit",
                float(state.declared_delay_sec),
            )
        )

    # ---- per-market (SPEC 32) --------------------------------------------
    if context.market is Market.IDX:
        if state.market_open is False:
            factors.append(
                RiskFactor(
                    "market_halt",
                    RiskLevel.EXTREME,
                    f"exchange tutup ({state.session or 'sesi tidak diketahui'})",
                )
            )
        # SPEC 32 also names corporate-action risk. ARUNA has no corporate
        # action feed - IDX's own announcements return 403 - so the gap is
        # declared rather than silently scored as zero risk.
        factors.append(
            RiskFactor(
                "corporate_action",
                RiskLevel.MODERATE,
                "tidak ada feed corporate action - risikonya tidak terukur, "
                "bukan berarti nol",
            )
        )
    else:
        factors.append(
            RiskFactor(
                "exchange",
                RiskLevel.MODERATE,
                f"eksposur satu venue ke {state.source or 'exchange yang dikonfigurasi'}",
            )
        )

    return RiskAssessment(factors=tuple(factors))


class RiskAgent(Agent):
    """Judges whether conditions permit taking a position at all."""

    role = AgentRole.RISK

    def evaluate(self, context: DecisionContext) -> AgentOpinion:
        assessment = assess_risk(context)
        if not assessment.factors:
            return abstain(self.role, "tidak ada evidence risiko yang bisa dinilai")

        evidence = tuple(
            EvidenceRef(
                key=f"risk:{factor.name}",
                value=factor.value,
                sample_size=1,
                reliable=True,
                detail=f"{factor.level.value}: {factor.detail}",
            )
            for factor in assessment.factors
        )
        reasons = [f"risiko keseluruhan {assessment.overall.value}"]
        reasons += [
            f"{f.name}: {f.detail}"
            for f in assessment.factors
            if f.level.severity >= RiskLevel.HIGH.severity
        ]

        blocking = assessment.blocking
        if blocking:
            reasons.append(
                "kondisi menutup kemungkinan mengambil posisi: "
                + ", ".join(f.name for f in blocking)
            )
            return AgentOpinion(
                role=self.role,
                decision=Decision.WAIT,
                confidence=1.0,
                reasoning=tuple(reasons),
                evidence=evidence,
                horizon=context.interval,
            )

        if assessment.overall is RiskLevel.HIGH:
            reasons.append("risiko meningkat - kecilkan position size atau menyingkir")
            return AgentOpinion(
                role=self.role,
                decision=Decision.WAIT,
                confidence=0.6,
                reasoning=tuple(reasons),
                evidence=evidence,
                horizon=context.interval,
            )

        # Ordinary conditions are not an argument for a trade, so the honest
        # output is abstention rather than a manufactured vote.
        return AgentOpinion(
            role=self.role,
            decision=Decision.WAIT,
            confidence=0.0,
            reasoning=(
                f"risiko {assessment.overall.value} - tidak ada keberatan, "
                "dan tidak ada pandangan arah yang bisa ditawarkan",
            ),
            evidence=evidence,
            horizon=context.interval,
            abstained=True,
        )


__all__ = [
    "RiskAgent",
    "RiskAssessment",
    "RiskFactor",
    "RiskLevel",
    "assess_risk",
]
