"""Order book and liquidity (FUTURES SPEC 29, 30).

Spot needed the top of book to price a spread. A leveraged position needs the
*depth*, because position size is chosen before the fill and a size that eats
four levels pays a very different price from one that rests on the touch.

The figure that matters most here is :meth:`OrderBookDepth.sweep_cost_pct` -
what it would actually cost to fill a given notional right now. FUTURES SPEC 30
wants that stress-tested, and it cannot be stress-tested from a spread alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.data.models import quantize_bps, quantize_pct

#: Levels to request. Enough to price a retail-sized sweep without pulling a
#: book so deep that the request itself becomes slow.
DEPTH_LEVELS = 50

#: An imbalance beyond this is worth reporting: one side of the book is much
#: thinner, and a fill against the thin side moves price further.
IMBALANCE_ALARM = Decimal("0.65")


@dataclass(frozen=True, slots=True)
class OrderBookDepth:
    """Bids and asks at one instant, with what a fill would cost."""

    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]
    asks: tuple[tuple[Decimal, Decimal], ...]
    as_of: datetime
    provenance: Any = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> Decimal | None:
        mid = self.mid
        if mid is None or mid <= 0:
            return None
        return quantize_bps((self.best_ask - self.best_bid) / mid * 10_000)

    @property
    def bid_notional(self) -> Decimal:
        return sum((p * q for p, q in self.bids), Decimal(0))

    @property
    def ask_notional(self) -> Decimal:
        return sum((p * q for p, q in self.asks), Decimal(0))

    @property
    def imbalance(self) -> Decimal | None:
        """Bid share of visible notional. 0.5 is balanced.

        Visible only: an order book shows resting limit orders, not intent.
        A thin side can fill instantly from hidden liquidity, and a thick one
        can vanish before an order reaches it.
        """
        total = self.bid_notional + self.ask_notional
        if total <= 0:
            return None
        return quantize_pct(self.bid_notional / total)

    def sweep_cost_pct(self, notional: Decimal, *, buying: bool) -> Decimal | None:
        """What filling ``notional`` would cost against the mid, in percent.

        Walks the book level by level. Returns ``None`` when the visible book
        cannot fill the order at all - which is the honest answer, and a much
        more useful one than a number extrapolated past the last level.
        """
        mid = self.mid
        if mid is None or mid <= 0 or notional <= 0:
            return None

        levels = self.asks if buying else self.bids
        remaining = notional
        paid = Decimal(0)
        for price, quantity in levels:
            available = price * quantity
            take = min(remaining, available)
            paid += take * price
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            return None

        average = paid / notional
        cost = (average - mid) / mid * 100
        return quantize_pct(cost if buying else -cost)

    def can_fill(self, notional: Decimal, *, buying: bool) -> bool:
        return self.sweep_cost_pct(notional, buying=buying) is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "as_of": isoformat(self.as_of),
            "levels": len(self.bids),
            "best_bid": str(self.best_bid) if self.best_bid else None,
            "best_ask": str(self.best_ask) if self.best_ask else None,
            "spread_bps": str(self.spread_bps) if self.spread_bps is not None else None,
            "imbalance": str(self.imbalance) if self.imbalance is not None else None,
            "bid_notional": str(self.bid_notional),
            "ask_notional": str(self.ask_notional),
            "note": (
                "hanya order yang terlihat menggantung di book; hidden "
                "liquidity dan depth yang dibatalkan tidak masuk di sini"
            ),
        }


@dataclass(frozen=True, slots=True)
class LiquidityVerdict:
    """Whether a book can carry a position (FUTURES SPEC 29)."""

    tradeable: bool
    sweep_cost_pct: Decimal | None
    spread_bps: Decimal | None
    imbalance: Decimal | None
    findings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tradeable": self.tradeable,
            "sweep_cost_pct": (
                str(self.sweep_cost_pct) if self.sweep_cost_pct is not None else None
            ),
            "spread_bps": str(self.spread_bps) if self.spread_bps is not None else None,
            "imbalance": str(self.imbalance) if self.imbalance is not None else None,
            "findings": list(self.findings),
        }


def assess_liquidity(
    book: OrderBookDepth,
    notional: Decimal,
    *,
    buying: bool,
    max_sweep_pct: Decimal = Decimal("0.15"),
) -> LiquidityVerdict:
    """Can this book carry this size, and at what cost?"""
    findings: list[str] = []
    sweep = book.sweep_cost_pct(notional, buying=buying)
    spread = book.spread_bps
    imbalance = book.imbalance

    if sweep is None:
        findings.append(
            f"order book yang terlihat sama sekali tidak bisa mengisi "
            f"{notional} - ordernya akan berjalan melewati level terakhir, dan "
            "apa yang terjadi setelah itu tidak diketahui"
        )
        return LiquidityVerdict(False, None, spread, imbalance, tuple(findings))

    tradeable = True
    if sweep > max_sweep_pct:
        tradeable = False
        findings.append(
            f"mengisi size ini memakan {sweep:.3f}% terhadap mid, di atas "
            f"batas {max_sweep_pct}% - size-nya menggerakkan harga yang justru "
            "mau ditransaksikannya"
        )
    if imbalance is not None and (
        imbalance > IMBALANCE_ALARM or imbalance < (1 - IMBALANCE_ALARM)
    ):
        side = "bid" if imbalance > Decimal("0.5") else "ask"
        findings.append(
            f"book condong ke sisi {side} sebesar {imbalance:.0%}; fill "
            "melawan sisi yang tipis akan bergerak lebih jauh daripada yang "
            "ditunjukkan sweep cost"
        )
    return LiquidityVerdict(tradeable, sweep, spread, imbalance, tuple(findings))


async def fetch_order_book(provider: Any, symbol: str) -> OrderBookDepth:
    """Pull the depth this module needs from a futures provider.

    Stamped from the **venue's** clock (``T``, the transaction time, else ``E``,
    the event time), because every other input in a snapshot is. Using
    ``now_utc()`` here mixed two clocks in one coherence check: the venue/local
    offset landed straight in the skew figure, so a 35-second-slow local clock
    reported ``SKEWED`` - "these inputs do not describe the same moment" - about
    six requests issued two seconds apart. Worse, it made a band of the tolerance
    unreachable: any offset between roughly 32s and the 60s
    :data:`~aruna.futures.integrity.MAX_CLOCK_LEAD_SEC` tripped ``SKEWED``
    before ``CLOCK_SKEW`` could ever fire, so half the range that constant
    declares tolerable could not produce an OK verdict.

    Stamping from the payload also makes venue-side lag visible instead of
    invisible: a depth body served from a stale cache now reports its real age
    rather than an age of zero.
    """
    data = await provider._get(  # noqa: SLF001 - the allowlisted transport
        "/fapi/v1/depth", symbol=symbol, limit=DEPTH_LEVELS
    )
    venue_time = data.get("T") or data.get("E")
    stamped = (
        datetime.fromtimestamp(int(venue_time) / 1000, tz=UTC)
        if venue_time is not None
        else now_utc()
    )
    return OrderBookDepth(
        symbol=symbol,
        bids=tuple(
            (Decimal(str(p)), Decimal(str(q))) for p, q in data.get("bids", [])
        ),
        asks=tuple(
            (Decimal(str(p)), Decimal(str(q))) for p, q in data.get("asks", [])
        ),
        as_of=stamped,
    )


__all__ = [
    "DEPTH_LEVELS",
    "IMBALANCE_ALARM",
    "LiquidityVerdict",
    "OrderBookDepth",
    "assess_liquidity",
    "fetch_order_book",
]
