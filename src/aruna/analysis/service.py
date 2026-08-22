"""Analysis service: stored candles -> snapshots -> storage.

Reads only closed bars from the database, computes, and persists. Assets or
intervals with too little history are reported as skipped rather than analysed
on thin data - a regime call from 8 bars is not a weaker answer, it is a
misleading one (SPEC 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.analysis.engine import MINIMUM_BARS, AnalysisEngine, TechnicalSnapshot
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.db.repositories.analysis import AnalysisRepository
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.universe import UniverseRepository

log = get_logger("aruna.analysis")

#: Bars pulled per analysis. Enough for a 50-period MA plus a 26/9 MACD with
#: room to spare, without dragging a year of history through memory.
DEFAULT_WINDOW = 300


@dataclass(slots=True)
class AnalysisResult:
    analysed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)
    snapshots: list[TechnicalSnapshot] = field(default_factory=list)
    regimes: dict[str, int] = field(default_factory=dict)

    def note_regime(self, name: str) -> None:
        self.regimes[name] = self.regimes.get(name, 0) + 1

    def summary(self) -> str:
        parts = [f"analysed={self.analysed}", f"skipped={self.skipped}"]
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


class AnalysisService:
    def __init__(
        self,
        *,
        universe: UniverseRepository,
        market_data: MarketDataRepository,
        analysis: AnalysisRepository,
        engine: AnalysisEngine | None = None,
        window: int = DEFAULT_WINDOW,
    ) -> None:
        self._universe = universe
        self._market_data = market_data
        self._analysis = analysis
        self._engine = engine or AnalysisEngine()
        self._window = window

    async def analyse_market(
        self,
        market: Market,
        intervals: tuple[Horizon, ...],
        *,
        symbols: tuple[str, ...] | None = None,
        persist: bool = True,
    ) -> AnalysisResult:
        result = AnalysisResult()
        assets = await self._universe.assets(market=market, enabled_only=True)
        if symbols:
            wanted = {s.upper() for s in symbols}
            assets = [a for a in assets if a.symbol.upper() in wanted]

        if not assets:
            result.failures.append(f"no enabled assets for {market.value}")
            return result

        for asset in assets:
            for interval in intervals:
                try:
                    await self._analyse_one(asset, interval, result, persist=persist)
                except ArunaError as exc:
                    result.failures.append(f"{asset.symbol} {interval.value}: {exc}")
        return result

    async def _analyse_one(
        self, asset, interval: Horizon, result: AnalysisResult, *, persist: bool
    ) -> None:
        rows = await self._market_data.candles(
            asset.id, interval, limit=self._window, closed_only=True
        )
        if len(rows) < MINIMUM_BARS:
            result.skipped += 1
            log.debug(
                "analysis.skipped",
                symbol=asset.symbol,
                interval=interval.value,
                bars=len(rows),
                needed=MINIMUM_BARS,
            )
            return

        try:
            series = CandleSeries.from_rows(
                rows, market=asset.market, symbol=asset.symbol, interval=interval
            )
        except InsufficientData as exc:
            result.skipped += 1
            result.failures.append(f"{asset.symbol} {interval.value}: {exc}")
            return

        snapshot = self._engine.analyse(series)
        if persist:
            await self._analysis.save(asset.id, snapshot)

        result.analysed += 1
        result.snapshots.append(snapshot)
        result.note_regime(snapshot.regime.regime.value)

        log.info(
            "analysis.computed",
            symbol=asset.symbol,
            interval=interval.value,
            regime=snapshot.regime.regime.value,
            confidence=round(snapshot.regime.confidence, 3),
            bars=snapshot.bars,
            reliable=snapshot.reliable_count,
        )


__all__ = ["DEFAULT_WINDOW", "AnalysisResult", "AnalysisService"]
