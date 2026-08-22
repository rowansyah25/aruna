"""News records (SPEC 8).

SPEC 8 requires every item to carry timestamp, source, asset, category,
importance, sentiment, and freshness - and requires the source to be auditable.
All seven are mandatory fields here, and the URL is kept so any classification
can be checked against the original.

Sentiment and importance are **derived**, not reported by the publisher, so both
carry their own confidence. See :mod:`aruna.news.classify` for exactly how crude
that derivation is - overstating it would be worse than not having it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import Market


class NewsCategory(StrEnum):
    """SPEC 8 categories, per market plus a shared fallback."""

    # Crypto
    REGULATION = "REGULATION"
    ETF = "ETF"
    EXCHANGE = "EXCHANGE"
    SECURITY = "SECURITY"
    PROTOCOL_UPGRADE = "PROTOCOL_UPGRADE"
    PROJECT = "PROJECT"
    # IDX
    EARNINGS = "EARNINGS"
    DIVIDEND = "DIVIDEND"
    RIGHTS_ISSUE = "RIGHTS_ISSUE"
    STOCK_SPLIT = "STOCK_SPLIT"
    ACQUISITION = "ACQUISITION"
    MANAGEMENT = "MANAGEMENT"
    CORPORATE_ACTION = "CORPORATE_ACTION"
    SECTOR = "SECTOR"
    GOVERNMENT_POLICY = "GOVERNMENT_POLICY"
    BI_RATE = "BI_RATE"
    INFLATION = "INFLATION"
    RUPIAH = "RUPIAH"
    COMMODITY = "COMMODITY"
    # Shared
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    UNCLASSIFIED = "UNCLASSIFIED"


class Sentiment(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    #: Genuinely could not tell. Distinct from NEUTRAL, which is a finding.
    UNKNOWN = "UNKNOWN"


class Importance(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One published item, classified.

    ``published_at`` is the publisher's timestamp; ``fetched_at`` is when ARUNA
    saw it. Both are kept because the gap between them is the freshness SPEC 8
    asks for, and a stale feed is invisible if you only record one.
    """

    title: str
    url: str
    source: str
    published_at: datetime | None
    fetched_at: datetime = field(default_factory=now_utc)
    summary: str = ""
    market: Market | None = None
    symbols: tuple[str, ...] = ()
    category: NewsCategory = NewsCategory.UNCLASSIFIED
    importance: Importance = Importance.LOW
    sentiment: Sentiment = Sentiment.UNKNOWN
    #: 0..1 for the derived sentiment. Low means the lexicon barely fired.
    sentiment_confidence: float = 0.0
    #: Terms that drove classification, so a verdict can be audited.
    matched_terms: tuple[str, ...] = ()

    @property
    def fingerprint(self) -> str:
        """Stable id for deduplication across feeds that syndicate each other."""
        basis = (self.url or f"{self.source}:{self.title}").strip().lower()
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]

    @property
    def age_seconds(self) -> float | None:
        if self.published_at is None:
            return None
        return (self.fetched_at - self.published_at).total_seconds()

    def freshness(self, *, reference: datetime | None = None) -> str:
        """SPEC 8 freshness, as a label that does not overstate what is known."""
        if self.published_at is None:
            return "UNKNOWN"
        age = ((reference or now_utc()) - self.published_at).total_seconds()
        if age < 3_600:
            return "FRESH"
        if age < 86_400:
            return "RECENT"
        if age < 7 * 86_400:
            return "STALE"
        return "OLD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": isoformat(self.published_at) if self.published_at else None,
            "fetched_at": isoformat(self.fetched_at),
            "market": self.market.value if self.market else None,
            "symbols": list(self.symbols),
            "category": self.category.value,
            "importance": self.importance.value,
            "sentiment": self.sentiment.value,
            "sentiment_confidence": round(self.sentiment_confidence, 3),
            "freshness": self.freshness(),
            "matched_terms": list(self.matched_terms),
        }


__all__ = ["Importance", "NewsCategory", "NewsItem", "Sentiment"]
