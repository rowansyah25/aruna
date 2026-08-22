"""Valuation verdict (SPEC 7).

Produces UNDERVALUED / FAIR_VALUE / OVERVALUED / UNCERTAIN from the reported
fundamentals.

**SPEC 7's standing instruction is enforced here: undervalued is never an
automatic BUY.** The verdict is a valuation observation, nothing more. It
carries no direction and no trade confidence, and :class:`ValuationReport`
states that in its own payload so nothing downstream can quietly promote it.
There are good reasons a company is cheap, and this module cannot tell which
one applies.

UNCERTAIN is a first-class answer, returned whenever coverage is thin or the
signals disagree. A confident verdict from three metrics would be worse than
admitting the data is not there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.core.enums import ValuationVerdict
from aruna.fundamental.models import Fundamentals

# Thresholds. Deliberately broad and sector-blind - see the limitations in the
# PHASE 4 report. A bank and a miner do not share a fair P/B, and nothing here
# pretends otherwise.
CHEAP_PE, EXPENSIVE_PE = 10.0, 25.0
CHEAP_PB, EXPENSIVE_PB = 1.0, 3.0
STRONG_ROE, WEAK_ROE = 15.0, 5.0
HIGH_DER = 2.0
GOOD_DIVIDEND = 4.0


@dataclass(frozen=True, slots=True)
class ValuationReport:
    symbol: str
    verdict: ValuationVerdict
    #: 0..1, from metric coverage and how much the signals agreed.
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    concerns: tuple[str, ...] = field(default_factory=tuple)
    coverage: float = 0.0
    metrics_used: int = 0

    #: SPEC 7. Present in the payload so no later phase can read a valuation
    #: as a trade instruction.
    is_recommendation: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 3),
            "reasons": list(self.reasons),
            "concerns": list(self.concerns),
            "coverage": round(self.coverage, 3),
            "metrics_used": self.metrics_used,
            "is_recommendation": False,
            "note": (
                "Valuation only. SPEC 7: undervalued is never an automatic BUY."
            ),
        }


class FundamentalEngine:
    """Scores reported fundamentals into a valuation verdict."""

    def evaluate(self, data: Fundamentals) -> ValuationReport:
        if not data.is_usable:
            return ValuationReport(
                symbol=data.symbol,
                verdict=ValuationVerdict.UNCERTAIN,
                confidence=0.0,
                reasons=(
                    f"only {len(data.available_metrics)} of "
                    f"{int(data.coverage * 100)}% metric coverage available",
                ),
                concerns=("insufficient fundamental data to value this company",),
                coverage=data.coverage,
            )

        # Valuation and quality are kept apart on purpose. A high-ROE company
        # can be expensive, and a shrinking one can be cheap. Counting ROE as
        # evidence of cheapness makes the engine call every good business
        # "undervalued", which is a different claim entirely.
        cheap: list[str] = []
        expensive: list[str] = []
        strengths: list[str] = []
        concerns: list[str] = []
        used = 0

        # ---- valuation: what price is being paid ---------------------------
        pe = data.price_to_earnings
        if pe is not None:
            used += 1
            if pe <= 0:
                concerns.append(f"negative earnings (P/E {pe:.1f})")
            elif pe < CHEAP_PE:
                cheap.append(f"P/E {pe:.1f} below {CHEAP_PE:.0f}")
            elif pe > EXPENSIVE_PE:
                expensive.append(f"P/E {pe:.1f} above {EXPENSIVE_PE:.0f}")

        pb = data.price_to_book
        if pb is not None:
            used += 1
            if pb < CHEAP_PB:
                cheap.append(f"P/B {pb:.2f} below book value")
            elif pb > EXPENSIVE_PB:
                expensive.append(f"P/B {pb:.2f} above {EXPENSIVE_PB:.0f}")

        # Yield is price-relative, so it does carry valuation information -
        # but at half weight, since a high yield is as often a falling price
        # as a cheap one.
        dividend = data.dividend_yield_pct
        if dividend is not None:
            used += 1
            if dividend >= GOOD_DIVIDEND:
                cheap.append(f"dividend yield {dividend:.2f}% (half weight)")

        # ---- quality: what kind of business it is --------------------------
        # These shape confidence and the narrative, never the cheap/expensive
        # tally.
        roe = data.roe_pct
        if roe is not None:
            used += 1
            if roe >= STRONG_ROE:
                strengths.append(f"ROE {roe:.1f}% is strong")
            elif roe < WEAK_ROE:
                concerns.append(f"ROE {roe:.1f}% is weak")

        if data.roa_pct is not None:
            used += 1
            if data.roa_pct < 1.0:
                concerns.append(f"ROA {data.roa_pct:.2f}% is thin")

        der = data.debt_to_equity
        if der is not None:
            used += 1
            if der > HIGH_DER:
                concerns.append(f"debt/equity {der:.2f} is high")

        growth = data.earnings_growth_pct
        if growth is not None:
            used += 1
            if growth < 0:
                concerns.append(f"earnings shrinking {growth:.1f}%")
            elif growth > 10:
                strengths.append(f"earnings growing {growth:.1f}%")

        if data.revenue_growth_pct is not None:
            used += 1
            if data.revenue_growth_pct < 0:
                concerns.append(f"revenue shrinking {data.revenue_growth_pct:.1f}%")

        weighted_cheap = sum(0.5 if "half weight" in item else 1.0 for item in cheap)
        verdict, confidence = self._decide(
            weighted_cheap, float(len(expensive)), data.coverage, used
        )
        return ValuationReport(
            symbol=data.symbol,
            verdict=verdict,
            confidence=confidence,
            reasons=tuple(cheap + expensive + strengths),
            concerns=tuple(concerns),
            coverage=data.coverage,
            metrics_used=used,
        )

    def _decide(
        self, cheap: float, expensive: float, coverage: float, used: int
    ) -> tuple[ValuationVerdict, float]:
        """Verdict from the *valuation* tally only.

        Requires a margin of 2 full-weight signals. With P/E, P/B and yield as
        the only inputs - and yield at half weight - that means at least two
        genuine valuation measures must agree before anything is called cheap
        or expensive.
        """
        if used < 3:
            return ValuationVerdict.UNCERTAIN, 0.0

        margin = cheap - expensive
        total = cheap + expensive

        if total == 0:
            # Nothing stood out either way; that is a real reading, not a gap.
            return ValuationVerdict.FAIR_VALUE, round(0.3 + coverage * 0.3, 3)

        agreement = abs(margin) / total
        confidence = round(min(1.0, coverage * 0.5 + agreement * 0.5), 3)

        if margin >= 2.0:
            return ValuationVerdict.UNDERVALUED, confidence
        if margin <= -2.0:
            return ValuationVerdict.OVERVALUED, confidence
        if margin == 0:
            # Signals cancel out. Saying FAIR_VALUE here would overstate what a
            # tie actually tells us.
            return ValuationVerdict.UNCERTAIN, round(confidence * 0.5, 3)
        return ValuationVerdict.FAIR_VALUE, round(confidence * 0.7, 3)


__all__ = ["FundamentalEngine", "ValuationReport"]
