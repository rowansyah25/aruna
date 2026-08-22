"""Analysis storage.

Snapshots are keyed on ``(asset, interval, as_of)`` so recomputing a bar
refreshes its row rather than accumulating near-duplicates. ``as_of`` is the
newest settled bar behind the row, which is what makes a PHASE 9 replay able to
prove no future data leaked in (SPEC 24).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aruna.analysis.engine import TechnicalSnapshot
from aruna.core.enums import Horizon, Market
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json, to_mysql_datetime


class AnalysisRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---- technical -------------------------------------------------------

    async def save(self, asset_id: int, snapshot: TechnicalSnapshot) -> None:
        """Persist the technical, volume, and regime views of one snapshot."""
        await self._save_technical(asset_id, snapshot)
        await self._save_volume(asset_id, snapshot)
        await self._save_regime(asset_id, snapshot)

    async def _save_technical(self, asset_id: int, snapshot: TechnicalSnapshot) -> None:
        macd = snapshot.reading("macd")
        atr = snapshot.reading("atr")

        await self._db.execute(
            """
            INSERT INTO technical_snapshots
                (asset_id, market_code, symbol, interval_code, as_of, computed_at,
                 bars, reliable_readings, total_readings, excluded_open_bars,
                 close, rsi, atr_pct, macd_histogram, bollinger_pct_b,
                 readings, structure, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                computed_at        = new.computed_at,
                bars               = new.bars,
                reliable_readings  = new.reliable_readings,
                total_readings     = new.total_readings,
                excluded_open_bars = new.excluded_open_bars,
                close              = new.close,
                rsi                = new.rsi,
                atr_pct            = new.atr_pct,
                macd_histogram     = new.macd_histogram,
                bollinger_pct_b    = new.bollinger_pct_b,
                readings           = new.readings,
                structure          = new.structure,
                notes              = new.notes
            """,
            asset_id,
            snapshot.market.value,
            snapshot.symbol,
            snapshot.interval.value,
            to_mysql_datetime(snapshot.as_of),
            to_mysql_datetime(snapshot.computed_at),
            snapshot.bars,
            snapshot.reliable_count,
            len(snapshot.readings),
            snapshot.excluded_open_bars,
            None,
            _round(snapshot.value("rsi"), 4),
            _round(atr.components.get("atr_pct") if atr and atr.reliable else None, 4),
            _round(macd.components.get("histogram") if macd and macd.reliable else None, 8),
            _round(snapshot.value("bollinger"), 4),
            dump_json({name: r.to_dict() for name, r in snapshot.readings.items()}),
            dump_json(snapshot.structure.to_dict()),
            dump_json(list(snapshot.notes)),
        )

    async def _save_volume(self, asset_id: int, snapshot: TechnicalSnapshot) -> None:
        anomaly = snapshot.reading("volume_anomaly")
        trend = snapshot.reading("volume_trend")
        vwap = snapshot.reading("vwap")

        ratio = anomaly.value if anomaly and anomaly.reliable else None
        await self._db.execute(
            """
            INSERT INTO volume_snapshots
                (asset_id, market_code, symbol, interval_code, as_of, computed_at,
                 latest_volume, average_volume, volume_ratio, volume_trend_pct,
                 vwap, vwap_distance_pct, is_anomaly, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                computed_at       = new.computed_at,
                latest_volume     = new.latest_volume,
                average_volume    = new.average_volume,
                volume_ratio      = new.volume_ratio,
                volume_trend_pct  = new.volume_trend_pct,
                vwap              = new.vwap,
                vwap_distance_pct = new.vwap_distance_pct,
                is_anomaly        = new.is_anomaly,
                detail            = new.detail
            """,
            asset_id,
            snapshot.market.value,
            snapshot.symbol,
            snapshot.interval.value,
            to_mysql_datetime(snapshot.as_of),
            to_mysql_datetime(snapshot.computed_at),
            _round(anomaly.components.get("latest") if anomaly else None, 12),
            _round(anomaly.components.get("average") if anomaly else None, 12),
            _round(ratio, 4),
            _round(trend.value if trend and trend.reliable else None, 4),
            _round(vwap.value if vwap and vwap.reliable else None, 12),
            _round(vwap.components.get("distance_pct") if vwap else None, 4),
            bool(ratio is not None and (ratio >= 2.0 or ratio <= 0.3)),
            (anomaly.detail or None) if anomaly else None,
        )

    async def _save_regime(self, asset_id: int, snapshot: TechnicalSnapshot) -> None:
        verdict = snapshot.regime
        await self._db.execute(
            """
            INSERT INTO regimes
                (asset_id, market_code, symbol, interval_code, as_of, computed_at,
                 regime, confidence, trend, breakout, evidence_used,
                 evidence_available, reasons, alternatives)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                computed_at        = new.computed_at,
                regime             = new.regime,
                confidence         = new.confidence,
                trend              = new.trend,
                breakout           = new.breakout,
                evidence_used      = new.evidence_used,
                evidence_available = new.evidence_available,
                reasons            = new.reasons,
                alternatives       = new.alternatives
            """,
            asset_id,
            snapshot.market.value,
            snapshot.symbol,
            snapshot.interval.value,
            to_mysql_datetime(snapshot.as_of),
            to_mysql_datetime(snapshot.computed_at),
            verdict.regime.value,
            round(verdict.confidence, 3),
            snapshot.structure.trend.value,
            snapshot.structure.breakout.value,
            verdict.evidence_used,
            verdict.evidence_available,
            dump_json(list(verdict.reasons)),
            dump_json(list(verdict.alternatives)),
        )

    # ---- reads -----------------------------------------------------------

    async def latest_technical(
        self, asset_id: int, interval: Horizon
    ) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT as_of, computed_at, bars, reliable_readings, total_readings, "
            "rsi, atr_pct, macd_histogram, bollinger_pct_b, readings, structure, notes "
            "FROM technical_snapshots WHERE asset_id = %s AND interval_code = %s "
            "ORDER BY as_of DESC LIMIT 1",
            asset_id,
            interval.value,
        )
        if row:
            row["as_of"] = as_utc(row["as_of"])
            row["computed_at"] = as_utc(row["computed_at"])
            row["readings"] = load_json(row["readings"])
            row["structure"] = load_json(row["structure"])
            row["notes"] = load_json(row["notes"])
        return row

    async def latest_regime(
        self, asset_id: int, interval: Horizon
    ) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT as_of, regime, confidence, trend, breakout, evidence_used, "
            "evidence_available, reasons, alternatives FROM regimes "
            "WHERE asset_id = %s AND interval_code = %s ORDER BY as_of DESC LIMIT 1",
            asset_id,
            interval.value,
        )
        if row:
            row["as_of"] = as_utc(row["as_of"])
            row["reasons"] = load_json(row["reasons"])
            row["alternatives"] = load_json(row["alternatives"])
        return row

    async def regime_distribution(
        self, market: Market, *, since: datetime | None = None
    ) -> dict[str, int]:
        if since:
            rows = await self._db.fetch(
                "SELECT regime, count(*) AS n FROM regimes "
                "WHERE market_code = %s AND as_of >= %s GROUP BY regime",
                market.value,
                to_mysql_datetime(since),
            )
        else:
            rows = await self._db.fetch(
                "SELECT regime, count(*) AS n FROM regimes WHERE market_code = %s "
                "GROUP BY regime",
                market.value,
            )
        return {row["regime"]: int(row["n"]) for row in rows}

    async def volume_anomalies(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT market_code, symbol, interval_code, as_of, volume_ratio, detail "
            "FROM volume_snapshots WHERE is_anomaly = TRUE "
            "ORDER BY as_of DESC LIMIT %s",
            limit,
        )
        for row in rows:
            row["as_of"] = as_utc(row["as_of"])
        return rows

    async def coverage(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT market_code, symbol, interval_code, count(*) AS snapshots, "
            "max(as_of) AS newest FROM technical_snapshots "
            "GROUP BY market_code, symbol, interval_code "
            "ORDER BY market_code, symbol, interval_code"
        )
        for row in rows:
            row["newest"] = as_utc(row["newest"])
        return rows


def _round(value: float | None, places: int) -> float | None:
    """Round in Python so MySQL never silently narrows a value on insert."""
    return None if value is None else round(float(value), places)


__all__ = ["AnalysisRepository"]
