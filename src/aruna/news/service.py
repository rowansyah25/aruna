"""News ingestion service (SPEC 8).

Fetches every configured feed, deduplicates across outlets that syndicate each
other, links items to assets, and stores them. A feed that fails is recorded and
the rest continue - one dead outlet must not blank out the news view.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.core.enums import Market
from aruna.core.errors import ArunaError, DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.db.repositories.news import NewsRepository
from aruna.db.repositories.universe import UniverseRepository
from aruna.news.rss import RssNewsProvider

log = get_logger("aruna.news")

#: Berapa kegagalan berturut-turut sebelum sebuah feed disebut bermasalah.
#:
#: Tiga, pada jadwal lima menit: seperempat jam tanpa satu berita pun dari
#: sumber itu. Di bawahnya, kegagalannya sudah pulih sebelum ada yang sempat
#: membacanya - dan peringatan yang isinya "tadi sempat gagal, sekarang tidak"
#: hanya melatih pembacanya melewati baris WARNING.
FEED_GAGAL_BERUNTUN = 3


@dataclass(slots=True)
class NewsResult:
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    linked: int = 0
    failures: list[str] = field(default_factory=list)
    by_source: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"fetched={self.fetched}",
            f"stored={self.stored}",
            f"duplicates={self.duplicates}",
            f"linked={self.linked}",
        ]
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


class NewsService:
    def __init__(
        self,
        *,
        provider: RssNewsProvider,
        store: NewsRepository,
        universe: UniverseRepository,
    ) -> None:
        self._provider = provider
        self._store = store
        self._universe = universe
        self._aliases: dict[str, tuple[str, ...]] = {}
        self._asset_ids: dict[str, int] = {}

    async def open(self) -> None:
        await self._provider.open()
        await self._refresh_aliases()

    async def close(self) -> None:
        await self._provider.close()

    async def _refresh_aliases(self) -> None:
        """Build the symbol->aliases map used to link items to assets.

        For IDX the ticker itself is the alias; for crypto the base asset and
        its common name are both worth matching, since coverage rarely writes
        the pair form 'BTC/USDT'.
        """
        aliases: dict[str, tuple[str, ...]] = {}
        asset_ids: dict[str, int] = {}
        names = {
            "BTC": ("btc", "bitcoin"),
            "ETH": ("eth", "ethereum"),
            "SOL": ("sol", "solana"),
            "BNB": ("bnb",),
            "XRP": ("xrp", "ripple"),
        }
        for market in (Market.CRYPTO, Market.IDX):
            for asset in await self._universe.assets(market=market, enabled_only=True):
                asset_ids[asset.symbol] = asset.id
                if market is Market.IDX:
                    aliases[asset.symbol] = (asset.symbol,)
                else:
                    base = (asset.base_asset or "").upper()
                    aliases[asset.symbol] = names.get(base, (base.lower(),))
        self._aliases = aliases
        self._asset_ids = asset_ids
        #: Kegagalan berturut-turut per feed. Direset saat feed itu pulih.
        self._gagal_beruntun: dict[str, int] = {}

    def _catat_gagal(self, nama: str, exc: Exception) -> None:
        """Feed yang sesekali putus dicatat pelan; yang benar-benar mati
        berteriak.

        **Terukur sebelum aturan ini ada:** 14 kegagalan dari 134 siklus,
        tersebar di tiga feed berbeda - coindesk 7, kontan 6, detik 1. Itu
        bukan feed yang mati, itu internet yang sesekali putus, dan tiap satunya
        menulis satu baris WARNING. Peringatan yang muncul sepuluh persen waktu
        berhenti dibaca, dan yang hilang berikutnya adalah feed yang benar-benar
        berhenti mengirim.

        Ambangnya berturut-turut, bukan total: sebuah feed yang gagal sekali
        lalu pulih tidak sedang bermasalah, sementara tiga kegagalan beruntun
        pada jadwal lima menit berarti lima belas menit tanpa satu berita pun
        dari sumber itu.

        Pemulihannya juga dicatat. Sebuah peringatan yang tidak pernah dicabut
        meninggalkan pembacanya menduga masalahnya masih ada.
        """
        beruntun = self._gagal_beruntun.get(nama, 0) + 1
        self._gagal_beruntun[nama] = beruntun
        if beruntun >= FEED_GAGAL_BERUNTUN:
            log.warning(
                "news.feed_unavailable",
                feed=nama,
                consecutive=beruntun,
                error=str(exc),
            )
        else:
            log.debug("news.feed_blip", feed=nama, error=str(exc))

    def _catat_pulih(self, nama: str) -> None:
        beruntun = self._gagal_beruntun.pop(nama, 0)
        if beruntun >= FEED_GAGAL_BERUNTUN:
            log.info("news.feed_recovered", feed=nama, after=beruntun)

    async def ingest(self) -> NewsResult:
        result = NewsResult()
        if not self._aliases:
            await self._refresh_aliases()

        seen: set[str] = set()
        for feed in self._provider.feeds:
            try:
                items = await self._provider.fetch(feed, symbol_aliases=self._aliases)
            except DataSourceUnavailableError as exc:
                result.failures.append(f"{feed.name}: {exc}")
                self._catat_gagal(feed.name, exc)
                continue
            except ArunaError as exc:
                result.failures.append(f"{feed.name}: {exc}")
                continue

            self._catat_pulih(feed.name)
            result.fetched += len(items)
            result.by_source[feed.name] = len(items)

            for item in items:
                # Outlets syndicate each other; the fingerprint keeps one copy.
                if item.fingerprint in seen:
                    result.duplicates += 1
                    continue
                seen.add(item.fingerprint)

                inserted = await self._store.upsert(item)
                if inserted:
                    result.stored += 1
                else:
                    result.duplicates += 1

                # One story can name several assets, so each link is its own
                # row. Written on every pass, not only on insert: a reclassified
                # item may name an asset the first pass did not match.
                for symbol in item.symbols:
                    asset_id = self._asset_ids.get(symbol)
                    if asset_id is None:
                        continue
                    await self._store.link_asset(item.fingerprint, asset_id, symbol)
                    result.linked += 1

        log.info("news.ingested", **{"detail": result.summary()})
        return result


__all__ = ["NewsResult", "NewsService"]
