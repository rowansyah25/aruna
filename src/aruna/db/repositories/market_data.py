"""Market data storage.

Every write records the SPEC 4 provenance and the SPEC 5 quality verdict that
was reached when the row arrived.  Storing the verdict alongside the value is
what lets a later phase replay a decision and see how trustworthy its inputs
were *at the time*, rather than re-judging them with today's thresholds.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.core.enums import DataQuality, Horizon, Market
from aruna.data.models import Candle, Snapshot
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, to_mysql_datetime

#: Name of the cross-process write lock guarding ``candles``.
#:
#: The upkeep refresher (in ``aruna-run``) and the futures evidence refresh (in
#: ``futures-loop``) both write this table, and their rows are neighbours in
#: ``candles_unique`` - per asset ``1m`` sits next to ``4h``, and asset 5's
#: ``4h`` sits next to asset 6's ``15m`` across the CRYPTO/IDX boundary.  See
#: :meth:`~aruna.db.pool.Database.write_lock` for what that adjacency costs.
CANDLE_WRITE_LOCK = "candles"


class MarketDataRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- candles --------------------------------------------------------

    async def upsert_candles(self, asset_id: int, candles: list[Candle]) -> int:
        """Insert or refresh bars.  Returns how many rows were written.

        Re-fetching a window is normal - the newest bar is still forming - so
        the unique key updates rather than duplicating.
        """
        if not candles:
            return 0

        rows = [
            (
                asset_id,
                candle.market.value,
                candle.symbol,
                candle.interval.value,
                to_mysql_datetime(candle.open_time),
                to_mysql_datetime(candle.close_time),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
                candle.quote_volume,
                candle.trade_count,
                candle.is_closed,
                candle.provenance.source,
                to_mysql_datetime(candle.provenance.provider_timestamp),
                to_mysql_datetime(candle.provenance.server_timestamp),
                candle.provenance.latency_ms,
            )
            for candle in candles
        ]

        # Serialised against every other candle writer, in this process and in
        # the other one.  Without it two upserts whose rows are neighbours in
        # candles_unique deadlock on the gap between them (error 1213) and the
        # loser's bars are simply lost - measured, 5 refresh passes dropped.
        async with self._db.write_lock(CANDLE_WRITE_LOCK):
            await self._db.executemany(
                """
                INSERT INTO candles
                    (asset_id, market_code, symbol, interval_code, open_time, close_time,
                     open, high, low, close, volume, quote_volume, trade_count, is_closed,
                     source, provider_timestamp, server_timestamp, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    AS new
                ON DUPLICATE KEY UPDATE
                    close_time         = new.close_time,
                    open               = new.open,
                    high               = new.high,
                    low                = new.low,
                    close              = new.close,
                    volume             = new.volume,
                    quote_volume       = new.quote_volume,
                    trade_count        = new.trade_count,
                    is_closed          = new.is_closed,
                    source             = new.source,
                    provider_timestamp = new.provider_timestamp,
                    server_timestamp   = new.server_timestamp,
                    latency_ms         = new.latency_ms
                """,
                rows,
            )
        return len(candles)

    async def candles(
        self,
        asset_id: int,
        interval: Horizon,
        *,
        limit: int = 500,
        closed_only: bool = True,
    ) -> list[dict[str, Any]]:
        clause = " AND is_closed = TRUE" if closed_only else ""
        rows = await self._db.fetch(
            "SELECT open_time, close_time, open, high, low, close, volume, "
            "trade_count, is_closed, source, quality FROM candles "
            f"WHERE asset_id = %s AND interval_code = %s{clause} "
            "ORDER BY open_time DESC LIMIT %s",
            asset_id,
            interval.value,
            limit,
        )
        for row in rows:
            row["open_time"] = as_utc(row["open_time"])
            row["close_time"] = as_utc(row["close_time"])
        rows.reverse()
        return rows

    async def candles_between(
        self,
        asset_id: int,
        interval: Horizon,
        *,
        mulai: datetime,
        sampai: datetime,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Bar tertutup dalam satu rentang waktu, urut MENAIK.

        Ada karena :meth:`candles` mengambil ``limit`` bar **terbaru**, dan itu
        tidak bisa menjawab "apa yang terjadi di sekitar pukul sekian kemarin".
        Terukur 2026-08-22: penilaian bagian 16.19 atas skenario berumur tiga
        belas jam mengambil tiga puluh enam bar terbaru - jendela yang mulai
        empat jam **sesudah** skenarionya lahir - sehingga empat puluh dari
        empat puluh dilaporkan belum bisa dinilai.

        Batasnya dari sisi AWAL rentang, bukan akhir: yang dicari jalan harga
        sesudah sebuah titik, dan memotongnya dari depan membuang justru
        bagian yang ditanyakan.
        """
        rows = await self._db.fetch(
            "SELECT open_time, close_time, open, high, low, close, volume, "
            "trade_count, is_closed, source, quality FROM candles "
            "WHERE asset_id = %s AND interval_code = %s AND is_closed = TRUE "
            "AND open_time >= %s AND open_time <= %s "
            "ORDER BY open_time ASC LIMIT %s",
            asset_id,
            interval.value,
            to_mysql_datetime(mulai),
            to_mysql_datetime(sampai),
            limit,
        )
        for row in rows:
            row["open_time"] = as_utc(row["open_time"])
            row["close_time"] = as_utc(row["close_time"])
        return rows

    async def latest_candle_time(self, asset_id: int, interval: Horizon) -> datetime | None:
        value = await self._db.fetchval(
            "SELECT max(open_time) FROM candles "
            "WHERE asset_id = %s AND interval_code = %s AND is_closed = TRUE",
            asset_id,
            interval.value,
        )
        return as_utc(value)

    async def newest_closed_candles(
        self, market: Market, intervals: tuple[Horizon, ...]
    ) -> dict[tuple[int, str], datetime]:
        """Newest closed bar per asset and interval, for one market, in one query.

        Keyed ``(asset_id, interval_code)``.  An asset/interval pair with no
        stored bar at all is simply absent - the caller has to decide what an
        empty series means, and answering with a fabricated timestamp would make
        "never fetched" indistinguishable from "fetched and empty" (SPEC 4).

        This exists because the alternative is one ``latest_candle_time`` call
        per asset per interval, every cycle: sixty-four round trips a minute to
        answer a question one ``GROUP BY`` answers once.
        """
        if not intervals:
            return {}
        placeholders = ", ".join(["%s"] * len(intervals))
        rows = await self._db.fetch(
            "SELECT asset_id, interval_code, max(open_time) AS newest FROM candles "
            f"WHERE market_code = %s AND interval_code IN ({placeholders}) "
            "AND is_closed = TRUE GROUP BY asset_id, interval_code",
            market.value,
            *[interval.value for interval in intervals],
        )
        newest: dict[tuple[int, str], datetime] = {}
        for row in rows:
            stamp = as_utc(row["newest"])
            if stamp is not None:
                newest[(int(row["asset_id"]), row["interval_code"])] = stamp
        return newest

    async def candle_count(self, asset_id: int, interval: Horizon) -> int:
        return int(
            await self._db.fetchval(
                "SELECT count(*) FROM candles WHERE asset_id = %s AND interval_code = %s",
                asset_id,
                interval.value,
            )
        )

    async def coverage(self) -> list[dict[str, Any]]:
        """Per asset and interval: how many bars and how recent."""
        rows = await self._db.fetch(
            "SELECT market_code, symbol, interval_code, count(*) AS bars, "
            "min(open_time) AS oldest, max(open_time) AS newest "
            "FROM candles GROUP BY market_code, symbol, interval_code "
            "ORDER BY market_code, symbol, bars DESC"
        )
        for row in rows:
            row["oldest"] = as_utc(row["oldest"])
            row["newest"] = as_utc(row["newest"])
        return rows

    # ---- ticks ----------------------------------------------------------
    #
    # There is no tick storage here any more, and no ``market_ticks`` table
    # for it to write to - migration 0020 dropped it.  PASAL 26 states the
    # rule: SQL is long-term analysis memory, not a tape of every observation.
    #
    # ``record_tick``, ``latest_tick`` and ``quality_breakdown`` lived here for
    # the whole of PHASE 2 onward and wrote 76,567 rows between them.  Nothing
    # in ``src/`` or ``tests/`` ever read one back; the two read methods had
    # zero callers.  The table was write-only, which is why removing it costs
    # no analysis: there was none to lose.
    #
    # The quality verdict that used to ride on each tick is not lost with it.
    # It is still computed for every observation by the gate, still stored on
    # the surviving ``market_snapshots`` row, and a rejection still writes a
    # ``QUALITY_REJECTED`` row to ``provider_events``.  What is gone is the
    # per-tick copy of it.
    #
    # ``quality_breakdown`` was the one function that genuinely died here: it
    # aggregated verdicts by counting ``market_ticks`` rows and had no other
    # source.  It also had no caller.  Rebuilding it over ``provider_events``
    # would count rejections only, not the accepted majority, so it would give
    # a different answer to the same question - that belongs in a later change
    # with a reader attached, not in a mechanical port.

    # ---- snapshots ------------------------------------------------------

    # ``raw`` used to be the last column here: the provider's whole payload,
    # dumped once per poll per asset.  It was measured on 2026-08-21 at 513
    # characters a row across 419,352 rows - about 215 MB, 42% of the entire
    # database - and no ``SELECT`` anywhere in the tree ever read it back.  The
    # three readers of this table (``agents/service``, the Telegram bot, and the
    # market surface) all read the newest row and all spell their columns out;
    # ``raw`` appeared in none of them.
    #
    # The column is gone rather than left empty, because a column that exists
    # and is always NULL tells the next reader it holds something.
    async def record_snapshot(self, asset_id: int, snapshot: Snapshot) -> int:
        return await self._db.insert(
            """
            INSERT INTO market_snapshots
                (asset_id, market_code, symbol, captured_at, last_price, bid, ask,
                 spread_bps, high_24h, low_24h, volume_24h, change_24h_pct,
                 bid_depth, ask_depth, session_code, market_open, is_realtime,
                 declared_delay_sec, source, provider_timestamp, server_timestamp,
                 latency_ms, quality, quality_detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            asset_id,
            snapshot.market.value,
            snapshot.symbol,
            to_mysql_datetime(snapshot.captured_at),
            snapshot.last_price,
            snapshot.bid,
            snapshot.ask,
            snapshot.spread_bps,
            snapshot.high_24h,
            snapshot.low_24h,
            snapshot.volume_24h,
            snapshot.change_24h_pct,
            snapshot.bid_depth,
            snapshot.ask_depth,
            snapshot.session,
            snapshot.market_open,
            snapshot.provenance.is_realtime,
            snapshot.provenance.declared_delay_sec,
            snapshot.provenance.source,
            to_mysql_datetime(snapshot.provenance.provider_timestamp),
            to_mysql_datetime(snapshot.provenance.server_timestamp),
            snapshot.provenance.latency_ms,
            snapshot.quality.value,
            (snapshot.quality_detail or None) and snapshot.quality_detail[:255],
        )

    async def latest_snapshot(
        self, *, market: Market, symbol: str
    ) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT captured_at, last_price, bid, ask, spread_bps, high_24h, low_24h, "
            "volume_24h, change_24h_pct, session_code, market_open, is_realtime, "
            "declared_delay_sec, source, provider_timestamp, server_timestamp, "
            "latency_ms, quality, quality_detail FROM market_snapshots "
            "WHERE market_code = %s AND symbol = %s ORDER BY id DESC LIMIT 1",
            market.value,
            symbol,
        )
        if row:
            row["captured_at"] = as_utc(row["captured_at"])
            row["provider_timestamp"] = as_utc(row["provider_timestamp"])
            row["server_timestamp"] = as_utc(row["server_timestamp"])
            row["market_open"] = None if row["market_open"] is None else bool(row["market_open"])
            row["is_realtime"] = bool(row["is_realtime"])
        return row

    async def latest_snapshots(self, market: Market) -> list[dict[str, Any]]:
        """Newest snapshot per symbol in one market."""
        rows = await self._db.fetch(
            """
            SELECT s.symbol, s.captured_at, s.last_price, s.change_24h_pct,
                   s.spread_bps, s.volume_24h, s.session_code, s.market_open,
                   s.is_realtime, s.declared_delay_sec, s.quality, s.source
            FROM market_snapshots s
            JOIN (
                SELECT symbol, max(id) AS newest
                FROM market_snapshots WHERE market_code = %s GROUP BY symbol
            ) latest ON latest.newest = s.id
            ORDER BY s.symbol
            """,
            market.value,
        )
        for row in rows:
            row["captured_at"] = as_utc(row["captured_at"])
            row["market_open"] = None if row["market_open"] is None else bool(row["market_open"])
            row["is_realtime"] = bool(row["is_realtime"])
        return rows

    # ---- provider events -------------------------------------------------

    async def record_provider_event(
        self,
        *,
        provider: str,
        market: Market,
        event_type: str,
        message: str,
        symbol: str | None = None,
        quality: DataQuality | None = None,
        latency_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return await self._db.insert(
            """
            INSERT INTO provider_events
                (provider, market_code, symbol, event_type, quality, message,
                 latency_ms, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            provider,
            market.value,
            symbol,
            event_type,
            quality.value if quality else None,
            message,
            latency_ms,
            dump_json(details),
        )

    async def recent_provider_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT occurred_at, provider, market_code, symbol, event_type, "
            "quality, message, latency_ms FROM provider_events "
            "ORDER BY id DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["occurred_at"] = as_utc(row["occurred_at"])
        return rows


def decimal_or_none(value: Any) -> Decimal | None:
    return value if isinstance(value, Decimal) else None


__all__ = ["MarketDataRepository"]
