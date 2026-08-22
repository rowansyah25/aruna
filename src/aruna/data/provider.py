"""Provider interface.

SPEC 4 wants providers separated by concern; SPEC 47 wants the source and its
regulatory standing auditable. Both land in :class:`ProviderCapabilities`,
which every adapter must declare truthfully:

* ``is_realtime`` / ``expected_delay_sec`` - so nothing downstream can call a
  delayed equities feed "realtime" (SPEC 3, 5).
* ``transport`` - POLL is not STREAM, and a system that says "streaming" while
  polling every 5 seconds is lying about its own latency.
* ``supported_intervals`` - what the provider genuinely returns. A horizon that
  is absent is reported unavailable, never synthesised.
* ``regulatory_note`` - recorded for audit (SPEC 47).

An adapter must not soften any of these to look more capable.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from aruna.core.enums import Horizon, Market
from aruna.data.models import Candle, OrderBook, Quote, Snapshot


class Transport(StrEnum):
    STREAM = "STREAM"
    POLL = "POLL"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    name: str
    market: Market
    transport: Transport
    is_realtime: bool
    expected_delay_sec: int
    supports_order_book: bool
    supported_intervals: tuple[Horizon, ...]
    max_candles_per_request: int
    requires_credentials: bool
    #: Licensing / regulatory standing, stored with ingested data for audit.
    regulatory_note: str
    #: Anything an operator should know that the flags above do not capture.
    limitations: tuple[str, ...] = ()

    def supports(self, interval: Horizon) -> bool:
        return interval in self.supported_intervals

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "market": self.market.value,
            "transport": self.transport.value,
            "realtime": self.is_realtime,
            "expected_delay_sec": self.expected_delay_sec,
            "order_book": self.supports_order_book,
            "intervals": [i.value for i in self.supported_intervals],
            "requires_credentials": self.requires_credentials,
            "regulatory_note": self.regulatory_note,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    reachable: bool
    latency_ms: float | None = None
    detail: str = ""
    server_time: datetime | None = None


class MarketDataProvider(abc.ABC):
    """One venue or feed, for exactly one market.

    Implementations raise :class:`~aruna.core.errors.DataSourceUnavailableError`
    when they cannot answer, and never return a fabricated or interpolated
    value in its place (SPEC 4).
    """

    @property
    @abc.abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @property
    def name(self) -> str:
        return self.capabilities.name

    @property
    def market(self) -> Market:
        return self.capabilities.market

    # ---- lifecycle ------------------------------------------------------
    # Not abstract: a provider with nothing to open should not be forced to
    # write an empty override.

    async def open(self) -> None:  # noqa: B027
        """Acquire connections.  Safe to call more than once."""

    async def close(self) -> None:  # noqa: B027
        """Release connections.  Safe to call more than once."""

    @abc.abstractmethod
    async def status(self) -> ProviderStatus:
        """Reachability probe for the health monitor."""

    # ---- symbols --------------------------------------------------------

    @abc.abstractmethod
    def provider_symbol(self, symbol: str) -> str:
        """ARUNA's canonical symbol to the provider's own form.

        ``BTC/USDT`` becomes ``BTCUSDT`` on Binance spot and ``BBCA`` becomes
        ``BBCA.JK`` on Yahoo.  Keeping this in the adapter means the rest of
        ARUNA only ever sees canonical symbols.

        Case matters to the venue, not just to the reader: Binance's REST API
        rejects ``btcusdt``.  An adapter that lower-cases here would fail every
        request while looking like a formatting preference.
        """

    # ---- data -----------------------------------------------------------

    @abc.abstractmethod
    async def fetch_quote(self, symbol: str) -> Quote:
        """Latest price, with bid/ask when the venue publishes them."""

    @abc.abstractmethod
    async def fetch_candles(
        self,
        symbol: str,
        interval: Horizon,
        *,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Closed bars, oldest first.

        Raises ``ValueError`` for an interval the provider does not support -
        resampling one interval into another is the caller's decision to make
        explicitly, not something an adapter should do silently.
        """

    async def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook | None:
        """Order book, or None when the provider has none."""
        return None

    @abc.abstractmethod
    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        """Consolidated view, including session state and freshness."""


__all__ = [
    "MarketDataProvider",
    "ProviderCapabilities",
    "ProviderStatus",
    "Transport",
]
