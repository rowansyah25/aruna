"""Everything the agents are allowed to see.

One object, assembled once, passed to every agent. That matters for two
reasons:

* **SPEC 24.** The context is built from data at or before ``as_of`` and is
  frozen. An agent physically cannot reach for a later price, because there is
  no later price in the object it was handed.
* **SPEC 17.** Because every agent draws from the same declared pool, the judge
  can tell which of them were actually looking at different things.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.analysis.correlation import CorrelationMatrix
from aruna.analysis.engine import TechnicalSnapshot
from aruna.core.clock import isoformat
from aruna.core.enums import Horizon, Market
from aruna.fundamental.engine import ValuationReport
from aruna.fundamental.models import Fundamentals
from aruna.news.models import NewsItem


@dataclass(frozen=True, slots=True)
class MarketState:
    """Price and venue conditions at decision time."""

    last_price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_bps: Decimal | None = None
    bid_depth: Decimal | None = None
    ask_depth: Decimal | None = None
    volume_24h: Decimal | None = None
    change_24h_pct: Decimal | None = None
    session: str | None = None
    market_open: bool | None = None
    is_realtime: bool = True
    declared_delay_sec: int = 0
    data_quality: str = "OK"
    quality_detail: str | None = None
    source: str = ""

    @property
    def tradeable(self) -> bool:
        """Whether the venue is in a state where a signal could be acted on."""
        if self.data_quality != "OK":
            return False
        return self.market_open is not False


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """The complete, frozen evidence pool for one decision."""

    market: Market
    symbol: str
    interval: Horizon
    #: Newest settled bar behind this context. Nothing here postdates it.
    as_of: datetime
    state: MarketState
    technical: TechnicalSnapshot | None = None
    news: tuple[NewsItem, ...] = field(default_factory=tuple)
    fundamentals: Fundamentals | None = None
    valuation: ValuationReport | None = None
    correlation: CorrelationMatrix | None = None
    #: Set when the operator has engaged the kill switch (SPEC 40).
    trading_allowed: bool = True
    #: Strategi yang sejarah sarankan untuk keadaan ini, atau alasan kenapa
    #: tidak ada (PASAL 12.6).
    #:
    #: **Bukti, bukan perintah.** Ia masuk ke kolam bukti yang sama dengan
    #: indikator dan berita, dan agent boleh membantahnya seperti membantah
    #: yang lain. Itu perbedaan yang menentukan: sebuah bobot yang salah tidak
    #: bisa dibantah siapa pun, sebuah bukti yang salah bisa - dan council ini
    #: memang dibangun untuk membantah bukti.
    #:
    #: ``None`` berarti pemilihnya tidak dirangkai sama sekali. Sebuah
    #: :class:`~aruna.learning.selection.Selection` yang abstain BUKAN None -
    #: ia keterangan yang berbunyi "sejarah belum bisa menyarankan apa pun",
    #: dan itu berbeda dari "tidak ada yang bertanya".
    strategy: Any = None

    # ---- convenience accessors ------------------------------------------

    def reading(self, name: str):
        return self.technical.reading(name) if self.technical else None

    def value(self, name: str) -> float | None:
        return self.technical.value(name) if self.technical else None

    @property
    def regime(self):
        return self.technical.regime if self.technical else None

    @property
    def structure(self):
        return self.technical.structure if self.technical else None

    @property
    def has_technical(self) -> bool:
        return self.technical is not None and self.technical.reliable_count > 0

    def recent_news(self, *, hours: int = 24) -> tuple[NewsItem, ...]:
        """News published within the window, ordered newest first.

        Items with no publish time are excluded rather than assumed recent -
        an undated story could be from last year.
        """
        cutoff = self.as_of.timestamp() - hours * 3600
        dated = [
            item
            for item in self.news
            if item.published_at is not None
            and item.published_at.timestamp() >= cutoff
            # SPEC 24: news published after the decision point is future data.
            and item.published_at <= self.as_of
        ]
        return tuple(sorted(dated, key=lambda i: i.published_at, reverse=True))

    def describe(self) -> dict[str, Any]:
        return {
            "market": self.market.value,
            "symbol": self.symbol,
            "interval": self.interval.value,
            "as_of": isoformat(self.as_of),
            "last_price": str(self.state.last_price),
            "data_quality": self.state.data_quality,
            "market_open": self.state.market_open,
            "is_realtime": self.state.is_realtime,
            "has_technical": self.has_technical,
            "news_items": len(self.news),
            "has_fundamentals": self.fundamentals is not None,
            "trading_allowed": self.trading_allowed,
        }


__all__ = ["DecisionContext", "MarketState"]
