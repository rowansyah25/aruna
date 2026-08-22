"""Funding analysis (FUTURES SPEC 27).

Funding is the payment that keeps a perpetual anchored to spot: longs pay
shorts when the contract trades above the index, and the reverse below. It has
two entirely different meanings and conflating them is the mistake this module
is built to avoid.

**As a cost**, it is an unavoidable drag priced per interval. FUTURES SPEC 23
requires it inside expected net PnL, and a position held through several
settlements can pay more in funding than the move it was predicting.

**As a signal**, it is crowd positioning. Extreme positive funding says longs
are paying heavily to stay long - which is what a crowded trade looks like just
before it is squeezed. But the same reading also says the trend is strong. The
two readings point opposite ways, so this module reports *both* and refuses to
collapse them into one direction. That refusal is the point: a single number
that resolved the ambiguity would be inventing information.

Everything here is a projection from observations. The rate changes before it
settles, so a cost estimate is a forecast and is labelled as one.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.data.models import quantize_pct
from aruna.futures.models import FundingRate, PositionSide

#: Per-interval rate beyond which positioning counts as crowded. 0.05% per 8h
#: is roughly 55% annualised - a level that is expensive to hold and usually
#: means one side is paying a lot to stay there.
CROWDED_RATE = Decimal("0.0005")

#: Beyond this the cost dominates most short-horizon setups on its own.
EXTREME_RATE = Decimal("0.0015")

#: Observations needed before a trend or a mean means anything.
MIN_HISTORY = 8


class FundingBias(StrEnum):
    """Who is paying whom, and how hard."""

    LONGS_PAY_HEAVILY = "LONGS_PAY_HEAVILY"
    LONGS_PAY = "LONGS_PAY"
    NEUTRAL = "NEUTRAL"
    SHORTS_PAY = "SHORTS_PAY"
    SHORTS_PAY_HEAVILY = "SHORTS_PAY_HEAVILY"


class FundingTrend(StrEnum):
    RISING = "RISING"
    FALLING = "FALLING"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class FundingAnalysis:
    """What funding costs, and what it says about positioning."""

    symbol: str
    current_rate: Decimal
    bias: FundingBias
    trend: FundingTrend
    #: Mean of the settled history, when there is enough of it.
    mean_rate: Decimal | None = None
    observations: int = 0
    #: Cost of holding for the requested horizon, percent of notional.
    projected_cost_pct: Decimal | None = None
    horizon_hours: float | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sufficient_history(self) -> bool:
        return self.observations >= MIN_HISTORY

    @property
    def crowded(self) -> bool:
        return abs(self.current_rate) >= CROWDED_RATE

    @property
    def extreme(self) -> bool:
        return abs(self.current_rate) >= EXTREME_RATE

    def cost_favours(self) -> str:
        """Which side funding *pays*, as opposed to which side it favours.

        Deliberately not a direction. Positive funding makes shorts cheaper to
        hold, which is a cost argument; the same reading is also evidence that
        the trend is strong, which is a directional argument the other way.
        Returning a side here would silently pick one of the two.
        """
        if self.current_rate > 0:
            return "SHORT dibayar untuk menahan posisi; LONG yang membayar"
        if self.current_rate < 0:
            return "LONG dibayar untuk menahan posisi; SHORT yang membayar"
        return "tidak ada sisi yang membayar"

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "current_rate": str(self.current_rate),
            "current_rate_pct": str(quantize_pct(self.current_rate * 100)),
            "bias": self.bias.value,
            "trend": self.trend.value,
            "mean_rate": str(self.mean_rate) if self.mean_rate is not None else None,
            "observations": self.observations,
            "sufficient_history": self.sufficient_history,
            "crowded": self.crowded,
            "extreme": self.extreme,
            "projected_cost_pct": (
                str(self.projected_cost_pct)
                if self.projected_cost_pct is not None
                else None
            ),
            "horizon_hours": self.horizon_hours,
            "cost_side": self.cost_favours(),
            "findings": list(self.findings),
            "note": (
                "funding adalah biaya sekaligus sinyal positioning, dan "
                "keduanya menunjuk ke arah yang berlawanan - keduanya "
                "dilaporkan dan tidak ada yang diringkas jadi satu arah "
                "(FUTURES SPEC 27)"
            ),
        }


def analyse_funding(
    current: FundingRate,
    history: list[FundingRate] | None = None,
    *,
    horizon_hours: float | None = None,
    side: PositionSide | None = None,
    settlements: int | None = None,
) -> FundingAnalysis:
    """Read funding as both a cost and a crowding signal (FUTURES SPEC 27).

    ``side`` decides whether the projected figure is a cost or an income. The
    rate itself is a market convention with no side in it - a positive rate
    means longs pay shorts - so without a side this function could only report
    the magnitude, and it reported it as a cost to everybody. A short in
    positive funding was told the hold would cost it money it was in fact being
    paid, while the arithmetic elsewhere charged it correctly. The number and
    the sentence about the number disagreed.

    ``settlements`` makes the cost **discrete**, which is what funding actually
    is: it is paid at fixed settlement times, not accrued continuously. Without
    it the figure is prorated - a one-hour hold charged an eighth of an
    eight-hour payment - and one plan then carried two different funding costs,
    a prorated one in its reward-to-risk and a whole-settlement one in its
    horizon caveat. A one-hour hold that crosses no settlement pays nothing at
    all, and prorating invents a payment that never happens.
    """
    settled = [f for f in (history or []) if f.settled]
    rates = [f.rate for f in settled]
    findings: list[str] = []

    mean_rate = None
    trend = FundingTrend.UNKNOWN
    if len(rates) >= MIN_HISTORY:
        mean_rate = Decimal(str(statistics.fmean(float(r) for r in rates)))
        trend = _trend(rates)
    else:
        findings.append(
            f"{len(rates)} observasi yang sudah settlement; butuh "
            f"{MIN_HISTORY} sebelum trend atau rata-rata funding punya arti"
        )

    bias = _bias(current.rate)
    if settlements is not None:
        cost = quantize_pct(current.rate * Decimal(settlements) * 100)
    elif horizon_hours is not None:
        cost = current.cost_pct(horizon_hours)
    else:
        cost = None

    if cost is not None:
        # `cost` carries the market convention's sign, not the position's. A
        # long pays when the rate is positive; a short is paid.
        pays = side is None or (
            (side is PositionSide.LONG) == (current.rate >= 0)
        )
        span = (
            f"{settlements} kali settlement"
            if settlements is not None
            else f"{horizon_hours:g}h"
        )
        if side is None:
            findings.append(
                f"{span} menggerakkan sekitar {abs(cost):.4f}% dari notional "
                "lewat funding - ini proyeksi, karena rate-nya bergerak "
                "sebelum settlement. Tidak ada side yang diberikan, jadi "
                "apakah itu dibayar atau diterima tidak dinyatakan di sini"
            )
        elif pays:
            findings.append(
                f"menahan posisi melewati {span} membebani sisi ini sekitar "
                f"{abs(cost):.4f}% dari notional pada rate saat ini - ini "
                "proyeksi, karena rate-nya bergerak sebelum settlement"
            )
        else:
            findings.append(
                f"menahan posisi melewati {span} membayar sisi ini sekitar "
                f"{abs(cost):.4f}% dari notional pada rate saat ini - ini "
                "pemasukan, bukan biaya, dan hanya selama rate-nya tidak "
                "berganti tanda"
            )

    if abs(current.rate) >= EXTREME_RATE:
        findings.append(
            f"funding ekstrem di {current.rate * 100:.4f}% per interval: "
            "biayanya saja bisa melebihi pergerakan yang diprediksi kebanyakan "
            "horizon pendek"
        )
    elif abs(current.rate) >= CROWDED_RATE:
        findings.append(
            f"funding sudah padat di {current.rate * 100:.4f}% per interval - "
            "satu sisi membayar mahal untuk tetap bertahan, dan itu terbaca "
            "sebagai kekuatan trend sekaligus risiko squeeze"
        )

    if mean_rate is not None and abs(current.rate) > abs(mean_rate) * 2 and abs(mean_rate) > 0:
        findings.append(
            f"rate saat ini lebih dari dua kali lipat rata-rata terkininya "
            f"({current.rate * 100:.4f}% vs {mean_rate * 100:.4f}%)"
        )

    return FundingAnalysis(
        symbol=current.symbol,
        current_rate=current.rate,
        bias=bias,
        trend=trend,
        mean_rate=mean_rate,
        observations=len(rates),
        projected_cost_pct=cost,
        horizon_hours=horizon_hours,
        findings=tuple(findings),
    )


def _bias(rate: Decimal) -> FundingBias:
    if rate >= EXTREME_RATE:
        return FundingBias.LONGS_PAY_HEAVILY
    if rate >= CROWDED_RATE:
        return FundingBias.LONGS_PAY
    if rate <= -EXTREME_RATE:
        return FundingBias.SHORTS_PAY_HEAVILY
    if rate <= -CROWDED_RATE:
        return FundingBias.SHORTS_PAY
    return FundingBias.NEUTRAL


def _trend(rates: list[Decimal]) -> FundingTrend:
    """Direction of the recent half against the earlier half.

    Two halves rather than a fitted slope: funding is published a few times a
    day, so a regression over a dozen points would put a confident-looking line
    through very little data.
    """
    half = len(rates) // 2
    earlier = statistics.fmean(float(r) for r in rates[:half])
    recent = statistics.fmean(float(r) for r in rates[half:])
    delta = recent - earlier
    if abs(delta) < float(CROWDED_RATE) / 4:
        return FundingTrend.FLAT
    return FundingTrend.RISING if delta > 0 else FundingTrend.FALLING


__all__ = [
    "CROWDED_RATE",
    "EXTREME_RATE",
    "MIN_HISTORY",
    "FundingAnalysis",
    "FundingBias",
    "FundingTrend",
    "analyse_funding",
]
