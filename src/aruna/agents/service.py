"""Deliberation service: assemble context, run the roster, store the record."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aruna.agents.context import DecisionContext, MarketState
from aruna.agents.deliberation import Deliberation, DeliberationEngine
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.db.repositories.agents import DeliberationRepository
from aruna.db.repositories.fundamental import FundamentalRepository
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.news import NewsRepository
from aruna.db.repositories.universe import UniverseRepository
from aruna.fundamental.engine import FundamentalEngine
from aruna.fundamental.models import Fundamentals

log = get_logger("aruna.agents.service")

WINDOW = 300


@dataclass(slots=True)
class DeliberationResult:
    deliberated: int = 0
    skipped: int = 0
    blocked: int = 0
    failures: list[str] = field(default_factory=list)
    results: list[Deliberation] = field(default_factory=list)
    outcomes: dict[str, int] = field(default_factory=dict)

    def note(self, result: Deliberation) -> None:
        self.results.append(result)
        self.deliberated += 1
        key = result.outcome.value
        self.outcomes[key] = self.outcomes.get(key, 0) + 1
        if result.no_trade.blocked:
            self.blocked += 1

    def summary(self) -> str:
        parts = [f"deliberasi={self.deliberated}", f"diblokir={self.blocked}"]
        if self.skipped:
            parts.append(f"dilewati={self.skipped}")
        if self.failures:
            parts.append(f"gagal={len(self.failures)}")
        return " ".join(parts)


class DeliberationService:
    def __init__(
        self,
        *,
        universe: UniverseRepository,
        market_data: MarketDataRepository,
        news: NewsRepository,
        fundamental: FundamentalRepository,
        store: DeliberationRepository,
        engine: DeliberationEngine | None = None,
        strategist: Any = None,
        router: Any = None,
        scenario: Any = None,
    ) -> None:
        self._universe = universe
        self._market_data = market_data
        self._news = news
        self._fundamental = fundamental
        self._store = store
        self._engine = engine or DeliberationEngine()
        self._analysis = AnalysisEngine()
        self._valuation = FundamentalEngine()
        #: Pemilih strategi (PASAL 12.6), atau None kalau tidak dirangkai.
        #:
        #: Opsional dengan sengaja: seluruh jalur keputusan harus tetap
        #: berjalan tanpa dia. Sebuah lapisan pembelajaran yang menjadi syarat
        #: agar council bisa memutuskan akan mengubah kegagalan pembelajaran
        #: menjadi kegagalan analisis.
        self._strategist = strategist
        #: Pembaca pilihan router Phase 17, atau None kalau tidak dirangkai.
        #:
        #: Opsional dengan alasan yang sama seperti `strategist`: seluruh jalur
        #: keputusan harus tetap berjalan tanpa dia. Yang hilang tanpanya adalah
        #: faktor `strategy` di skor mutu - dan `Factor` memulangkan
        #: "tidak terukur", bukan nol.
        self._router = router
        #: Pembaca skenario Phase 16, dengan alasan yang sama.
        self._scenario = scenario

    async def assets_for(
        self, market: Market, symbols: tuple[str, ...] | None = None
    ) -> list:
        """Enabled assets, optionally filtered. Shared with the PHASE 6 council."""
        assets = await self._universe.assets(market=market, enabled_only=True)
        if symbols:
            wanted = {s.upper() for s in symbols}
            assets = [a for a in assets if a.symbol.upper() in wanted]
        return assets

    async def find_asset(self, market: Market, symbol: str):
        """Look up one asset. Shared with PHASE 7 outcome resolution."""
        return await self._universe.find(market, symbol)

    async def build_context(
        self, asset, market: Market, interval: Horizon, *, trading_allowed: bool = True
    ):
        """Assemble the frozen evidence pool for one asset.

        Public because the council reuses exactly this context - the two phases
        must decide from the same evidence or their records are not comparable.
        """
        return await self._build_context(
            asset, market, interval, trading_allowed=trading_allowed
        )

    async def timeframe_readings(
        self, asset, market: Market, intervals: tuple[Horizon, ...]
    ) -> tuple[Any, ...]:
        """Keputusan internal per timeframe (PASAL 14.4).

        **Jauh lebih murah daripada satu konteks penuh per timeframe.** Sebuah
        ``DecisionContext`` menarik snapshot pasar, berita, fundamental, dan
        pemilih strategi - semuanya tidak berubah menurut timeframe. Yang
        benar-benar berbeda per timeframe hanyalah candle-nya dan struktur yang
        diturunkan darinya, dan itu saja yang diambil di sini.

        Timeframe yang candle-nya tidak cukup **dilewati**, bukan diisi dengan
        tebakan. Peta lintas timeframe yang memuat baris hasil karangan lebih
        buruk daripada peta yang lebih pendek.
        """
        from aruna.decision.timeframes import reading_from_structure

        bacaan: list[Any] = []
        for interval in intervals:
            try:
                rows = await self._market_data.candles(
                    asset.id, interval, limit=WINDOW, closed_only=True
                )
                if len(rows) < 15:
                    continue
                series = CandleSeries.from_rows(
                    rows, market=market, symbol=asset.symbol, interval=interval
                )
                teknis = self._analysis.analyse(series)
            except ArunaError as exc:
                log.debug(
                    "agents.timeframe_skipped",
                    symbol=asset.symbol,
                    interval=interval.value,
                    reason=str(exc)[:120],
                )
                continue
            bacaan.append(
                reading_from_structure(
                    interval.value, getattr(teknis, "structure", None)
                )
            )
        return tuple(bacaan)

    async def run(
        self,
        market: Market,
        interval: Horizon,
        *,
        symbols: tuple[str, ...] | None = None,
        trading_allowed: bool = True,
        persist: bool = True,
    ) -> DeliberationResult:
        result = DeliberationResult()
        assets = await self._universe.assets(market=market, enabled_only=True)
        if symbols:
            wanted = {s.upper() for s in symbols}
            assets = [a for a in assets if a.symbol.upper() in wanted]
        if not assets:
            result.failures.append(f"tidak ada asset aktif untuk {market.value}")
            return result

        for asset in assets:
            try:
                context = await self._build_context(
                    asset, market, interval, trading_allowed=trading_allowed
                )
            except InsufficientData as exc:
                result.skipped += 1
                log.debug("agents.skipped", symbol=asset.symbol, reason=str(exc))
                continue
            except ArunaError as exc:
                result.failures.append(f"{asset.symbol}: {exc}")
                continue

            if context is None:
                result.skipped += 1
                continue

            deliberation = self._engine.deliberate(context)
            result.note(deliberation)
            if persist:
                await self._store.save(asset.id, deliberation)
        return result

    async def _build_context(
        self, asset, market: Market, interval: Horizon, *, trading_allowed: bool
    ) -> DecisionContext | None:
        rows = await self._market_data.candles(
            asset.id, interval, limit=WINDOW, closed_only=True
        )
        if len(rows) < 15:
            return None

        series = CandleSeries.from_rows(
            rows, market=market, symbol=asset.symbol, interval=interval
        )
        technical = self._analysis.analyse(series)

        snapshot = await self._market_data.latest_snapshot(
            market=market, symbol=asset.symbol
        )
        state = self._to_state(snapshot, fallback_price=Decimal(str(series.last_close)))

        news = ()
        rows_news = await self._news.for_symbol(asset.symbol, limit=20)
        if rows_news:
            news = tuple(_to_news_items(rows_news))

        fundamentals = valuation = None
        if market is Market.IDX:
            record = await self._fundamental.latest(asset.id)
            if record:
                fundamentals = _to_fundamentals(record)
                valuation = self._valuation.evaluate(fundamentals)

        # PASAL 12.6: rezim -> aset -> timeframe -> kecocokan historis, dan
        # hasilnya masuk ke kolam bukti SEBELUM council berdebat.
        #
        # Kegagalannya diisolasi. Pemilih strategi membaca tabel pembelajaran,
        # dan tabel itu bisa kosong, basi, atau tidak ada sama sekali; tidak
        # satu pun dari keadaan itu boleh menghentikan sebuah analisis. Yang
        # hilang saat ia gagal adalah satu baris keterangan.
        strategi = None
        if self._strategist is not None:
            try:
                strategi = await self._strategist.suggest(
                    market=market,
                    symbol=asset.symbol,
                    interval=interval,
                    regime=getattr(technical, "regime", None),
                )
            except Exception:
                log.exception("agents.strategy_suggest_failed", symbol=asset.symbol)

        # Bagian 18.14 dan 18.15. Sebelum ini Phase 16 dan 17 berjalan sebagai
        # PENGAMAT - keduanya menulis baris yang tak seorang pun di jalur
        # keputusan baca. Keduanya masuk ke kolam bukti yang sama dengan
        # indikator dan berita, dan agent boleh membantahnya seperti membantah
        # yang lain.
        #
        # Kegagalannya diisolasi dengan alasan yang sama seperti `strategi`:
        # yang hilang saat ia gagal adalah satu baris keterangan, bukan
        # analisisnya.
        pilihan = await self._baca(
            self._router, "agents.router_read_failed", asset.symbol,
            market=market, symbol=asset.symbol, as_of=technical.as_of,
        )
        skenario = await self._baca(
            self._scenario, "agents.scenario_read_failed", asset.symbol,
            market=market, symbol=asset.symbol, as_of=technical.as_of,
        )

        return DecisionContext(
            market=market,
            symbol=asset.symbol,
            interval=interval,
            as_of=technical.as_of,
            state=state,
            technical=technical,
            news=news,
            fundamentals=fundamentals,
            valuation=valuation,
            trading_allowed=trading_allowed,
            strategy=strategi,
            router=pilihan,
            scenario=skenario,
        )

    @staticmethod
    async def _baca(sumber: Any, peristiwa: str, simbol: str, **kw: Any) -> Any:
        """Baca satu sumber bukti opsional, atau ``None`` kalau ia gagal.

        Satu tempat untuk pola yang sama: sumber bukti yang tidak dirangkai
        bukan kegagalan, dan sumber yang meledak tidak boleh menghentikan
        analisis. Yang hilang saat ia gagal adalah satu baris keterangan.
        """
        if sumber is None:
            return None
        try:
            return await sumber.untuk_keputusan(**kw)
        except Exception:
            log.exception(peristiwa, symbol=simbol)
            return None

    @staticmethod
    def _to_state(snapshot: dict | None, *, fallback_price: Decimal) -> MarketState:
        if snapshot is None:
            # No live snapshot: the last settled close is the only honest
            # reference, and the venue state is unknown rather than assumed open.
            return MarketState(
                last_price=fallback_price,
                data_quality="MISSING",
                quality_detail="tidak ada snapshot market yang tercatat untuk asset ini",
            )
        return MarketState(
            last_price=snapshot["last_price"],
            bid=snapshot.get("bid"),
            ask=snapshot.get("ask"),
            spread_bps=snapshot.get("spread_bps"),
            volume_24h=snapshot.get("volume_24h"),
            change_24h_pct=snapshot.get("change_24h_pct"),
            session=snapshot.get("session_code"),
            market_open=snapshot.get("market_open"),
            is_realtime=bool(snapshot.get("is_realtime", True)),
            declared_delay_sec=int(snapshot.get("declared_delay_sec") or 0),
            data_quality=str(snapshot.get("quality") or "OK"),
            quality_detail=snapshot.get("quality_detail"),
            source=str(snapshot.get("source") or ""),
        )


def _to_news_items(rows: list[dict]):
    from aruna.news.models import Importance, NewsCategory, NewsItem, Sentiment

    for row in rows:
        yield NewsItem(
            title=row["title"],
            url=row["url"],
            source=row["source"],
            published_at=row["published_at"],
            category=NewsCategory(row["category"]),
            importance=Importance(row["importance"]),
            sentiment=Sentiment(row["sentiment"]),
            sentiment_confidence=0.5,
        )


def _to_fundamentals(row: dict) -> Fundamentals:
    def number(key: str) -> float | None:
        value = row.get(key)
        return None if value is None else float(value)

    return Fundamentals(
        symbol=row["symbol"],
        source=row["source"],
        fetched_at=row["fetched_at"],
        currency=row.get("currency"),
        sector=row.get("sector"),
        eps=number("eps"),
        roe_pct=number("roe_pct"),
        roa_pct=number("roa_pct"),
        debt_to_equity=number("debt_to_equity"),
        price_to_earnings=number("price_to_earnings"),
        price_to_book=number("price_to_book"),
        dividend_yield_pct=number("dividend_yield_pct"),
        revenue_growth_pct=number("revenue_growth_pct"),
        earnings_growth_pct=number("earnings_growth_pct"),
    )


__all__ = ["DeliberationResult", "DeliberationService"]
