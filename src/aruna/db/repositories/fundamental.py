"""Fundamental and correlation storage (SPEC 7, 17, 32)."""

from __future__ import annotations

from datetime import date
from typing import Any

from aruna.analysis.correlation import CorrelationMatrix
from aruna.core.enums import Market
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime
from aruna.fundamental.engine import ValuationReport
from aruna.fundamental.models import METRIC_FIELDS, Fundamentals


class FundamentalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(
        self,
        asset_id: int,
        data: Fundamentals,
        report: ValuationReport | None = None,
        *,
        as_of: date | None = None,
    ) -> None:
        metrics = {name: getattr(data, name) for name in METRIC_FIELDS}
        await self._db.execute(
            """
            INSERT INTO fundamentals
                (asset_id, symbol, source, fetched_at, as_of_date, currency, sector,
                 industry, revenue_growth_pct, earnings_growth_pct, eps, roe_pct,
                 roa_pct, debt_to_equity, free_cash_flow, total_debt,
                 price_to_earnings, price_to_book, book_value_per_share,
                 dividend_yield_pct, profit_margin_pct, market_cap, coverage,
                 missing_metrics, verdict, verdict_confidence, verdict_reasons,
                 verdict_concerns)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                source              = new.source,
                fetched_at          = new.fetched_at,
                currency            = new.currency,
                sector              = new.sector,
                industry            = new.industry,
                revenue_growth_pct  = new.revenue_growth_pct,
                earnings_growth_pct = new.earnings_growth_pct,
                eps                 = new.eps,
                roe_pct             = new.roe_pct,
                roa_pct             = new.roa_pct,
                debt_to_equity      = new.debt_to_equity,
                free_cash_flow      = new.free_cash_flow,
                total_debt          = new.total_debt,
                price_to_earnings   = new.price_to_earnings,
                price_to_book       = new.price_to_book,
                book_value_per_share= new.book_value_per_share,
                dividend_yield_pct  = new.dividend_yield_pct,
                profit_margin_pct   = new.profit_margin_pct,
                market_cap          = new.market_cap,
                coverage            = new.coverage,
                missing_metrics     = new.missing_metrics,
                verdict             = new.verdict,
                verdict_confidence  = new.verdict_confidence,
                verdict_reasons     = new.verdict_reasons,
                verdict_concerns    = new.verdict_concerns
            """,
            asset_id,
            data.symbol,
            data.source,
            to_mysql_datetime(data.fetched_at),
            as_of or data.fetched_at.date(),
            data.currency,
            data.sector,
            data.industry,
            *[_round(metrics[name]) for name in METRIC_FIELDS],
            round(data.coverage, 3),
            dump_json(list(data.missing)),
            report.verdict.value if report else None,
            round(report.confidence, 3) if report else None,
            dump_json(list(report.reasons)) if report else None,
            dump_json(list(report.concerns)) if report else None,
        )

    async def latest(self, asset_id: int) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT symbol, source, fetched_at, as_of_date, sector, currency, "
            "eps, roe_pct, roa_pct, debt_to_equity, price_to_earnings, price_to_book, "
            "dividend_yield_pct, revenue_growth_pct, earnings_growth_pct, coverage, "
            "missing_metrics, verdict, verdict_confidence, verdict_reasons, "
            "verdict_concerns FROM fundamentals WHERE asset_id = %s "
            "ORDER BY as_of_date DESC LIMIT 1",
            asset_id,
        )
        if row:
            row["fetched_at"] = as_utc(row["fetched_at"])
            row["missing_metrics"] = load_json(row["missing_metrics"])
            row["verdict_reasons"] = load_json(row["verdict_reasons"])
            row["verdict_concerns"] = load_json(row["verdict_concerns"])
        return row

    async def verdict_distribution(self) -> dict[str, int]:
        rows = await self._db.fetch(
            "SELECT verdict, count(*) AS n FROM fundamentals "
            "WHERE verdict IS NOT NULL GROUP BY verdict"
        )
        return {row["verdict"]: int(row["n"]) for row in rows}

    async def coverage(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT symbol, as_of_date, coverage, verdict, verdict_confidence "
            "FROM fundamentals ORDER BY symbol"
        )
        return rows


class CorrelationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, matrix: CorrelationMatrix, *, market: Market) -> int:
        stored = 0
        for pair in matrix.pairs:
            await self._db.execute(
                """
                INSERT INTO correlations
                    (market_code, interval_code, left_symbol, right_symbol,
                     coefficient, overlap, strength, computed_at, as_of)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
                ON DUPLICATE KEY UPDATE
                    coefficient = new.coefficient,
                    overlap     = new.overlap,
                    strength    = new.strength,
                    computed_at = new.computed_at
                """,
                market.value,
                matrix.interval,
                pair.left,
                pair.right,
                round(pair.coefficient, 5),
                pair.overlap,
                pair.strength,
                to_mysql_datetime(matrix.computed_at),
                to_mysql_datetime(pair.last or matrix.computed_at),
            )
            stored += 1
        return stored

    async def latest(
        self, market: Market, interval: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT left_symbol, right_symbol, coefficient, overlap, strength, as_of "
            "FROM correlations WHERE market_code = %s AND interval_code = %s "
            "ORDER BY as_of DESC, abs(coefficient) DESC LIMIT %s",
            market.value,
            interval,
            limit,
        )
        for row in rows:
            row["as_of"] = as_utc(row["as_of"])
        return rows


def _round(value: float | None, places: int = 6) -> float | None:
    """Round in Python so MySQL never silently narrows a value on insert."""
    return None if value is None else round(float(value), places)


__all__ = ["CorrelationRepository", "FundamentalRepository"]
