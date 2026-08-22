"""Open interest analysis (FUTURES SPEC 28).

Open interest is how much position is open, not how much traded. Rising OI
means positions are being *created*; falling OI means they are being closed.
Neither is directional on its own, which is why FUTURES SPEC 28 says plainly:
**OI tidak boleh digunakan sendirian** - OI must not be used alone.

That is enforced here rather than trusted. :func:`analyse_open_interest`
requires a price change alongside the OI change, and the four combinations mean
four different things:

===================  ==========================================================
price up, OI up      new money going long - the move is being funded
price up, OI down    shorts covering - the move is closing positions, not
                     opening them, so it runs out when they are done
price down, OI up    new money going short - the move is being funded
price down, OI down  longs liquidating out - same exhaustion logic in reverse
===================  ==========================================================

The distinction that matters for a leveraged system is the middle two: a rally
on falling OI and a rally on rising OI look identical on a price chart and have
opposite implications for what happens next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.data.models import quantize_pct
from aruna.futures.models import OpenInterest

#: Change below this is noise in both series.
NOISE_PCT = Decimal("0.15")

#: An OI move beyond this is a genuine shift in participation.
SIGNIFICANT_PCT = Decimal("1.0")


class FlowRead(StrEnum):
    """What price and OI together say about the flow behind a move."""

    NEW_LONGS = "NEW_LONGS"
    SHORT_COVERING = "SHORT_COVERING"
    NEW_SHORTS = "NEW_SHORTS"
    LONG_LIQUIDATION = "LONG_LIQUIDATION"
    #: Neither series moved enough to read.
    QUIET = "QUIET"
    #: Price moved and OI did not, or the reverse - no coherent flow story.
    INCONCLUSIVE = "INCONCLUSIVE"


#: Whether the flow is being funded by new positions or unwound by closing
#: ones. Continuation reads are *not* directional claims - a move funded by new
#: shorts is well-supported and can still reverse.
CONTINUATION: frozenset[FlowRead] = frozenset(
    {FlowRead.NEW_LONGS, FlowRead.NEW_SHORTS}
)
EXHAUSTION: frozenset[FlowRead] = frozenset(
    {FlowRead.SHORT_COVERING, FlowRead.LONG_LIQUIDATION}
)


@dataclass(frozen=True, slots=True)
class OpenInterestAnalysis:
    symbol: str
    flow: FlowRead
    oi_change_pct: Decimal | None
    price_change_pct: Decimal | None
    current_oi: Decimal | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_continuation(self) -> bool:
        return self.flow in CONTINUATION

    @property
    def is_exhaustion(self) -> bool:
        return self.flow in EXHAUSTION

    @property
    def readable(self) -> bool:
        return self.flow not in (FlowRead.QUIET, FlowRead.INCONCLUSIVE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "flow": self.flow.value,
            "oi_change_pct": (
                str(self.oi_change_pct) if self.oi_change_pct is not None else None
            ),
            "price_change_pct": (
                str(self.price_change_pct)
                if self.price_change_pct is not None
                else None
            ),
            "current_oi": str(self.current_oi) if self.current_oi else None,
            "continuation": self.is_continuation,
            "exhaustion": self.is_exhaustion,
            "readable": self.readable,
            "findings": list(self.findings),
            "note": (
                "open interest tidak punya arah kalau berdiri sendiri; ia hanya "
                "dibaca terhadap perubahan harga, dan pergerakan yang didanai "
                "dengan baik pun tetap bisa berbalik (FUTURES SPEC 28)"
            ),
        }


def analyse_open_interest(
    current: OpenInterest,
    earlier: OpenInterest | None,
    price_change_pct: Decimal | None,
) -> OpenInterestAnalysis:
    """Read OI against price. Refuses to read it alone (FUTURES SPEC 28)."""
    findings: list[str] = []
    oi_change = current.change_pct(earlier)

    if oi_change is None:
        findings.append(
            "tidak ada pembacaan open interest sebelumnya sebagai pembanding - "
            "satu observasi tunggal tidak mengatakan apa pun soal apakah posisi "
            "sedang dibuka atau ditutup"
        )
        return OpenInterestAnalysis(
            symbol=current.symbol,
            flow=FlowRead.INCONCLUSIVE,
            oi_change_pct=None,
            price_change_pct=price_change_pct,
            current_oi=current.open_interest,
            findings=tuple(findings),
        )

    if price_change_pct is None:
        findings.append(
            "open interest bergerak tetapi tidak ada perubahan harga yang "
            "diberikan. OI sendirian tidak bisa mengatakan apakah uang masuk ke "
            "long atau ke short, jadi tidak ada flow yang dilaporkan "
            "(FUTURES SPEC 28)"
        )
        return OpenInterestAnalysis(
            symbol=current.symbol,
            flow=FlowRead.INCONCLUSIVE,
            oi_change_pct=oi_change,
            price_change_pct=None,
            current_oi=current.open_interest,
            findings=tuple(findings),
        )

    price_quiet = abs(price_change_pct) < NOISE_PCT
    oi_quiet = abs(oi_change) < NOISE_PCT

    if price_quiet and oi_quiet:
        findings.append(
            f"harga {price_change_pct:+.2f}% dan OI {oi_change:+.2f}% dua-duanya "
            "masih di dalam noise band; tidak ada yang bisa dibaca"
        )
        flow = FlowRead.QUIET
    elif price_quiet or oi_quiet:
        moved, still = (
            ("harga", "open interest") if oi_quiet else ("open interest", "harga")
        )
        findings.append(
            f"{moved} bergerak dan {still} tidak - keduanya tidak membentuk "
            "cerita flow, jadi tidak ada pembacaan yang diberikan"
        )
        flow = FlowRead.INCONCLUSIVE
    elif price_change_pct > 0:
        flow = FlowRead.NEW_LONGS if oi_change > 0 else FlowRead.SHORT_COVERING
    else:
        flow = FlowRead.NEW_SHORTS if oi_change > 0 else FlowRead.LONG_LIQUIDATION

    if flow in CONTINUATION:
        findings.append(
            f"harga {price_change_pct:+.2f}% dengan OI {oi_change:+.2f}%: posisi "
            "baru sedang mendanai pergerakan ini, jadi ada komitmen segar di "
            "belakangnya - dan itu bukan janji bahwa pergerakannya berlanjut"
        )
    elif flow in EXHAUSTION:
        closing = (
            "posisi short yang ditutup"
            if flow is FlowRead.SHORT_COVERING
            else "posisi long yang keluar"
        )
        findings.append(
            f"harga {price_change_pct:+.2f}% dengan OI {oi_change:+.2f}%: "
            f"pergerakan ini adalah {closing}, bukan uang baru. Pergerakannya "
            "habis begitu mereka selesai"
        )

    if abs(oi_change) >= SIGNIFICANT_PCT:
        findings.append(
            f"open interest bergerak {oi_change:+.2f}% - pergeseran nyata pada "
            "seberapa banyak posisi yang terbuka, bukan sekadar gerak pelan"
        )

    return OpenInterestAnalysis(
        symbol=current.symbol,
        flow=flow,
        oi_change_pct=oi_change,
        price_change_pct=quantize_pct(price_change_pct),
        current_oi=current.open_interest,
        findings=tuple(findings),
    )


__all__ = [
    "CONTINUATION",
    "EXHAUSTION",
    "NOISE_PCT",
    "SIGNIFICANT_PCT",
    "FlowRead",
    "OpenInterestAnalysis",
    "analyse_open_interest",
]
