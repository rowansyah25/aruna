"""Fundamental ingestion (SPEC 7).

IDX only. Crypto assets have no earnings, book value, or dividend, so running a
fundamental model over them would produce shaped noise — the service refuses
rather than returning empty numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.core.enums import Market, ValuationVerdict
from aruna.core.errors import ArunaError, DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.db.repositories.fundamental import FundamentalRepository
from aruna.db.repositories.universe import UniverseRepository
from aruna.fundamental.engine import FundamentalEngine
from aruna.fundamental.yahoo import YahooFundamentalProvider

log = get_logger("aruna.fundamental")


@dataclass(slots=True)
class FundamentalResult:
    fetched: int = 0
    stored: int = 0
    failures: list[str] = field(default_factory=list)
    verdicts: dict[str, int] = field(default_factory=dict)

    def note(self, verdict: ValuationVerdict) -> None:
        self.verdicts[verdict.value] = self.verdicts.get(verdict.value, 0) + 1

    def summary(self) -> str:
        parts = [f"fetched={self.fetched}", f"stored={self.stored}"]
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


class FundamentalService:
    def __init__(
        self,
        *,
        provider: YahooFundamentalProvider,
        store: FundamentalRepository,
        universe: UniverseRepository,
        engine: FundamentalEngine | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._universe = universe
        self._engine = engine or FundamentalEngine()

    async def ingest(
        self, *, symbols: tuple[str, ...] | None = None
    ) -> FundamentalResult:
        result = FundamentalResult()
        assets = await self._universe.assets(market=Market.IDX, enabled_only=True)
        if symbols:
            wanted = {s.upper() for s in symbols}
            assets = [a for a in assets if a.symbol.upper() in wanted]

        if not assets:
            result.failures.append("no enabled IDX assets")
            return result

        for asset in assets:
            try:
                data = await self._provider.fetch(asset.symbol)
            except DataSourceUnavailableError as exc:
                result.failures.append(f"{asset.symbol}: {exc}")
                continue
            except ArunaError as exc:
                result.failures.append(f"{asset.symbol}: {exc}")
                continue

            result.fetched += 1
            report = self._engine.evaluate(data)
            result.note(report.verdict)

            await self._store.save(asset.id, data, report)
            result.stored += 1

            log.info(
                "fundamental.evaluated",
                symbol=asset.symbol,
                verdict=report.verdict.value,
                confidence=round(report.confidence, 3),
                coverage=round(data.coverage, 3),
                metrics=report.metrics_used,
            )
        return result


__all__ = ["FundamentalResult", "FundamentalService"]
