"""Ingestion: provider -> quality gate -> storage.

Every observation passes the SPEC 5 gate before it is stored, and the verdict
is stored with it.  Rejected observations are still written, flagged - a
discarded bad tick teaches a later loss autopsy (SPEC 25) nothing, whereas a
recorded one distinguishes "the model was wrong" from "the data was bad".

The poll loop is deliberately not called a stream.  Binance publishes a
market-data websocket and ARUNA does not use it yet, so what runs here is
polling and the adapter declares ``Transport.POLL`` for it.  Saying otherwise
would misstate the gap between observations, which is the poll interval and
nothing shorter.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime

from aruna.core.clock import idx_active, monotonic
from aruna.core.config import DataSettings
from aruna.core.enums import DataQuality, Horizon, Market
from aruna.core.errors import ArunaError, DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.models import Candle, Quote, Snapshot
from aruna.data.perubahan import layak_simpan
from aruna.data.provider import MarketDataProvider
from aruna.data.quality import QualityGate, find_candle_gaps
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.universe import AssetRecord, UniverseRepository

log = get_logger("aruna.data.ingest")

#: Berapa tarikan candle boleh berangkat bersamaan dalam satu rombongan.
#:
#: Dua puluh, karena itu ukuran alam semesta crypto dan membuat satu tick
#: menariknya dalam satu rombongan. Angkanya membatasi dua hal sekaligus:
#: permintaan yang menganggur bersamaan, dan candle yang ditahan di memori
#: sebelum ditulis. Penulisannya tidak ikut - lihat :meth:`MarketIngestor.backfill`.
FETCH_CONCURRENCY = 20


@dataclass(slots=True)
class IngestResult:
    """What one pass actually achieved.  Failures are counted, not hidden."""

    market: Market
    provider: str
    snapshots: int = 0
    candles: int = 0
    rejected: int = 0
    failures: list[str] = field(default_factory=list)
    quality_counts: dict[str, int] = field(default_factory=dict)
    #: Snapshot yang diamati tapi tidak ditulis karena tidak membawa keterangan
    #: baru, dan sebab dari yang ditulis.  Keduanya dicatat: gerbang yang
    #: melewatkan nol baris dan gerbang yang tidak pernah dipanggil terlihat
    #: sama persis dari luar kalau hanya keputusannya yang dilaporkan.
    dilewati: int = 0
    sebab_simpan: dict[str, int] = field(default_factory=dict)

    def note_quality(self, quality: DataQuality) -> None:
        self.quality_counts[quality.value] = self.quality_counts.get(quality.value, 0) + 1
        if quality.blocks_signal:
            self.rejected += 1

    def summary(self) -> str:
        parts = [
            f"{self.provider}/{self.market.value}",
            f"snapshots={self.snapshots}",
            f"candles={self.candles}",
        ]
        if self.dilewati:
            parts.append(f"dilewati={self.dilewati}")
        if self.rejected:
            parts.append(f"rejected={self.rejected}")
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)


#: Jarak antar ringkasan gerbang di log, dalam detik.
#:
#: Lima menit. Lintasan poll berbunyi tiap lima detik per pasar; satu baris
#: INFO per lintasan adalah kebisingan yang persis membuat `ingest.pass`
#: diturunkan ke DEBUG. Satu baris per lima menit membawa jumlah kumulatifnya
#: tanpa menenggelamkan peringatan yang layak dibaca.
JEDA_RINGKASAN_DETIK = 300.0


class RingkasanGerbang:
    """Jumlah kumulatif gerbang perubahan, untuk dilaporkan berkala.

    `IngestResult.dilewati` dan `sebab_simpan` dibangun supaya gerbangnya bisa
    diperiksa saat berjalan - dan terukur 2026-08-21 sesudah restart pertama,
    keduanya hanya sampai ke `log.debug("ingest.pass", ...)` sementara produksi
    punya **nol baris DEBUG**. Pencacah yang tidak terbaca sama saja dengan
    tidak ada.
    """

    def __init__(self) -> None:
        self.disimpan = 0
        self.dilewati = 0
        self.sebab: dict[str, int] = {}
        self._terakhir: float | None = None

    def tambah(self, result: IngestResult) -> None:
        self.disimpan += result.snapshots
        self.dilewati += result.dilewati
        for nama, n in result.sebab_simpan.items():
            self.sebab[nama] = self.sebab.get(nama, 0) + n

    def ambil(self, *, sekarang: float) -> dict[str, object] | None:
        """Muatan log kalau sudah waktunya, dan kosongkan pencacahnya.

        Memulangkan `None` selama belum jatuh tempo. Jamnya dihitung dari
        laporan TERAKHIR, bukan dari awal proses: kalau dari awal, laporan
        kedua menyusul satu lintasan sesudah yang pertama dan cadence-nya
        runtuh.

        Panggilan PERTAMA memasang jangkarnya dan tidak melaporkan apa pun.
        Versi sebelumnya menjangkarkannya di nol, dan itu lolos seluruh
        testnya sementara gagal di produksi: `monotonic()` adalah uptime
        mesin, bukan nol, jadi selisih terhadap nol selalu melewati ambang dan
        laporan pertama berbunyi seketika saat start - membawa dua puluh baris
        `PERTAMA` dan `pct_dilewati=0,0`, yang persis terbaca seperti gerbang
        yang tidak menahan apa-apa.
        """
        if self._terakhir is None:
            self._terakhir = sekarang
            return None
        if sekarang - self._terakhir < JEDA_RINGKASAN_DETIK:
            return None
        self._terakhir = sekarang

        amatan = self.disimpan + self.dilewati
        muatan = {
            "disimpan": self.disimpan,
            "dilewati": self.dilewati,
            "pct_dilewati": (
                round(self.dilewati / amatan * 100, 1) if amatan else 0.0
            ),
            "sebab": dict(self.sebab),
        }
        self.disimpan = 0
        self.dilewati = 0
        self.sebab = {}
        return muatan


class MarketIngestor:
    """Ingests one market from one provider."""

    def __init__(
        self,
        *,
        provider: MarketDataProvider,
        universe: UniverseRepository,
        store: MarketDataRepository,
        settings: DataSettings,
    ) -> None:
        self._provider = provider
        self._universe = universe
        self._store = store
        self._settings = settings
        self._gate = QualityGate(
            settings,
            source=provider.name,
            declared_delay_sec=provider.capabilities.expected_delay_sec,
        )
        # Snapshot terakhir yang **tersimpan** per aset, pembanding untuk
        # gerbang perubahan.  Hidup di memori proses dan sengaja tidak
        # dipulihkan saat mulai: satu baris tambahan per aset sesudah restart
        # jauh lebih murah daripada satu kueri per aset untuk memulihkannya,
        # dan `Perubahan.PERTAMA` sudah menerangkan kenapa baris itu ada.
        self._tersimpan: dict[int, Snapshot] = {}

    @property
    def provider(self) -> MarketDataProvider:
        return self._provider

    @property
    def gate(self) -> QualityGate:
        return self._gate

    @property
    def market(self) -> Market:
        return self._provider.market

    async def assets(self) -> list[AssetRecord]:
        return await self._universe.assets(market=self.market, enabled_only=True)

    # ---- snapshots and ticks --------------------------------------------

    async def poll_once(self) -> IngestResult:
        """One observation of every enabled asset in this market."""
        result = IngestResult(market=self.market, provider=self._provider.name)
        assets = await self.assets()
        if not assets:
            result.failures.append(
                f"no enabled assets for {self.market.value}; run: aruna seed"
            )
            return result

        for asset in assets:
            try:
                await self._poll_asset(asset, result)
            except DataSourceUnavailableError as exc:
                result.failures.append(f"{asset.symbol}: {exc}")
                await self._record_failure(asset.symbol, str(exc))
            except ArunaError as exc:
                result.failures.append(f"{asset.symbol}: {exc}")
        return result

    async def _poll_asset(self, asset: AssetRecord, result: IngestResult) -> None:
        snapshot = await self._provider.fetch_snapshot(asset.symbol)

        # The snapshot's own price is judged as a quote so one gate governs all
        # price observations rather than each path inventing its own rules.
        quote = _snapshot_as_quote(snapshot)
        verdict = self._gate.evaluate_quote(quote)
        result.note_quality(verdict.quality)

        if self._provider.capabilities.supports_order_book:
            snapshot = await self._with_depth(asset.symbol, snapshot, result)

        stored = snapshot
        if not verdict.ok:
            stored = replace(
                snapshot, quality=verdict.quality, quality_detail=verdict.detail or None
            )
            # Membaca ulang observasi yang sama bukan kejadian yang perlu
            # diteriakkan. Kelasnya tetap tersimpan pada baris snapshot dan
            # tetap ikut dihitung `note_quality`, jadi tidak ada yang
            # disembunyikan - yang dihentikan adalah satu baris peringatan dan
            # satu baris provider_event untuk setiap kali ARUNA bertanya lebih
            # cepat daripada providernya menjawab.
            #
            # Terukur sebelum diubah: 1165 peringatan dan 1165 baris kejadian
            # per enam jam, seluruhnya dari Yahoo, nol dari Binance. Yang
            # sesungguhnya perlu diketahui operator - "feed ini tidak
            # menghasilkan apa pun sejak sekian lama" - adalah STALE, dan STALE
            # tetap berbunyi keras.
            if verdict.quality is not DataQuality.REPEATED_READ:
                log.warning(
                    "ingest.quality_rejected",
                    provider=self._provider.name,
                    symbol=asset.symbol,
                    quality=verdict.quality.value,
                    detail=verdict.detail,
                )
                await self._store.record_provider_event(
                    provider=self._provider.name,
                    market=self.market,
                    symbol=asset.symbol,
                    event_type="QUALITY_REJECTED",
                    quality=verdict.quality,
                    message=verdict.detail or verdict.quality.value,
                    latency_ms=snapshot.provenance.latency_ms,
                )

        # One row per poll per asset, and no per-tick row beside it: PASAL 26
        # keeps SQL for long-term analysis memory, not for a tape of every
        # observation. The quality verdict travels on this row, so nothing that
        # was actually read back has been lost.
        #
        # Catatan sebelumnya di tempat ini mengaku bahwa penulisan snapshot
        # masih satu INSERT per poll per aset tanpa penyaringan sendiri, dan
        # menundanya karena "thinning or relocating it is a decision about
        # those readers".  Keputusan itu sekarang bisa diambil: audit
        # 2026-08-21 menemukan tabel ini punya **tepat tiga pembaca**
        # (`agents/service`, bot Telegram, permukaan pasar) dan **ketiganya
        # hanya membaca baris terbaru per simbol**.  Tidak ada pembaca sejarah
        # yang perlu dipikirkan.  Yang tersimpan: 422.172 baris, 286 MB, 62%
        # basis data, 60.227 di antaranya redundan secara isi.
        #
        # Pembandingnya adalah snapshot terakhir yang benar-benar **tersimpan**,
        # bukan yang terakhir dilihat.  Kalau yang terakhir dilihat yang
        # dipakai, harga bisa merambat melewati ambang berkali-kali tanpa satu
        # baris pun ditulis - setiap langkah kecil dibandingkan dengan langkah
        # kecil sebelumnya, dan tak satu pun melewati ambang.
        terakhir = self._tersimpan.get(asset.id)
        sejak = (
            (stored.captured_at - terakhir.captured_at).total_seconds()
            if terakhir is not None
            else 0.0
        )
        simpan, sebab = layak_simpan(stored, terakhir, sejak_detik=sejak)
        if not simpan:
            result.dilewati += 1
            return

        await self._store.record_snapshot(asset.id, stored)
        self._tersimpan[asset.id] = stored
        result.snapshots += 1
        for s in sebab:
            result.sebab_simpan[s.value] = result.sebab_simpan.get(s.value, 0) + 1

    async def _with_depth(
        self, symbol: str, snapshot: Snapshot, result: IngestResult
    ) -> Snapshot:
        """Snapshot with order-book depth folded in, when the venue has one.

        Depth is optional context: if the book request fails, the snapshot is
        still worth storing without it.
        """
        try:
            book = await self._provider.fetch_order_book(symbol, depth=20)
        except DataSourceUnavailableError as exc:
            result.failures.append(f"{symbol} depth: {exc}")
            return snapshot
        if book is None:
            return snapshot
        if book.is_crossed:
            log.warning("ingest.crossed_book", symbol=symbol, provider=self._provider.name)
        return replace(snapshot, bid_depth=book.bid_depth, ask_depth=book.ask_depth)

    async def _record_failure(self, symbol: str, message: str) -> None:
        await self._store.record_provider_event(
            provider=self._provider.name,
            market=self.market,
            symbol=symbol,
            event_type="REQUEST_FAILED",
            message=message[:500],
        )

    # ---- candles ---------------------------------------------------------

    async def backfill(
        self,
        intervals: tuple[Horizon, ...],
        *,
        limit: int | None = None,
        symbols: tuple[str, ...] | None = None,
        quiet: bool = False,
        detect_gaps: bool = True,
    ) -> IngestResult:
        """Pull candle history for the given intervals.

        ``quiet`` drops the per-asset success line to DEBUG.  A periodic
        refresher calls this once a minute per asset per interval; at INFO that
        is thousands of lines a day saying nothing happened, and the warnings
        worth reading drown in them.

        ``detect_gaps`` exists for the same reason in a more dangerous form.
        Gap detection only sees the window just fetched, so a routine
        three-bar refresh cannot find anything a fetch of the same three bars
        did not already find - but a catch-up pass re-reports the *same* old
        hole on every attempt until it is closed, filling ``provider_events``
        with duplicates and teaching the operator to ignore ``GAP_DETECTED``.
        The caller turns it on when the window is wide enough for the answer to
        be new. Nothing here fills a gap either way (SPEC 4).

        **Penarikan bersamaan, penulisan bergiliran - dan bentuk itu dipilih
        sesudah bentuk yang lebih naif gagal.**

        Versi pertama menjalankan seluruh ``_backfill_one`` per simbol secara
        bersamaan. Enam dari dua puluh simbol gagal dengan ``OperationalError
        1213: Deadlock found``: upsert candle yang bersamaan saling mengunci di
        InnoDB. Yang ditukar bukan sekadar pesan error - simbol yang gagal
        disegarkan tetap dianalisis, dari bar lama, yang persis kegagalan yang
        penyegaran ini dibangun untuk mencegah.

        Jadi yang dibuat bersamaan hanya penarikannya. Terukur: satu tarikan
        ~200 ms, penulisannya ~20 ms - sembilan puluh persen waktunya adalah
        menunggu jaringan, dan menunggu bisa dilakukan berbarengan. Penulisan
        tetap satu per satu di dalam satu panggilan ini.

        **Dan satu panggilan ini bukan seluruh dunianya.** Paragraf di atas dulu
        berakhir dengan "deadlock tidak bisa terjadi karena struktur", dan itu
        salah: yang digilir hanyalah penulisan di dalam SATU ``backfill``.
        ``backfill`` punya dua pemanggil di dua proses berbeda - penyegar candle
        upkeep di ``aruna-run`` dan ``refresh_evidence`` di ``futures-loop`` -
        dan tidak ada apa pun di sini yang menggilir mereka satu sama lain.
        Deadlock 1213 yang sama karena itu tetap terjadi, dari tetangga yang
        berbeda: bar ``4h`` milik futures duduk persis di sebelah bar ``1m``
        milik upkeep di ``candles_unique``. Yang benar-benar menegakkan janji
        ini sekarang ada di :meth:`~aruna.db.repositories.market_data.
        MarketDataRepository.upsert_candles`, satu tingkat lebih bawah, tempat
        setiap penulis candle - berapa pun jumlah pemanggil dan prosesnya -
        harus lewat.

        Dikerjakan per rombongan, bukan sekali gather untuk semuanya: rombongan
        membatasi berapa banyak candle yang ditahan di memori sekaligus.
        Sekali-gather atas seratus aset kali empat interval kali seribu lima
        ratus bar adalah kebutuhan memori yang tidak dibatasi apa pun.
        """
        result = IngestResult(market=self.market, provider=self._provider.name)
        count = limit or self._settings.backfill_candles
        assets = await self.assets()
        if symbols:
            wanted = {s.upper() for s in symbols}
            assets = [a for a in assets if a.symbol.upper() in wanted]

        pekerjaan: list[tuple[AssetRecord, Horizon]] = []
        for asset in assets:
            for interval in intervals:
                if not self._provider.capabilities.supports(interval):
                    result.failures.append(
                        f"{asset.symbol} {interval.value}: not offered by "
                        f"{self._provider.name}"
                    )
                    continue
                pekerjaan.append((asset, interval))

        for awal in range(0, len(pekerjaan), FETCH_CONCURRENCY):
            rombongan = pekerjaan[awal : awal + FETCH_CONCURRENCY]

            async def tarik(
                asset: AssetRecord, interval: Horizon
            ) -> tuple[list[Candle], float]:
                mulai = monotonic()
                candles = await self._provider.fetch_candles(
                    asset.symbol, interval, limit=count
                )
                return candles, monotonic() - mulai

            ditarik = await asyncio.gather(
                *(tarik(a, i) for a, i in rombongan), return_exceptions=True
            )

            # Ditulis sesudahnya, berurutan, dan dalam urutan pekerjaannya -
            # bukan urutan selesainya jaringan. Log dan `result.failures` jadi
            # bisa dibaca dua kali dan sama.
            for (asset, interval), hasil in zip(rombongan, ditarik, strict=True):
                if isinstance(hasil, BaseException):
                    if not isinstance(hasil, DataSourceUnavailableError | ValueError):
                        raise hasil
                    result.failures.append(f"{asset.symbol} {interval.value}: {hasil}")
                    continue
                candles, lama = hasil
                try:
                    await self._store_backfill(
                        asset,
                        interval,
                        candles,
                        result,
                        fetch_sec=lama,
                        quiet=quiet,
                        detect_gaps=detect_gaps,
                    )
                except (DataSourceUnavailableError, ValueError) as exc:
                    result.failures.append(f"{asset.symbol} {interval.value}: {exc}")
        return result

    async def _store_backfill(
        self,
        asset: AssetRecord,
        interval: Horizon,
        candles: list[Candle],
        result: IngestResult,
        *,
        fetch_sec: float,
        quiet: bool = False,
        detect_gaps: bool = True,
    ) -> None:
        """Nilai mutu lalu simpan satu tarikan. Tidak menyentuh jaringan.

        Dipanggil satu per satu dari :meth:`backfill`, dan itu syarat, bukan
        kebetulan: dua panggilan ini yang berjalan bersamaan adalah deadlock
        InnoDB yang dijelaskan di sana.
        """
        started = monotonic()
        if not candles:
            result.failures.append(f"{asset.symbol} {interval.value}: provider returned none")
            return

        usable = []
        for candle in candles:
            verdict = self._gate.evaluate_candle(candle)
            if verdict.ok:
                usable.append(candle)
                continue
            result.note_quality(verdict.quality)
            log.warning(
                "ingest.candle_rejected",
                symbol=asset.symbol,
                interval=interval.value,
                open_time=candle.open_time.isoformat(),
                detail=verdict.detail,
            )

        # **Bar yang belum tutup tidak disimpan.**
        #
        # SPEC 24 menyatakan alasannya dalam satu kalimat: bar yang terbuka
        # masih berubah sesudah dibaca, jadi memakainya sebagai bukti yang
        # sudah selesai adalah look-ahead. Sampai baris ini ada, ia tetap
        # DITULIS ke tabel - dan setiap pembaca yang lupa menyaring
        # ``is_closed`` memakainya sebagai bar biasa.
        #
        # Terukur saat ditemukan: 275 dari 6.050 bar 15m IDX belum tutup,
        # bervolume nol, dan stempel waktunya adalah detik pengambilannya -
        # ``02:05:35`` alih-alih ``02:00:00``. Ia bukan bar; ia potret sesaat
        # yang menyamar sebagai bar, dan ia menempati posisi "bar terbaru"
        # dalam setiap kueri yang mengurutkan menurut ``open_time``.
        #
        # Harga hidup tidak hilang karenanya: ia datang dari snapshot dan
        # aliran WebSocket, bukan dari tabel candle.
        tertutup = [c for c in usable if c.is_closed]
        terbuka = len(usable) - len(tertutup)
        if terbuka and not tertutup:
            # Seluruh tarikan berisi bar berjalan. Itu bukan "tidak ada yang
            # baru" - ia bisa berarti provider menandai semuanya salah, dan
            # diam di sini akan membuat seri berhenti tumbuh tanpa satu pun
            # tanda.
            log.warning(
                "ingest.all_bars_open",
                symbol=asset.symbol,
                interval=interval.value,
                count=terbuka,
                detail="tidak ada bar tertutup pada tarikan ini; tidak ada yang disimpan",
            )
        elif terbuka:
            log.debug(
                "ingest.open_bars_skipped",
                symbol=asset.symbol,
                interval=interval.value,
                count=terbuka,
            )

        written = await self._store.upsert_candles(asset.id, tertutup)
        result.candles += written

        # Gaps are reported, never filled: an interpolated bar is fabricated
        # data (SPEC 4) that would corrupt any backtest built on it (SPEC 35).
        gaps = find_candle_gaps(tertutup) if detect_gaps else []
        if gaps:
            missing = sum(g[2] for g in gaps)
            log.warning(
                "ingest.gaps_detected",
                symbol=asset.symbol,
                interval=interval.value,
                gaps=len(gaps),
                missing_bars=missing,
            )
            await self._store.record_provider_event(
                provider=self._provider.name,
                market=self.market,
                symbol=asset.symbol,
                event_type="GAP_DETECTED",
                message=f"{len(gaps)} gap(s), {missing} bar(s) missing at {interval.value}",
                details={
                    "interval": interval.value,
                    "gaps": [
                        {"from": g[0].isoformat(), "to": g[1].isoformat(), "missing": g[2]}
                        for g in gaps[:20]
                    ],
                },
            )

        log.log(
            logging.DEBUG if quiet else logging.INFO,
            "ingest.backfilled",
            symbol=asset.symbol,
            interval=interval.value,
            candles=written,
            # Dua angka, bukan satu: tarikannya berbagi jendela dengan tarikan
            # lain di rombongan yang sama, penulisannya tidak. Menjumlahkannya
            # jadi satu `duration_ms` akan menyembunyikan yang mana yang lambat.
            fetch_ms=round(fetch_sec * 1000, 1),
            write_ms=round((monotonic() - started) * 1000, 1),
        )


class IngestService:
    """Owns every market's ingestor and the shared poll loop."""

    def __init__(self, ingestors: dict[Market, MarketIngestor], settings: DataSettings) -> None:
        self._ingestors = ingestors
        self._settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._last_results: dict[Market, IngestResult] = {}
        #: Pencacah gerbang perubahan, dilaporkan lima menit sekali di INFO.
        self._ringkasan = RingkasanGerbang()
        #: Jam monotonik saat IDX terakhir dipoll. Monotonik, bukan jam dinding:
        #: jam mesin ini pernah terukur meleset lima belas detik, dan sebuah
        #: koreksi jam tidak boleh membuat loop melewatkan satu jendela poll.
        self._last_idx_poll: float | None = None

    @property
    def markets(self) -> tuple[Market, ...]:
        return tuple(self._ingestors)

    def ingestor(self, market: Market) -> MarketIngestor | None:
        return self._ingestors.get(market)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def last_result(self, market: Market) -> IngestResult | None:
        return self._last_results.get(market)

    async def open(self) -> None:
        for ingestor in self._ingestors.values():
            await ingestor.provider.open()

    async def close(self) -> None:
        await self.stop()
        for ingestor in self._ingestors.values():
            await ingestor.provider.close()

    def _idx_due(self) -> bool:
        """Sudah cukup jauh dari poll IDX terakhir?

        Cadence-nya dipisah dari crypto karena feed-nya berbeda kecepatan, dan
        loop yang satu kecepatan akan selalu salah untuk salah satunya. Poll
        pertama selalu lolos: tanpa itu, sebuah perintah sekali jalan akan
        diam tanpa alasan yang bisa dilihat pemanggilnya.
        """
        batas = self._settings.idx_poll_interval_sec
        sebelumnya = self._last_idx_poll
        sekarang = monotonic()
        if sebelumnya is not None and sekarang - sebelumnya < batas:
            return False
        self._last_idx_poll = sekarang
        return True

    async def poll_once(self) -> dict[Market, IngestResult]:
        results: dict[Market, IngestResult] = {}
        for market, ingestor in self._ingestors.items():
            if market is Market.IDX:
                # IDX is closed most of the day; polling a closed venue burns
                # rate limit to re-record the same close (SPEC 3).
                if not idx_worth_polling():
                    continue
                # Dan bahkan saat bursanya buka, feed-nya menjawab jauh lebih
                # lambat daripada loop ini bertanya. Lihat
                # `idx_poll_interval_sec` untuk angkanya dan dari mana ia
                # diukur.
                if not self._idx_due():
                    continue
            results[market] = await ingestor.poll_once()
        self._last_results.update(results)
        return results

    async def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="aruna-ingest")
        log.info(
            "ingest.started",
            markets=[m.value for m in self._ingestors],
            interval_sec=self._settings.poll_interval_sec,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        log.info("ingest.stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                results = await self.poll_once()
                for result in results.values():
                    self._ringkasan.tambah(result)
                    if result.failures:
                        log.warning("ingest.pass_incomplete", detail=result.summary())
                    else:
                        log.debug("ingest.pass", detail=result.summary())
                # Ringkasan gerbang perubahan, lima menit sekali di INFO.
                #
                # Barisnya sendiri tetap DEBUG dengan alasan yang tidak
                # berubah - ia berbunyi tiap lima detik per pasar. Yang naik ke
                # INFO adalah jumlah kumulatifnya, karena pencacah yang tidak
                # pernah terbaca sama saja dengan tidak ada: produksi punya nol
                # baris DEBUG.
                muatan = self._ringkasan.ambil(sekarang=monotonic())
                if muatan is not None:
                    log.info("ingest.gerbang", **muatan)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ingest.pass_failed")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._settings.poll_interval_sec
                )
            except TimeoutError:
                continue


def idx_worth_polling(moment: datetime | None = None) -> bool:
    """True while IDX work is worth doing at all.

    Outside that, the last price is already recorded and will not change, so
    polling only burns rate limit to rewrite the same close (SPEC 3).

    Public because the candle refresher must gate on the *same* predicate the
    quote poll uses.  A second copy of this rule would drift from this one, and
    the two would then disagree about whether the exchange is open - with only
    the rate-limit bill to reveal it.

    Diteruskan ke :func:`aruna.core.clock.idx_active`, yang sekarang menjadi
    satu-satunya tempat aturan itu ditulis. Versi lama mengejanya sendiri -
    "buka, atau sesi OPENING/CLOSING" - dan itu tepat kelas duplikasi yang
    diperingatkan paragraf di atas: dua aturan yang harus sepakat, ditulis dua
    kali. Ia juga tidak punya jendela pemanasan, sehingga penarikan data baru
    dimulai saat bursa sudah berjalan.
    """
    return idx_active(moment)


def _snapshot_as_quote(snapshot: Snapshot) -> Quote:
    return Quote(
        market=snapshot.market,
        symbol=snapshot.symbol,
        price=snapshot.last_price,
        bid=snapshot.bid,
        ask=snapshot.ask,
        provenance=snapshot.provenance,
    )


__all__ = ["IngestResult", "IngestService", "MarketIngestor", "idx_worth_polling"]
