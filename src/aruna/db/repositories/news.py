"""News storage (SPEC 8).

Deduplication is by fingerprint (a hash of the URL), because outlets syndicate
each other and one story arriving through three feeds must not read as three
independent pieces of evidence (SPEC 17).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aruna.core.enums import Market
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, to_mysql_datetime
from aruna.news.models import NewsItem


class NewsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(self, item: NewsItem) -> bool:
        """Store an item. Returns True when it was new."""
        affected = await self._db.execute(
            """
            INSERT INTO news_events
                (fingerprint, title, url, summary, source, market_code, category,
                 importance, sentiment, sentiment_confidence, matched_terms,
                 published_at, fetched_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                -- Re-seeing a story refreshes its classification but keeps the
                -- original published_at: that is the publisher's claim, not ours.
                title                = new.title,
                summary              = new.summary,
                category             = new.category,
                importance           = new.importance,
                sentiment            = new.sentiment,
                sentiment_confidence = new.sentiment_confidence,
                matched_terms        = new.matched_terms
            """,
            item.fingerprint,
            item.title[:512],
            item.url[:1024],
            item.summary or None,
            item.source,
            item.market.value if item.market else None,
            item.category.value,
            item.importance.value,
            item.sentiment.value,
            round(item.sentiment_confidence, 3),
            dump_json(list(item.matched_terms)),
            to_mysql_datetime(item.published_at),
            to_mysql_datetime(item.fetched_at),
        )
        # MySQL reports 1 for an insert and 2 for an update of a changed row.
        return affected == 1

    async def link_asset(self, fingerprint: str, asset_id: int, symbol: str) -> None:
        """Tautkan satu berita ke satu aset, aman diulang.

        Alias baris, bukan ``VALUES(col)``. MySQL 8.0.20 menandai fungsi
        ``VALUES()`` sebagai usang dan memperingatkannya **sekali per baris** -
        dan tautan berita ditulis per item per aset, jadi satu siklus ingest
        menghasilkan ratusan baris peringatan identik di log operator.

        Kerugiannya bukan sekadar berisik. Log yang tenggelam oleh peringatan
        yang sama membuat peringatan yang sungguhan tidak terlihat, dan itu
        persis kegagalan yang sama dengan alert critical untuk cache.

        ``INSERT ... SELECT`` tidak bisa memakai bentuk ``VALUES (...) AS new``
        yang biasa, karena tidak ada klausa VALUES untuk diberi alias. Yang
        didukung MySQL 8.0.19+ adalah membungkus SELECT-nya sebagai derived
        table dan memberi alias pada tabel itu.
        """
        await self._db.execute(
            """
            INSERT INTO news_asset_links (news_id, asset_id, symbol)
            SELECT baru.news_id, baru.asset_id, baru.symbol
            FROM (
                SELECT id AS news_id, %s AS asset_id, %s AS symbol
                FROM news_events WHERE fingerprint = %s
            ) AS baru
            ON DUPLICATE KEY UPDATE symbol = baru.symbol
            """,
            asset_id,
            symbol,
            fingerprint,
        )

    async def recent(
        self,
        *,
        limit: int = 20,
        market: Market | None = None,
        min_importance: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if market is not None:
            clauses.append("market_code = %s")
            args.append(market.value)
        if min_importance:
            ranking = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
            allowed = [
                name for name, rank in ranking.items()
                if rank >= ranking.get(min_importance, 0)
            ]
            placeholders = ", ".join(["%s"] * len(allowed))
            clauses.append(f"importance IN ({placeholders})")
            args.extend(allowed)

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        args.append(limit)

        rows = await self._db.fetch(
            "SELECT fingerprint, title, url, source, market_code, category, "
            "importance, sentiment, sentiment_confidence, published_at, fetched_at "
            f"FROM news_events{where} "
            "ORDER BY COALESCE(published_at, fetched_at) DESC LIMIT %s",
            *args,
        )
        for row in rows:
            row["published_at"] = as_utc(row["published_at"])
            row["fetched_at"] = as_utc(row["fetched_at"])
        return rows

    async def for_symbol(self, symbol: str, *, limit: int = 10) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT n.title, n.url, n.source, n.category, n.importance, n.sentiment,
                   n.published_at
            FROM news_events n
            JOIN news_asset_links l ON l.news_id = n.id
            WHERE l.symbol = %s
            ORDER BY COALESCE(n.published_at, n.fetched_at) DESC LIMIT %s
            """,
            symbol,
            limit,
        )
        for row in rows:
            row["published_at"] = as_utc(row["published_at"])
        return rows

    async def sentiment_breakdown(
        self, *, since: datetime, market: Market | None = None
    ) -> dict[str, int]:
        if market is not None:
            rows = await self._db.fetch(
                "SELECT sentiment, count(*) AS n FROM news_events "
                "WHERE COALESCE(published_at, fetched_at) >= %s AND market_code = %s "
                "GROUP BY sentiment",
                to_mysql_datetime(since),
                market.value,
            )
        else:
            rows = await self._db.fetch(
                "SELECT sentiment, count(*) AS n FROM news_events "
                "WHERE COALESCE(published_at, fetched_at) >= %s GROUP BY sentiment",
                to_mysql_datetime(since),
            )
        return {row["sentiment"]: int(row["n"]) for row in rows}

    async def counts_by_source(self) -> dict[str, int]:
        rows = await self._db.fetch(
            "SELECT source, count(*) AS n FROM news_events GROUP BY source ORDER BY source"
        )
        return {row["source"]: int(row["n"]) for row in rows}

    async def total(self) -> int:
        return int(await self._db.fetchval("SELECT count(*) FROM news_events"))


__all__ = ["NewsRepository"]
