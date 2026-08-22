"""RSS news provider (SPEC 8, 47).

RSS is used because it is the one news channel publishers explicitly offer for
syndication - no scraping, no circumvented access control, and the source URL
travels with every item so any classification stays auditable (SPEC 8).

Feeds verified reachable during development:

===========================  ==========================================
Kontan (investasi)           Indonesian markets
CNBC Indonesia (market)      Indonesian markets
Detik Finance                Indonesian business
CoinDesk                     Crypto
Cointelegraph                Crypto
===========================  ==========================================

IDX's own announcement feed and Bisnis.com both return 403 to non-browser
clients; they are listed as unavailable rather than worked around.

Parsing uses the stdlib XML parser with entity expansion left off by default.
Feeds are untrusted third-party input, and an XML parser that resolves external
entities is a well-known way to turn "read the news" into "read local files".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from aruna.core.clock import now_utc
from aruna.core.enums import Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.http import HttpFetcher
from aruna.news.classify import (
    classify_category,
    classify_importance,
    classify_sentiment,
    infer_market,
    link_symbols,
)
from aruna.news.models import NewsItem

log = get_logger("aruna.news.rss")

SOURCE = "rss"


@dataclass(frozen=True, slots=True)
class Feed:
    name: str
    url: str
    #: Market this outlet mostly covers. Individual items can still be
    #: reclassified by their own text.
    market: Market | None = None
    language: str = "id"


DEFAULT_FEEDS: tuple[Feed, ...] = (
    Feed("kontan", "https://investasi.kontan.co.id/rss", Market.IDX, "id"),
    Feed("cnbc-indonesia", "https://www.cnbcindonesia.com/market/rss", Market.IDX, "id"),
    Feed("detik-finance", "https://finance.detik.com/rss", Market.IDX, "id"),
    Feed("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", Market.CRYPTO, "en"),
    Feed("cointelegraph", "https://cointelegraph.com/rss", Market.CRYPTO, "en"),
)

#: Feeds known to refuse non-browser clients. Recorded so the gap is visible
#: rather than silently missing (SPEC 4).
UNAVAILABLE_FEEDS: tuple[tuple[str, str], ...] = (
    ("idx-announcements", "returns HTTP 403 to non-browser clients"),
    ("bisnis-com", "returns HTTP 403 to non-browser clients"),
)


class RssNewsProvider:
    """Fetches and classifies items from a set of RSS feeds."""

    def __init__(
        self,
        feeds: tuple[Feed, ...] = DEFAULT_FEEDS,
        *,
        timeout_sec: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        self._feeds = feeds
        self._http = HttpFetcher(
            source=SOURCE,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
        )

    @property
    def feeds(self) -> tuple[Feed, ...]:
        return self._feeds

    async def open(self) -> None:
        await self._http.open()

    async def close(self) -> None:
        await self._http.close()

    async def fetch(
        self, feed: Feed, *, symbol_aliases: dict[str, tuple[str, ...]] | None = None
    ) -> list[NewsItem]:
        """Fetch and classify one feed. Raises when the feed is unreachable."""
        raw, _latency = await self._http.get_text(feed.url)
        return self.parse(raw, feed, symbol_aliases=symbol_aliases or {})

    def parse(
        self,
        raw: str,
        feed: Feed,
        *,
        symbol_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> list[NewsItem]:
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError as exc:
            raise DataSourceUnavailableError(
                f"feed {feed.name} returned unparseable XML: {exc}"
            ) from exc

        fetched = now_utc()
        aliases = symbol_aliases or {}
        items: list[NewsItem] = []

        for node in _entries(root):
            item = self._to_item(node, feed, fetched, aliases)
            if item is not None:
                items.append(item)
        return items

    def _to_item(
        self,
        node: ElementTree.Element,
        feed: Feed,
        fetched: datetime,
        aliases: dict[str, tuple[str, ...]],
    ) -> NewsItem | None:
        title = _text(node, "title")
        if not title:
            return None

        link = _text(node, "link") or _attr(node, "link", "href") or ""
        summary = _text(node, "description") or _text(node, "summary") or ""
        published = _parse_date(
            _text(node, "pubDate") or _text(node, "published") or _text(node, "updated")
        )

        basis = f"{title} {summary}"
        category, category_terms = classify_category(basis)
        sentiment, confidence, sentiment_terms = classify_sentiment(basis)

        return NewsItem(
            title=title.strip(),
            url=link.strip(),
            source=feed.name,
            published_at=published,
            fetched_at=fetched,
            summary=_clean(summary)[:500],
            market=infer_market(basis, feed.market),
            symbols=link_symbols(basis, aliases),
            category=category,
            importance=classify_importance(category, confidence),
            sentiment=sentiment,
            sentiment_confidence=confidence,
            matched_terms=tuple(category_terms + sentiment_terms)[:12],
        )


# ---------------------------------------------------------------------------


def _entries(root: ElementTree.Element) -> list[ElementTree.Element]:
    """Items from RSS 2.0 (``item``) or Atom (``entry``)."""
    items = root.findall(".//item")
    if items:
        return items
    return [
        node
        for node in root.iter()
        if node.tag.rsplit("}", 1)[-1] == "entry"
    ]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ElementTree.Element, name: str) -> str:
    for child in node:
        if _local(child.tag) == name and child.text:
            return child.text
    return ""


def _attr(node: ElementTree.Element, name: str, attribute: str) -> str:
    for child in node:
        if _local(child.tag) == name:
            value = child.get(attribute)
            if value:
                return value
    return ""


def _clean(text: str) -> str:
    """Strip tags and collapse whitespace from a description blob."""
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _parse_date(value: str) -> datetime | None:
    """RFC 822 (RSS) or ISO 8601 (Atom). Returns None rather than guessing."""
    if not value:
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        # A feed without an offset is ambiguous; UTC is the least-wrong
        # assumption and the gap to fetched_at makes any error visible.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = ["DEFAULT_FEEDS", "SOURCE", "UNAVAILABLE_FEEDS", "Feed", "RssNewsProvider"]
