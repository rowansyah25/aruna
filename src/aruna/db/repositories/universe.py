"""Markets and the tradable universe.

SPEC 3 is explicit that the IDX universe must not be hardcoded, so assets are
upserted from a configuration module rather than a migration, and every asset
can be disabled without deleting its history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from aruna.core.enums import Market
from aruna.db.pool import Database
from aruna.db.types import dump_json, load_json_object


@dataclass(frozen=True, slots=True)
class MarketRecord:
    code: Market
    display_name: str
    timezone: str
    is_continuous: bool
    quote_currency: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class AssetRecord:
    id: int
    market: Market
    symbol: str
    display_name: str
    asset_class: str
    base_asset: str | None
    quote_asset: str | None
    sector: str | None
    lot_size: int | None
    tick_size: Decimal | None
    price_precision: int | None
    enabled: bool
    listed_on: date | None
    delisted_on: date | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """An asset as declared in configuration, before it reaches the database."""

    market: Market
    symbol: str
    display_name: str
    asset_class: str
    base_asset: str | None = None
    quote_asset: str | None = None
    sector: str | None = None
    lot_size: int | None = None
    tick_size: Decimal | None = None
    price_precision: int | None = None
    metadata: dict[str, Any] | None = None


_ASSET_COLUMNS = (
    "id, market_code, symbol, display_name, asset_class, base_asset, quote_asset, "
    "sector, lot_size, tick_size, price_precision, enabled, listed_on, delisted_on, "
    "metadata"
)


class UniverseRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- markets -------------------------------------------------------

    async def markets(self, *, enabled_only: bool = False) -> list[MarketRecord]:
        sql = (
            "SELECT code, display_name, timezone, is_continuous, quote_currency, enabled "
            "FROM markets"
        )
        if enabled_only:
            sql += " WHERE enabled = TRUE"
        rows = await self._db.fetch(sql + " ORDER BY code")
        return [
            MarketRecord(
                code=Market(row["code"]),
                display_name=row["display_name"],
                timezone=row["timezone"],
                is_continuous=bool(row["is_continuous"]),
                quote_currency=row["quote_currency"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    async def set_market_enabled(self, market: Market, enabled: bool) -> None:
        await self._db.execute(
            "UPDATE markets SET enabled = %s WHERE code = %s", enabled, market.value
        )

    # ---- assets --------------------------------------------------------

    async def upsert_asset(self, spec: AssetSpec) -> int:
        """Insert or update one asset, returning its id.

        Deliberately does not touch ``enabled``: an operator who disabled an
        asset should not have it silently re-enabled by the next seed run.

        ``id = LAST_INSERT_ID(id)`` is the MySQL idiom for "give me the row's id
        whether this was an insert or an update" - without it, ``lastrowid``
        after an update is meaningless.
        """
        return await self._db.insert(
            """
            INSERT INTO assets
                (market_code, symbol, display_name, asset_class, base_asset,
                 quote_asset, sector, lot_size, tick_size, price_precision, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                id              = LAST_INSERT_ID(assets.id),
                display_name    = new.display_name,
                asset_class     = new.asset_class,
                base_asset      = new.base_asset,
                quote_asset     = new.quote_asset,
                sector          = new.sector,
                lot_size        = new.lot_size,
                tick_size       = new.tick_size,
                price_precision = new.price_precision,
                metadata        = new.metadata
            """,
            spec.market.value,
            spec.symbol,
            spec.display_name,
            spec.asset_class,
            spec.base_asset,
            spec.quote_asset,
            spec.sector,
            spec.lot_size,
            spec.tick_size,
            spec.price_precision,
            dump_json(spec.metadata or {}),
        )

    async def upsert_many(self, specs: list[AssetSpec]) -> int:
        count = 0
        async with self._db.transaction():
            for spec in specs:
                await self.upsert_asset(spec)
                count += 1
        return count

    async def assets(
        self, *, market: Market | None = None, enabled_only: bool = True
    ) -> list[AssetRecord]:
        clauses: list[str] = []
        args: list[Any] = []
        if market is not None:
            clauses.append("market_code = %s")
            args.append(market.value)
        if enabled_only:
            clauses.append("enabled = TRUE")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        rows = await self._db.fetch(
            f"SELECT {_ASSET_COLUMNS} FROM assets{where} ORDER BY market_code, symbol",
            *args,
        )
        return [_to_asset(row) for row in rows]

    async def find(self, market: Market, symbol: str) -> AssetRecord | None:
        row = await self._db.fetchrow(
            f"SELECT {_ASSET_COLUMNS} FROM assets "
            "WHERE market_code = %s AND symbol = %s",
            market.value,
            symbol,
        )
        return _to_asset(row) if row else None

    async def set_asset_enabled(self, market: Market, symbol: str, enabled: bool) -> bool:
        affected = await self._db.execute(
            "UPDATE assets SET enabled = %s WHERE market_code = %s AND symbol = %s",
            enabled,
            market.value,
            symbol,
        )
        return affected == 1

    async def disable_absent(
        self, market: Market, keep_symbols: set[str]
    ) -> list[str]:
        """Disable enabled assets in ``market`` that are not in ``keep_symbols``.

        Disabled, never deleted: an asset that leaves the universe still has
        candles and signals pointing at it, and SPEC 35 needs delisted names
        retained so backtests do not acquire survivorship bias.
        """
        current = await self.assets(market=market, enabled_only=True)
        retired = [a.symbol for a in current if a.symbol not in keep_symbols]
        for symbol in retired:
            await self.set_asset_enabled(market, symbol, False)
        return retired

    async def counts_by_market(self) -> dict[str, int]:
        rows = await self._db.fetch(
            "SELECT market_code, count(*) AS n FROM assets WHERE enabled = TRUE "
            "GROUP BY market_code"
        )
        return {row["market_code"]: int(row["n"]) for row in rows}


def _to_asset(row: dict[str, Any]) -> AssetRecord:
    return AssetRecord(
        id=row["id"],
        market=Market(row["market_code"]),
        symbol=row["symbol"],
        display_name=row["display_name"],
        asset_class=row["asset_class"],
        base_asset=row["base_asset"],
        quote_asset=row["quote_asset"],
        sector=row["sector"],
        lot_size=row["lot_size"],
        tick_size=row["tick_size"],
        price_precision=row["price_precision"],
        enabled=bool(row["enabled"]),
        listed_on=row["listed_on"],
        delisted_on=row["delisted_on"],
        metadata=load_json_object(row["metadata"]),
    )


__all__ = ["AssetRecord", "AssetSpec", "MarketRecord", "UniverseRepository"]
