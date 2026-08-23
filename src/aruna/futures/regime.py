"""Futures market regime (FUTURES SPEC 6).

Spot already classifies trend, range, breakout, reversal and volatility, and
that work is reused unchanged - a regime read on price should not disagree with
itself depending on which module asked.

Two regimes exist only in derivatives, and both are the reason a leveraged
position dies in ways a spot position does not:

* **LIQUIDATION_DRIVEN** - the move is forced closes, not opinions. Price is
  travelling because positions are being closed at any price available, and
  liquidity behind it is thinner than the book suggests.
* **FAKE_BREAKOUT** - price left the range and the flow did not follow. Spot
  can see the price part; only open interest can see that nobody funded it.

Both are *overrides*. When either is present it replaces the spot read rather
than sitting alongside it, because a trader who is told "TRENDING" during a
liquidation cascade has been told the least useful true thing available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.core.enums import Regime
from aruna.futures.funding import FundingAnalysis
from aruna.futures.liquidation import CASCADE_SHARE as _CASCADE_SHARE
from aruna.futures.openinterest import OpenInterestAnalysis

#: Liquidation notional, as a share of open interest, that marks a move as
#: forced rather than chosen.
#:
#: **Didefinisikan di :mod:`aruna.futures.liquidation`, bukan di sini.** Sampai
#: 2026-08-23 kedua modul mendefinisikannya sendiri-sendiri dengan nilai yang
#: sama, dan dua angka yang HARUS sepakat tapi ditulis di dua tempat adalah dua
#: angka yang suatu saat tidak sepakat - tanpa satu pun test merah, karena
#: masing-masing menguji miliknya sendiri.
#:
#: Rumahnya di modul likuidasi: di sanalah FUTURES SPEC 26 mendefinisikan
#: kaskade, dan modul ini memakainya untuk membaca sebuah gerak sebagai paksa.
#: Di-re-export supaya import lama tetap jalan - pola yang sama dengan
#: :data:`~aruna.futures.plan.FORBIDDEN_CLAIMS`.
CASCADE_SHARE = _CASCADE_SHARE

#: A breakout with OI change smaller than this was not funded by anyone.
UNFUNDED_OI_PCT = Decimal("0.3")


class FuturesRegime(StrEnum):
    """The spot vocabulary, plus what only derivatives can show."""

    TRENDING = "TRENDING"
    RANGING = "RANGING"
    BREAKOUT = "BREAKOUT"
    FAKE_BREAKOUT = "FAKE_BREAKOUT"
    REVERSAL = "REVERSAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    NEWS_DRIVEN = "NEWS_DRIVEN"
    LIQUIDATION_DRIVEN = "LIQUIDATION_DRIVEN"
    UNCERTAIN = "UNCERTAIN"


#: Regimes in which a leveraged position is exposed to more than its own thesis.
#: Not a ban - FUTURES SPEC 6 feeds regime to the leverage engine, and this is
#: what it reads.
HOSTILE: frozenset[FuturesRegime] = frozenset(
    {
        FuturesRegime.LIQUIDATION_DRIVEN,
        FuturesRegime.FAKE_BREAKOUT,
        FuturesRegime.NEWS_DRIVEN,
        FuturesRegime.HIGH_VOLATILITY,
    }
)

_FROM_SPOT: dict[Regime, FuturesRegime] = {
    Regime.TRENDING: FuturesRegime.TRENDING,
    Regime.RANGING: FuturesRegime.RANGING,
    Regime.BREAKOUT: FuturesRegime.BREAKOUT,
    Regime.REVERSAL: FuturesRegime.REVERSAL,
    Regime.HIGH_VOLATILITY: FuturesRegime.HIGH_VOLATILITY,
    Regime.LOW_VOLATILITY: FuturesRegime.LOW_VOLATILITY,
    Regime.NEWS_SHOCK: FuturesRegime.NEWS_DRIVEN,
}


@dataclass(frozen=True, slots=True)
class FuturesRegimeVerdict:
    regime: FuturesRegime
    confidence: float
    #: What the price-only classifier said, kept so an override is visible.
    spot_regime: Regime | None = None
    overrode_spot: bool = False
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def hostile(self) -> bool:
        return self.regime in HOSTILE

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 3),
            "spot_regime": self.spot_regime.value if self.spot_regime else None,
            "overrode_spot": self.overrode_spot,
            "hostile_to_leverage": self.hostile,
            "findings": list(self.findings),
            "note": (
                "liquidation cascade atau breakout yang tidak didanai "
                "menggantikan pembacaan berbasis harga saja, bukan berdiri di "
                "sampingnya: 'TRENDING' saat cascade adalah hal benar yang "
                "paling tidak berguna untuk dikatakan (FUTURES SPEC 6)"
            ),
        }


def classify_futures_regime(
    *,
    spot_regime: Regime | None,
    spot_confidence: float = 0.0,
    open_interest: OpenInterestAnalysis | None = None,
    funding: FundingAnalysis | None = None,
    liquidation_notional: Decimal | None = None,
    total_open_interest: Decimal | None = None,
) -> FuturesRegimeVerdict:
    """Classify the regime a leveraged decision is being made in.

    Every derivatives input is optional, and a missing one narrows the answer
    rather than being assumed. With none of them this degrades to the spot read,
    which is honest: without funding, OI or liquidation data there is nothing a
    perpetual can see that a spot chart cannot.
    """
    findings: list[str] = []
    base = (
        _FROM_SPOT.get(spot_regime, FuturesRegime.UNCERTAIN)
        if spot_regime
        else FuturesRegime.UNCERTAIN
    )

    cascade = _cascade_share(liquidation_notional, total_open_interest)
    if cascade is not None and cascade >= CASCADE_SHARE:
        findings.append(
            f"penutupan paksa mencapai {cascade:.1%} dari open interest: "
            "pergerakan ini adalah posisi yang ditutup di harga berapa pun yang "
            "tersedia, bukan pandangan yang sedang disampaikan"
        )
        return FuturesRegimeVerdict(
            regime=FuturesRegime.LIQUIDATION_DRIVEN,
            confidence=min(0.95, 0.5 + float(cascade) * 10),
            spot_regime=spot_regime,
            overrode_spot=base is not FuturesRegime.LIQUIDATION_DRIVEN,
            findings=tuple(findings),
        )

    if base is FuturesRegime.BREAKOUT and open_interest is not None:
        unfunded = (
            open_interest.oi_change_pct is not None
            and abs(open_interest.oi_change_pct) < UNFUNDED_OI_PCT
        )
        if unfunded or open_interest.is_exhaustion:
            flow_text = open_interest.flow.value.lower().replace("_", " ")
            reason = (
                "open interest nyaris tidak bergerak"
                if unfunded
                else f"pergerakannya {flow_text}"
            )
            findings.append(
                f"harga menembus range tapi {reason} - tidak ada yang "
                "mendanainya, dan breakout yang tidak dimasuki uang baru "
                "cenderung tidak bertahan"
            )
            return FuturesRegimeVerdict(
                regime=FuturesRegime.FAKE_BREAKOUT,
                confidence=min(0.9, spot_confidence + 0.15),
                spot_regime=spot_regime,
                overrode_spot=True,
                findings=tuple(findings),
            )

    if funding is not None and funding.extreme:
        findings.append(
            f"funding ekstrem ({funding.bias.value}): posisi sudah terlalu "
            "ramai, dan trade yang ramai bubar lebih cepat daripada saat "
            "terbentuk"
        )

    if open_interest is not None and open_interest.readable:
        findings.append(
            f"flow terbaca {open_interest.flow.value.lower().replace('_', ' ')}"
        )

    if spot_regime is None:
        findings.append(
            "tidak ada regime berbasis harga yang diberikan, dan data "
            "derivatif sendirian tidak bisa mengklasifikasikannya"
        )

    if not any((open_interest, funding, liquidation_notional)):
        findings.append(
            "tidak ada data funding, open interest, atau liquidation: ini "
            "pembacaan spot, dan perpetual tidak melihat apa pun tambahan "
            "tanpa data itu"
        )

    return FuturesRegimeVerdict(
        regime=base,
        confidence=spot_confidence,
        spot_regime=spot_regime,
        overrode_spot=False,
        findings=tuple(findings),
    )


def _cascade_share(
    liquidation_notional: Decimal | None, total_open_interest: Decimal | None
) -> Decimal | None:
    if (
        liquidation_notional is None
        or total_open_interest is None
        or total_open_interest <= 0
    ):
        return None
    return liquidation_notional / total_open_interest


__all__ = [
    "CASCADE_SHARE",
    "HOSTILE",
    "UNFUNDED_OI_PCT",
    "FuturesRegime",
    "FuturesRegimeVerdict",
    "classify_futures_regime",
]
