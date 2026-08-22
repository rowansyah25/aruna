"""Market data records.

Every record carries :class:`Provenance`.  SPEC 4 requires the timestamp, the
provider's own timestamp, ARUNA's receive timestamp, the latency between them,
and the source - and requires all of it to be auditable later.  Making it a
mandatory field rather than optional columns means no code path can create a
price whose origin is unknown.

Prices are :class:`~decimal.Decimal` throughout.  Binary floats lose exactness
at both ends of the range ARUNA carries - a nine-figure IDX price and a
four-decimal USDT price like ``0.9984`` - and a paper-trading ledger that
disagrees with itself by a tick is a ledger nobody trusts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import DataQuality, Horizon, Market

#: Scale of the ``spread_bps`` and percentage columns.  Decimal division yields
#: 28 significant digits; rounding is done here, explicitly, rather than left to
#: MySQL to truncate on insert - a silent narrowing is exactly the kind of
#: quiet data change this system must not have.
_BPS_SCALE = Decimal("0.0001")
_PCT_SCALE = Decimal("0.000001")


def quantize_bps(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_BPS_SCALE, rounding=ROUND_HALF_EVEN)


def quantize_pct(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_PCT_SCALE, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a record came from and how long it took to arrive (SPEC 4)."""

    source: str
    server_timestamp: datetime = field(default_factory=now_utc)
    provider_timestamp: datetime | None = None
    #: Round-trip time of the request that produced this record.
    latency_ms: float | None = None
    #: Provider-declared feed delay, in seconds.  Non-zero means the value was
    #: already old when the provider published it - an equities feed, typically.
    declared_delay_sec: int = 0

    @property
    def is_realtime(self) -> bool:
        return self.declared_delay_sec == 0

    @property
    def provider_age_sec(self) -> float | None:
        """How stale the provider's own timestamp is, relative to receipt.

        Negative means the provider's clock is ahead of ARUNA's.
        """
        if self.provider_timestamp is None:
            return None
        return (self.server_timestamp - self.provider_timestamp).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "server_timestamp": isoformat(self.server_timestamp),
            "provider_timestamp": (
                isoformat(self.provider_timestamp) if self.provider_timestamp else None
            ),
            "latency_ms": self.latency_ms,
            "declared_delay_sec": self.declared_delay_sec,
        }


@dataclass(frozen=True, slots=True)
class Quote:
    """A price observation, with bid/ask when the venue publishes them."""

    market: Market
    symbol: str
    price: Decimal
    provenance: Provenance
    quantity: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_quantity: Decimal | None = None
    ask_quantity: Decimal | None = None

    @property
    def spread(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def spread_bps(self) -> Decimal | None:
        """Spread in basis points of the mid price.

        Basis points rather than absolute, because the same absolute spread
        means entirely different things across ARUNA's range: 0.01 is under
        0.002 bps on BTC/USDT near 63,000 and about 100 bps on XRP/USDT near
        1.00.  Only the ratio is comparable.
        """
        if self.bid is None or self.ask is None:
            return None
        mid = (self.bid + self.ask) / 2
        if mid <= 0:
            return None
        return quantize_bps((self.ask - self.bid) / mid * Decimal(10_000))

    @property
    def mid(self) -> Decimal | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV bar.

    ``is_closed`` is load-bearing: an open bar changes after it is read, so
    using one as settled evidence is a look-ahead bug (SPEC 24).
    """

    market: Market
    symbol: str
    interval: Horizon
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    provenance: Provenance
    quote_volume: Decimal | None = None
    trade_count: int | None = None
    is_closed: bool = True

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    @property
    def change_pct(self) -> Decimal | None:
        if self.open <= 0:
            return None
        return (self.close - self.open) / self.open * Decimal(100)

    @property
    def is_coherent(self) -> bool:
        """OHLC internally consistent.  A provider that fails this is broken."""
        return (
            self.high >= self.low
            and self.high >= self.open
            and self.high >= self.close
            and self.low <= self.open
            and self.low <= self.close
            and self.volume >= 0
            and self.close_time > self.open_time
        )


@dataclass(frozen=True, slots=True)
class OrderBookLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBook:
    market: Market
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    provenance: Provenance

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def bid_depth(self) -> Decimal:
        return sum((level.quantity for level in self.bids), Decimal(0))

    @property
    def ask_depth(self) -> Decimal:
        return sum((level.quantity for level in self.asks), Decimal(0))

    @property
    def imbalance(self) -> Decimal | None:
        """Bid depth as a share of total, in [0, 1].  0.5 is balanced."""
        total = self.bid_depth + self.ask_depth
        if total <= 0:
            return None
        return self.bid_depth / total

    @property
    def is_crossed(self) -> bool:
        """Best bid at or above best ask - a book that cannot be real."""
        if self.best_bid is None or self.best_ask is None:
            return False
        return self.best_bid >= self.best_ask


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Consolidated point-in-time view of one asset.

    ``session`` and ``market_open`` are carried so that nothing downstream has
    to guess whether the venue was trading (SPEC 3): an IDX snapshot taken at
    20:00 WIB is a last close, not a live price, and it says so.
    """

    market: Market
    symbol: str
    captured_at: datetime
    last_price: Decimal
    provenance: Provenance
    quality: DataQuality = DataQuality.OK
    quality_detail: str | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_bps: Decimal | None = None
    high_24h: Decimal | None = None
    low_24h: Decimal | None = None
    volume_24h: Decimal | None = None
    change_24h_pct: Decimal | None = None
    bid_depth: Decimal | None = None
    ask_depth: Decimal | None = None
    session: str | None = None
    market_open: bool | None = None
    raw: dict[str, Any] | None = None

    @property
    def is_realtime(self) -> bool:
        return self.provenance.is_realtime

    @property
    def tradeable(self) -> bool:
        """Whether a signal may be produced from this observation.

        False when the data failed a quality check (SPEC 5) or the venue is
        closed (SPEC 3).  ``market_open is None`` means the venue has no
        concept of closing - crypto.
        """
        if self.quality.blocks_signal:
            return False
        return self.market_open is not False

    def describe_freshness(self) -> str:
        """Human phrasing that never overstates the feed (SPEC 3, 5, 49)."""
        if not self.is_realtime:
            minutes = self.provenance.declared_delay_sec // 60
            return f"DELAYED ~{minutes}m" if minutes else "DELAYED"
        if self.market_open is False:
            return "MARKET CLOSED - last known price"
        return "REALTIME"


__all__ = [
    "Candle",
    "OrderBook",
    "OrderBookLevel",
    "Provenance",
    "Quote",
    "Snapshot",
]
