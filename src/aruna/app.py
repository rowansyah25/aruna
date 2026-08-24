"""Application lifecycle.

Startup order is deliberate and each step's failure mode is chosen:

1. **logging** - so every later failure is visible and redacted;
2. **database** - fatal.  MySQL is the store of record for predictions and
   audit entries; without it ARUNA has nowhere to put evidence;
3. **schema check** - fatal if migrations are pending.  Running application
   code against a schema it does not match is how silent data corruption
   starts;
4. **runtime state** - a kill switch engaged before a restart must still be
   engaged after it;
5. **cache** - non-fatal.  Redis is a cache (see ``aruna.cache``);
6. **Telegram** - non-fatal.  ARUNA runs headless and says so;
7. **health monitor** - last, so its first sweep sees the finished system.

Shutdown runs in reverse and never lets one failing component prevent the rest
from closing.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

from aruna import __version__
from aruna.analysis.service import AnalysisService
from aruna.cache.redis_client import Cache
from aruna.core.clock import now_utc
from aruna.core.config import CURRENT_PHASE, Settings, get_settings
from aruna.core.enums import EventSeverity, HealthStatus, Market
from aruna.core.errors import ArunaError, ConfigError, StartupError, TelegramError
from aruna.core.logging import configure_logging, get_logger
from aruna.core.redaction import REDACTOR
from aruna.core.runtime_state import KILL_SWITCH_KEY, KillSwitchState, RuntimeState
from aruna.data.crypto.stream import BinanceSpotStream
from aruna.data.ingest import IngestService, MarketIngestor
from aruna.data.registry import build_providers
from aruna.db.migrator import Migrator
from aruna.db.pool import Database
from aruna.db.repositories import (
    AnalysisRepository,
    AppStateRepository,
    AuditRepository,
    CorrelationRepository,
    DeliberationRepository,
    FundamentalRepository,
    MarketDataRepository,
    NewsRepository,
    SystemEventRepository,
    TelegramSubscriberRepository,
    UniverseRepository,
)
from aruna.fundamental.service import FundamentalService
from aruna.fundamental.yahoo import YahooFundamentalProvider
from aruna.health import heartbeat
from aruna.health.alerts import HealthAlertPolicy
from aruna.health.checks import (
    ClockCheck,
    ConfigCheck,
    DatabaseCheck,
    ProcessCheck,
    RedisCheck,
    TelegramCheck,
)
from aruna.health.models import ComponentHealth, HealthReport
from aruna.health.monitor import HealthMonitor
from aruna.health.providers import ProviderCheck
from aruna.health.stream import StreamCheck
from aruna.health.ukuran import UkuranDatabaseCheck
from aruna.health.upkeep import CandleFreshnessCheck, UpkeepCheck
from aruna.news.rss import RssNewsProvider
from aruna.news.service import NewsService
from aruna.notify.telegram import formatting as fmt
from aruna.notify.telegram.bot import BotDeps, TelegramBot
from aruna.scanner.service import ScannerService
from aruna.upkeep.candles import CandleRefresher, refresh_intervals
from aruna.upkeep.loop import STOP_GRACE_SEC, UpkeepLoop

log = get_logger("aruna.app")

#: Redis key holding the latest health report, for out-of-process readers.
HEALTH_CACHE_KEY = ("health", "latest")
HEALTH_CACHE_TTL_SEC = 300

#: Jeda pertama sebelum mencoba menyalakan bot Telegram lagi, dan batas
#: atasnya. Melebar dua kali lipat tiap kegagalan.
#:
#: Tiga puluh detik cukup cepat untuk menyusul sumbatan sesaat; sepuluh menit
#: cukup jarang untuk tidak memenuhi log selama pemblokiran berjam-jam.
TELEGRAM_RETRY_MIN_SEC = 30.0
TELEGRAM_RETRY_MAX_SEC = 600.0


class _LateSender:
    """Bot yang dicari saat mau mengirim, bukan saat dibangun.

    Beberapa komponen dibangun sebelum bot Telegram ada. Menyimpan
    ``self.bot`` apa adanya di saat itu berarti menyimpan ``None`` selamanya,
    dan komponen itu diam-diam menjadi tidak berfungsi tanpa satu pun error.
    """

    def __init__(self, resolve: Any) -> None:
        self._resolve = resolve

    def ready(self) -> bool:
        return self._resolve() is not None

    async def send(self, text: str) -> bool:
        bot = self._resolve()
        return False if bot is None else bool(await bot.send(text))

    async def send_id(self, text: str, *, reply_to: int | None = None) -> int | None:
        """Kirim dan kembalikan id pesannya, kalau pengirimnya bisa.

        Jatuh kembali ke :meth:`send` untuk pengirim yang tidak mengenal
        ``send_id`` - dan mengembalikan ``0`` di situ, bukan ``None``. Nol
        berarti "terkirim, id-nya tidak diketahui"; ``None`` berarti "tidak
        terkirim". Menyamakan keduanya akan membuat pengirim tanpa id terlihat
        seperti pengirim yang gagal, dan seluruh hasil yang mengikutinya ikut
        dibungkam.
        """
        bot = self._resolve()
        if bot is None:
            return None
        kirim = getattr(bot, "send_id", None)
        if kirim is None:
            return 0 if await bot.send(text) else None
        return await kirim(text, reply_to=reply_to)


class ArunaApplication:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.phase = CURRENT_PHASE
        # Stamped onto every locked prediction. SPEC 39 replay is only
        # meaningful if a stored signal names the build that produced it.
        self.model_version = f"{__version__}+phase{CURRENT_PHASE}"

        self.db = Database(self.settings.db)
        self.cache = Cache(self.settings.redis)
        self.state = RuntimeState()

        self.app_state: AppStateRepository | None = None
        self.audit: AuditRepository | None = None
        self.events: SystemEventRepository | None = None
        self.subscribers: TelegramSubscriberRepository | None = None
        self.universe: UniverseRepository | None = None
        self.market_data: MarketDataRepository | None = None
        self.analysis_store: AnalysisRepository | None = None
        self.news_store: NewsRepository | None = None
        self.fundamental_store: FundamentalRepository | None = None
        self.correlation_store: CorrelationRepository | None = None
        #: Ingatan pasar (PASAL 15.2). Publik karena **dua proses** memakainya:
        #: `aruna run` memproyeksikannya lewat loop upkeep, dan `futures-loop`
        #: membacanya di jalur keputusan. Satu tempat pembangunan, supaya
        #: keduanya tidak bisa berselisih.
        self.memory_store: Any = None
        self.deliberation_store: DeliberationRepository | None = None
        self.deliberation: Any = None
        self.council_store: Any = None
        self.council: Any = None
        self.signal_store: Any = None
        self.signals: Any = None
        self.learning_store: Any = None
        self.learning: Any = None
        self.history: Any = None
        self.backtest_store: Any = None
        self.backtest: Any = None
        self.governance_store: Any = None
        self.governance: Any = None

        self.bot: TelegramBot | None = None
        self.monitor: HealthMonitor | None = None
        self.ingest: IngestService | None = None
        self.stream: BinanceSpotStream | None = None
        self.upkeep: UpkeepLoop | None = None
        self.analysis: AnalysisService | None = None
        self.news: NewsService | None = None
        self.fundamental: FundamentalService | None = None

        self._running = False
        self._background = True
        #: The first health sweep transitions every component from "no prior
        #: status", which is startup rather than an incident. One-shot, because
        #: a recovery looks identical to a clean first sweep.
        self._health_alerted = False
        #: Peralihan mana yang layak membangunkan operator (PASAL 17, 18).
        self._alert_policy = HealthAlertPolicy()
        self._stop_requested = asyncio.Event()
        #: Task yang mencoba menyalakan bot Telegram lagi, kalau startup gagal
        #: karena jaringan. Lihat :meth:`_retry_telegram`.
        self._telegram_retry: asyncio.Task[None] | None = None

    # ---- startup -------------------------------------------------------

    def configure_logging(self) -> None:
        configure_logging(
            level=self.settings.log.level,
            fmt=self.settings.log.format,
            log_dir=self.settings.log.resolved_dir(),
            file_enabled=self.settings.log.file_enabled,
            secrets=self.settings.secrets(),
            instance=self.settings.app.instance_name,
            env=self.settings.app.env,
            phase=self.phase,
        )

    async def startup(self, *, background: bool = True) -> None:
        """Bring the system up.

        ``background=False`` wires everything but starts no periodic loops - the
        right shape for one-shot CLI commands, which otherwise race their own
        explicit work against a poller they did not ask for.
        """
        self._background = background
        self.configure_logging()
        log.info("aruna.starting", **self.settings.describe())
        for notice in self.settings.phase_notices():
            log.info("config.phase_notice", detail=notice)
        for warning in self.settings.startup_warnings():
            log.warning("config.warning", detail=warning)

        await self._start_database()
        self._build_repositories()
        await self._verify_schema()
        await self._load_runtime_state()
        await self._load_measured_history()
        await self.cache.connect()
        await self._start_ingestion()
        await self._start_upkeep()
        if background:
            # Telegram polling IS a periodic loop, and the docstring above
            # promised not to start one here. It started anyway, and the cost
            # was not cosmetic:
            #
            # * Telegram allows exactly ONE getUpdates consumer per token, so
            #   every short CLI command stole the token from `aruna run` for
            #   the length of its own run. A second instance then fails to
            #   start - which is why a `futures-loop` bot timed out while the
            #   real bot was up.
            # * Each of those commands announced "ARUNA ONLINE" on the way in
            #   and "Shutting down. No signals until restart." on the way out.
            #   To anyone reading the chat, ARUNA looked like it kept dying.
            #   It was not dying; it was a `plan` command finishing normally.
            #
            # Only the long-running process may hold the bot.
            await self._start_telegram()
        await self._start_health_monitor()

        self._running = True
        await self._record_event(
            component="application",
            event_type="STARTUP",
            severity=EventSeverity.INFO,
            message=f"ARUNA started (PHASE {self.phase})",
            details=self.settings.describe(),
        )
        await self._audit("system", "APPLICATION_START", detail=f"phase {self.phase}")

        # **Tidak ada pesan "ARUNA menyala" (PASAL 14.38).**
        #
        # Pasal itu mendaftar tujuh jenis pesan yang boleh berangkat tanpa
        # diminta, dan peristiwa proses bukan salah satunya. Alasannya bukan
        # kerapian: "ARUNA menyala" tidak memberi operator satu pun keputusan
        # untuk diambil - kalau ia menyala, signal akan datang sendiri.
        #
        # Terukur pada 2026-08-19: bot-nya menyala lima kali dalam tujuh jam,
        # semuanya dari restart rutin dan penjaga proses. Lima notifikasi yang
        # tidak mengubah apa pun, dan tiap satunya membeli sedikit perhatian
        # yang tidak dikembalikan. Peristiwanya tetap tercatat penuh di
        # `_record_event`, di audit, dan di log - yang berhenti hanyalah
        # membangunkan ponsel operator untuk itu.
        #
        # Yang TETAP dikirim adalah waktu mati, dan itu jenis pesan yang
        # berbeda: HEALTH ALERT (PASAL 14.38). Ia hanya berbunyi kalau ARUNA
        # benar-benar hilang cukup lama, dan yang dilindunginya bukan rasa
        # ingin tahu melainkan kesalahan baca - diam yang panjang terlihat
        # persis sama dengan "tidak ada setup".
        await self._report_downtime()

        log.info("aruna.started", phase=self.phase)

    async def _report_downtime(self) -> None:
        """Beritahu operator kalau ARUNA baru saja mati cukup lama.

        **Yang melapor adalah yang bangun, bukan yang mati.** Proses yang
        dibunuh paksa tidak sempat mengirim apa pun - terukur nol dari dua
        puluh dua penghentian - jadi satu-satunya pelapor yang bisa diandalkan
        adalah proses berikutnya. Laporannya terlambat, dan itu diakui: ia
        datang saat ARUNA kembali, bukan saat ia pergi.

        Diam-diam gagal kalau denyutnya tidak terbaca. Sebuah kegagalan di sini
        tidak boleh menghentikan penyalaan - ARUNA yang menolak hidup karena
        tidak bisa melaporkan matinya sendiri adalah kegagalan yang jauh lebih
        besar daripada yang dilaporkannya.
        """
        if self.bot is None or not self.bot.started or self.app_state is None:
            return
        try:
            jeda = await heartbeat.check(self.app_state, now_utc())
        except Exception:
            log.exception("heartbeat.check_failed")
            return
        if jeda is None:
            return
        log.warning("heartbeat.downtime", seconds=round(jeda.seconds))
        await self.bot.send(jeda.line())

    async def _start_database(self) -> None:
        try:
            await self.db.connect()
        except ArunaError as exc:
            raise StartupError(
                f"{exc}\n\nARUNA cannot run without MySQL: it is the store of "
                "record for predictions, outcomes, and the audit trail. Check "
                "ARUNA_DB_* in your .env, then run: aruna doctor"
            ) from exc

    def _build_repositories(self) -> None:
        instance = self.settings.app.instance_name
        self.app_state = AppStateRepository(self.db)
        self.audit = AuditRepository(self.db, instance=instance)
        self.events = SystemEventRepository(self.db, instance=instance, phase=self.phase)
        self.subscribers = TelegramSubscriberRepository(self.db)
        self.universe = UniverseRepository(self.db)
        self.market_data = MarketDataRepository(self.db)
        self.analysis_store = AnalysisRepository(self.db)
        self.news_store = NewsRepository(self.db)
        self.fundamental_store = FundamentalRepository(self.db)
        self.correlation_store = CorrelationRepository(self.db)

        from aruna.db.repositories.memory import MemoryRepository

        # PASAL 15.5. Pembaca open interest dioper di sini supaya proyektor
        # ingatan futures bisa mengisi dimensinya - adapternya punya
        # `open_interest_history()` sejak lama dan tidak pernah ada yang
        # menyimpan hasilnya. Opsional: tanpa venue yang terjangkau, dimensinya
        # UNKNOWN dan proyeksinya tetap jalan.
        self.memory_store = MemoryRepository(
            self.db, oi_reader=self._oi_reader()
        )

        self.analysis = AnalysisService(
            universe=self.universe,
            market_data=self.market_data,
            analysis=self.analysis_store,
        )
        self.news = NewsService(
            provider=RssNewsProvider(),
            store=self.news_store,
            universe=self.universe,
        )
        self.fundamental = FundamentalService(
            provider=YahooFundamentalProvider(),
            store=self.fundamental_store,
            universe=self.universe,
        )

        from aruna.agents.service import DeliberationService
        from aruna.council.service import CouncilService
        from aruna.db.repositories.council import CouncilRepository
        from aruna.db.repositories.learning12 import (
            LearningRepository as AdaptiveRepository,
        )
        from aruna.learning.strategist import Strategist

        # PASAL 12.6. Dirangkai di sini dan bukan dibiarkan sebagai kemungkinan:
        # sebuah pemilih strategi yang tidak pernah dipanggil jalur hidup adalah
        # cacat yang paling sering terulang di sistem ini - kode yang benar,
        # diuji, diekspor, dan tidak pernah dilewati.
        #
        # Yang dilakukannya sekarang, pada tiga hari data, hampir selalu abstain.
        # Itu perilaku yang benar dan bukan alasan menundanya: yang dirangkai
        # hari ini adalah jalurnya, dan jalur yang sudah ada akan mulai membawa
        # jawaban begitu datanya cukup - tanpa ada yang perlu ingat merangkainya.
        self.adaptive_store = AdaptiveRepository(self.db)
        self.strategist = Strategist(store=self.adaptive_store)

        self.deliberation_store = DeliberationRepository(self.db, phase=self.phase)
        from aruna.db.repositories.router import RouterRepository
        from aruna.db.repositories.scenario import ScenarioRepository

        self.deliberation = DeliberationService(
            universe=self.universe,
            market_data=self.market_data,
            news=self.news_store,
            fundamental=self.fundamental_store,
            store=self.deliberation_store,
            strategist=self.strategist,
            # Bagian 18.14 dan 18.15. Tanpa dua baris ini, Phase 16 dan Phase
            # 17 tetap berjalan sebagai PENGAMAT: keduanya menulis
            # `scenario_evidence` dan `router_pilihan` yang tak seorang pun di
            # jalur keputusan baca, dan skor mutu menyusun delapan belas faktor
            # tanpa satu pun dari keduanya.
            #
            # Diverifikasi 2026-08-24 lewat impor: tidak ada satu berkas pun di
            # `signals/`, `council/`, atau `agents/` yang menyentuh
            # `aruna.router` maupun `aruna.scenario`. Penjaganya di
            # `tests/test_phase18_terpasang.py`, berbasis AST karena komentar
            # ini sendiri menyebut `router=` dan `scenario=`.
            router=RouterRepository(self.db),
            scenario=ScenarioRepository(self.db),
        )
        self.council_store = CouncilRepository(self.db, phase=self.phase)
        self.council = CouncilService(
            deliberation=self.deliberation, store=self.council_store
        )
        from aruna.db.repositories.learning import LearningRepository
        from aruna.learning.service import LearningService

        self.learning_store = LearningRepository(self.db)
        self.learning = LearningService(
            store=self.learning_store,
            market_data=self.market_data,
            universe=self.universe,
        )

        from aruna.backtest.service import BacktestService
        from aruna.db.repositories.backtest import BacktestRepository

        self.backtest_store = BacktestRepository(
            self.db, model_version=self.model_version
        )
        self.backtest = BacktestService(
            universe=self.universe,
            market_data=self.market_data,
            store=self.backtest_store,
            learning=self.learning,
        )

        from aruna.db.repositories.governance import GovernanceRepository
        from aruna.governance.service import GovernanceService

        self.governance_store = GovernanceRepository(self.db)
        self.governance = GovernanceService(
            store=self.governance_store,
            learning=self.learning_store,
            backtest=self.backtest_store,
        )

        # FUTURES F5. The store is built here, unconditionally, so a futures
        # plan is never quietly discarded for want of somewhere to put it - the
        # refusals matter as much as the plans (FUTURES SPEC 48).
        from aruna.db.repositories.futures import FuturesRepository

        self.futures_store = FuturesRepository(self.db)

        # PASAL 14.40 dan 14.41. Terukur di produksi 2026-08-20: Phase 12 hanya
        # 22% sampai ke keputusan, dan korelasi 0% - mesinnya ada sejak Phase 4,
        # tabelnya terisi, dan `DecisionContext.correlation` tidak pernah diisi
        # di mana pun. Keduanya bukan lapisan yang belum dibangun; keduanya
        # lapisan yang tidak punya pembaca.
        #
        # Dirangkai di sini dan bukan dibiarkan sebagai kemungkinan, dengan
        # alasan yang sama seperti `Strategist` di atas.
        from aruna.learning.snapshot import PembacaPembelajaran

        self.pembelajaran = PembacaPembelajaran(
            learning12=self.adaptive_store,
            governance=self.governance_store,
            correlation=self.correlation_store,
            # PASAL 14.40. Sampai 2026-08-21 `backtest_runs` berisi nol baris:
            # mesinnya lengkap, perintahnya mencetak hasilnya lalu membuangnya,
            # dan WALK_FORWARD/OUT_OF_SAMPLE hilang dari tiap keputusan.
            backtest=self.backtest_store,
            # Bagian 18.45. **Bukan `self.adaptive_store`**, walau bidang di
            # sebelahnya bernama `learning12` dan kelasnya juga bernama
            # `LearningRepository`: ada dua kelas dengan nama itu, dan yang
            # memegang `latest_calibration` adalah yang ini. Impor di atas
            # sengaja menamainya `AdaptiveRepository` untuk memisahkan
            # keduanya - dan versi pertama baris ini tetap tertukar.
            kalibrasi_store=self.learning_store,
            model_version=self.model_version,
        )


    async def _verify_schema(self) -> None:
        status = await Migrator(self.db).status()
        if status.pending:
            pending = ", ".join(m.label for m in status.pending)
            raise StartupError(
                f"{len(status.pending)} migration(s) pending: {pending}\n\n"
                "Refusing to start against a schema this build does not match. "
                "Run: aruna migrate"
            )
        log.info("schema.verified", version=status.current_version)

    async def _load_runtime_state(self) -> None:
        assert self.app_state is not None
        stored = await self.app_state.get(KILL_SWITCH_KEY)
        state = KillSwitchState.from_dict(stored)
        self.state.load_kill_switch(state)
        self.state.set_persist_hook(self._persist_kill_switch)
        if state.active:
            log.warning(
                "runtime.kill_switch_restored",
                reason=state.reason,
                actor=state.actor,
                detail="ARUNA starts with signal generation blocked; send /resume",
            )

    async def _load_measured_history(self) -> None:
        """Give the council what PHASE 8 has measured (SPEC 29, 30).

        Non-fatal, and usually a no-op with nothing to apply: until enough
        predictions have resolved, both factors answer ``None`` and the judge
        keeps them neutral. The log line says which of the two states this is,
        because "learning is wired in" and "learning is changing decisions" are
        very different claims.
        """
        if self.learning is None:
            return
        try:
            history = await self.learning.measured_history()
        except ArunaError as exc:
            log.warning("learning.history_unavailable", error=str(exc))
            return

        self.history = history
        if self.council is not None:
            self.council.use_history(history)

        if history.measurable:
            log.info(
                "learning.history_applied",
                agents_measured=len(history.reliability_report.measured),
                calibration=history.calibration_report.verdict,
            )
        else:
            log.info(
                "learning.history_neutral",
                resolved=history.calibration_report.total,
                detail=(
                    "too few resolved outcomes to measure reliability or "
                    "calibration; both SPEC 16 factors stay neutral and are "
                    "reported as unavailable"
                ),
            )

    async def _persist_kill_switch(self, state: KillSwitchState) -> None:
        if self.app_state is None:
            return
        await self.app_state.set(
            KILL_SWITCH_KEY, state.to_dict(), actor=state.actor or "system"
        )

    async def _start_ingestion(self) -> None:
        """Build one ingestor per enabled market.

        Non-fatal by design: a provider outage must not stop ARUNA from
        running, and a market with no configured provider is simply reported as
        DATA SOURCE UNAVAILABLE rather than served from another market's feed.
        """
        assert self.universe is not None and self.market_data is not None

        try:
            providers = build_providers(
                self.settings.providers,
                self.settings.data,
                self.settings.app.enabled_markets,
            )
        except ConfigError as exc:
            log.error("ingest.provider_config_invalid", error=str(exc))
            await self._record_event(
                component="ingest",
                event_type="CONFIG_INVALID",
                severity=EventSeverity.ERROR,
                message=str(exc),
                status=HealthStatus.DOWN,
            )
            return

        if not providers:
            log.warning(
                "ingest.no_providers",
                markets=[m.value for m in self.settings.app.enabled_markets],
                impact="DATA SOURCE UNAVAILABLE for every enabled market",
            )
            return

        ingestors = {
            market: MarketIngestor(
                provider=provider,
                universe=self.universe,
                store=self.market_data,
                settings=self.settings.data,
            )
            for market, provider in providers.items()
        }
        self.ingest = IngestService(ingestors, self.settings.data)
        await self.ingest.open()
        if self._background:
            await self.ingest.start()

        for market, provider in providers.items():
            capabilities = provider.capabilities
            log.info(
                "ingest.provider_ready",
                market=market.value,
                provider=provider.name,
                transport=capabilities.transport.value,
                realtime=capabilities.is_realtime,
                declared_delay_sec=capabilities.expected_delay_sec,
            )

        await self._start_stream()

    async def _start_stream(self) -> None:
        """Bring up the Binance spot WebSocket (PASAL 2, 8).

        **It runs beside the quote poll, not yet instead of it.** Nothing reads
        this stream today: health, Telegram and the council all still take
        their prices from the five-second REST snapshot. Cutting them over is a
        change to three live readers at once and is deliberately not smuggled
        in behind the word "wiring" - half a source swap, where some readers
        see the stream and some see the poll and nobody can tell which, is
        worse than either source alone.

        What this does buy now: the stream is reached by a live process, so its
        reconnect, hang-detection and freshness behaviour are exercised against
        the real venue instead of only against fakes - and its figures show up
        in ``state()`` where an operator can see them. A component that only
        ever runs in tests is this codebase's oldest recurring defect.

        Spot only. Futures streaming is silent on this network - the venue
        answers SUBSCRIBE and then sends nothing - so futures stays REST and is
        named REST everywhere (SPEC 4, 49).
        """
        if self.universe is None:
            return
        try:
            assets = await self.universe.assets(market=Market.CRYPTO)
        except ArunaError as exc:
            log.warning("stream.universe_unavailable", error=str(exc)[:200])
            return

        symbols = tuple(asset.symbol for asset in assets)
        if not symbols:
            log.info(
                "stream.not_wired",
                detail="no enabled CRYPTO assets, so there is nothing to subscribe to",
            )
            return

        self.stream = BinanceSpotStream(symbols)
        if self._background:
            await self.stream.start()

    async def _start_upkeep(self) -> None:
        """Wire the maintenance loop, and start it only for a long-running process.

        This is the loop that keeps candles current and scores predictions whose
        horizon has elapsed. Before it existed, spot candles were refreshed only
        by a manual ``aruna fetch`` and ``resolve_due`` was reachable only from
        a CLI flag - so the finest intervals froze and locked predictions piled
        up unscored, each defect feeding the other.

        The loop object is built even for a one-shot command, so ``aruna
        upkeep`` can drive exactly one cycle by hand and the health component
        has something to describe. **Starting** it is behind ``_background``,
        because ``startup(background=False)`` promises to run no periodic loop
        and a previous violation of that promise had short CLI commands
        stealing the Telegram token from the live process.
        """
        if not self.settings.upkeep.enabled:
            log.info(
                "upkeep.disabled",
                detail=(
                    "ARUNA_UPKEEP_ENABLED=false: candles are refreshed only by "
                    "`aruna fetch` and predictions are scored only by "
                    "`aruna signal --resolve-only`"
                ),
            )
            return
        if self.ingest is None or self.market_data is None:
            log.warning(
                "upkeep.not_wired",
                detail=(
                    "no market data provider is configured, so there is nothing "
                    "to refresh"
                ),
            )
            return

        from aruna.learning.adaptive import AdaptiveLearningService

        # Penyimpanan Phase 12 dirakit di fase lain, dan perakitan yang belum
        # sampai ke sana adalah keadaan yang sah - bukan sesuatu yang boleh
        # menghentikan seluruh loop upkeep. Tanpa penyimpanannya, pembelajaran
        # tidak dijalankan, dan itu dinyatakan di sini alih-alih meledak jauh
        # di dalam siklus.
        adaptif = getattr(self, "adaptive_store", None)

        refresher = CandleRefresher(
            ingest=self.ingest,
            store=self.market_data,
            settings=self.settings.upkeep,
        )
        self.upkeep = UpkeepLoop(
            refresher=refresher,
            # `resolver` dan `locker` HILANG sejak jalur spot dicabut
            # (2026-08-25, keputusan operator). Keduanya dulu `self.signals` -
            # satu servis yang mengunci prediksi spot dan menilai hasilnya.
            #
            # Yang ikut berhenti disebut apa adanya di `docs/`: proyeksi
            # ingatan, kalibrasi, dan keandalan agen semuanya dihitung dari
            # hasil spot, dan tidak ada satu pun yang menggantikannya.
            # Pemindai cepat (PASAL 14, 15). Ia TIDAK menggerakkan council -
            # lihat `UpkeepLoop._scan` untuk kenapa, dan untuk apa yang jujur
            # bisa diklaim tentang nilainya pada universe sebesar sekarang.
            scanner=ScannerService(
                universe=self.universe, market_data=self.market_data
            ),
            # PASAL 11. Built at line 238 and closed at shutdown, and until
            # this argument existed, never run in between: no `start` on the
            # class, no caller of `ingest` outside the CLI. The council read
            # sixty-hour-old headlines as present context.
            news=self.news,
            # Laporan harian pukul 00:00 WIB. Dititipkan ke loop upkeep dan
            # bukan ke penjadwal sendiri, karena loop inilah satu-satunya yang
            # sudah pasti berdetak selama ARUNA hidup - penjadwal terpisah
            # berarti satu lagi hal yang bisa diam tanpa ada yang tahu.
            #
            # Servicenya sendiri yang memutuskan kapan waktunya dan menolak
            # mengirim dua kali untuk tanggal yang sama, termasuk sesudah
            # restart, jadi memanggilnya tiap tick aman.
            daily=self._build_daily(),
            # PASAL 12.27. Tanpa baris ini seluruh Phase 12 diam di produksi:
            # servicenya hanya dipanggil dari `cli.py`, jadi ia belajar tepat
            # ketika seseorang mengetik `aruna learn` - dan `Strategist` yang
            # membaca hasilnya membaca angka dari entah kapan.
            learning=AdaptiveLearningService(adaptif) if adaptif else None,
            # PASAL 11.16. ARUNA membaca kekalahannya sendiri, mengangkat
            # pertanyaan, dan berhenti di situ - ia tidak menulis usulan
            # perubahan atas dirinya sendiri. Yang didorong ke operator adalah
            # pertanyaan itu, ditambah proposal yang menunggu keputusannya.
            research=self._build_research(),
            # PASAL 14.41. Sama persis dengan `learning` di atas, satu lapis
            # lebih dalam: mesin korelasi ada sejak Phase 4, pembacanya ada di
            # `PembacaPembelajaran`, dan yang MENJALANKANNYA hanya perintah CLI
            # `aruna correlate`. Terukur 2026-08-21: tabelnya nol baris, dan
            # empat puluh amatan berturut-turut melaporkan CORRELATION_RISK
            # hilang dari keputusan.
            korelasi=self._build_korelasi(),
            # PASAL 15.2. Ingatan pasar diproyeksikan dari `signal_snapshots`
            # dan `outcome_snapshots` yang sudah ada - tidak ada raw market
            # data yang disimpan ulang (PASAL 15.27). Tanpa baris ini,
            # `market_memories` berhenti tumbuh di titik terakhir seseorang
            # menjalankan proyeksinya dengan tangan, dan pencarian kemiripan
            # menjawab pertanyaan hari ini dengan sejarah kemarin.
            memory=self._build_memory(),
            # Bagian 25-26. Audit 2026-08-21 menemukan nol retention di seluruh
            # basis kode: setiap DELETE yang ada hanya penggantian per-sesi, dan
            # basis data tumbuh selamanya - 506 MB, dengan `market_snapshots`
            # menyumbang 62% pada 69.048 baris sehari. Tanpa baris ini,
            # pembersihnya lengkap, teruji, dan tidak pernah berjalan.
            retensi=self._build_retensi(),
            # PASAL 15.44. Tanpa baris ini putusannya tidak pernah dihitung,
            # gerbang per timeframe tidak pernah menutup, dan ARUNA terus
            # memberi bobot pada ingatan di 1h - tempat evaluasinya sendiri
            # mengukur -7 poin.
            manfaat=self._build_manfaat(),
            # Bagian 16.17. Tanpa baris ini seluruh Phase 16 menjadi kode yang
            # benar, diuji, dan tidak pernah dipanggil - cacat yang sudah
            # berulang di proyek ini pada `AdaptiveLearningService`, pembersih
            # retensi, dan penilai PASAL 15.44. Penjaganya ada di
            # `tests/test_scenario_terpasang.py`, dan penjaganya berbasis AST
            # karena komentar ini sendiri menyebut `scenario=`.
            scenario=self._build_scenario(),
            # Bagian 16.19. Tanpa baris ini `aruna.scenario.evaluasi` adalah
            # modul yang ditulis, diuji, diekspor, dan tidak pernah dipanggil -
            # dan tiap skenario tersimpan dengan `hasil` NULL selamanya. Itu
            # keadaannya sampai baris ini ada.
            scenario_nilai=self._build_scenario_nilai(),
            # Bagian 17.19. Tanpa baris ini seluruh paket `aruna.router` -
            # tujuh modul, sembilan puluh test - adalah kode yang benar,
            # diuji, diekspor, dan tidak pernah dipanggil. Cacat yang sama
            # sudah lima kali muncul di proyek ini; penjaganya ada di
            # `tests/test_router_terpasang.py`, berbasis AST karena komentar
            # ini sendiri menyebut `router=`.
            router=self._build_router(),
            # SPEC 29, 30. `learning.review()` punya tepat satu pemanggil di
            # seluruh kode - perintah `aruna learn` - dan terukur 2026-08-21
            # tidak seorang pun mengetiknya sejak 2026-08-15: tiga baris di
            # `calibration_snapshots`, verdict "OVERCONFIDENT" di seluruh pita.
            review=self.learning,
            # Penerimanya, supaya pengukurannya benar-benar dipakai lagi.
            # `_load_measured_history` hanya jalan saat start; tanpa dua baris
            # ini council memakai angka dari saat proses menyala sampai mati.
            review_council=self.council,
            # Bagian 23. Tanpa baris ini kalibrasi kembali menimpa dirinya tiap
            # hari tanpa catatan apa yang hilang - dan sejak 2026-08-21 angkanya
            # sampai ke keyakinan yang diterbitkan.
            review_state=self.app_state,
            # Denyut. Tanpa ini tidak ada satu pun catatan tentang berapa lama
            # ARUNA mati - terukur: `aruna.stopped` 22 kali dalam sehari dan
            # `telegram.stopped` nol kali, karena proses yang dibunuh paksa
            # tidak sempat mencatat akhirnya sendiri.
            heartbeat_state=self.app_state,
            settings=self.settings.upkeep,
        )
        if self.settings.upkeep.lock_enabled:
            log.info(
                "upkeep.lock_set",
                markets=[m.value for m in self.settings.upkeep.lock_market_set],
                horizons=[h.value for h in self.settings.upkeep.lock_horizon_set],
                detail=(
                    "one prediction per horizon per bar; paper only, no order "
                    "is placed and no funds move (SPEC 46)"
                ),
            )
        for market in self.ingest.markets:
            log.info(
                "upkeep.refresh_set",
                market=market.value,
                intervals=[i.value for i in refresher.intervals_for(market)],
            )
        if self._background:
            await self.upkeep.start()

    def _oi_reader(self) -> Any:
        """Adapter futures untuk membaca open interest, atau ``None``.

        Dibangun sendiri dan bukan dipinjam dari loop futures: keduanya hidup
        di **proses yang berbeda**, dan yang di sini hanya dipakai proyektor
        ingatan. Read-only secara struktur - lihat ``PUBLIC_ENDPOINTS``.
        """
        try:
            from aruna.futures.binance import BinanceFuturesProvider

            return BinanceFuturesProvider()
        except Exception:
            log.exception("memory.oi_reader_unavailable")
            return None

    def _build_memory(self) -> Any:
        """Proyektor ingatan pasar (PASAL 15.2), atau ``None``.

        Repositori yang **sama** dengan yang dibaca jalur keputusan di proses
        `futures-loop` - lihat ``memory_store``. Dua tempat pembangunan berarti
        dua yang harus tetap sepakat, dan yang satu akan diam-diam berbeda.
        """
        if getattr(self, "memory_store", None) is not None:
            return self.memory_store
        if getattr(self, "db", None) is None:
            return None

        from aruna.db.repositories.memory import MemoryRepository

        return MemoryRepository(self.db)

    def _build_korelasi(self) -> Any:
        """Penyegar korelasi pasangan (PASAL 14.41), atau ``None``.

        Pasarnya diambil dari yang benar-benar diaktifkan, bukan didaftar ulang
        di sini: satu daftar pasar yang berdiri sendiri akan menghitung korelasi
        untuk pasar yang sudah dimatikan, dan melewatkan yang baru dinyalakan.
        """
        if self.universe is None or self.market_data is None:
            return None
        if self.correlation_store is None:
            return None

        from aruna.upkeep.korelasi import PenyegarKorelasi

        return PenyegarKorelasi(
            universe=self.universe,
            market_data=self.market_data,
            store=self.correlation_store,
            markets=tuple(self.settings.app.enabled_markets),
        )

    def _build_retensi(self) -> Any:
        """Pembersih retensi (bagian 25-26), atau ``None``.

        Memakai `RENCANA` bawaan, tidak menyusun rencananya sendiri di sini:
        satu rencana yang berdiri sendiri di lapisan perangkaian adalah
        rencana yang bisa menyimpang dari daftar `DILINDUNGI` tanpa satu pun
        test menyentuhnya.
        """
        if self.db is None:
            return None

        from aruna.upkeep.retensi import PembersihRetensi

        return PembersihRetensi(self.db)

    def _build_manfaat(self) -> Any:
        """Penilai PASAL 15.44, atau ``None``.

        Butuh keduanya: ``memory_store`` untuk membaca ingatan, dan
        ``app_state`` untuk menulis putusannya ke tempat yang **proses lain**
        bisa baca. Yang memakai putusan ini adalah ``futures-loop``, bukan
        proses ini - menyimpannya di memori akan membuat gerbangnya terbuka di
        sisi yang justru mengambil keputusan.
        """
        if self.memory_store is None or self.app_state is None:
            return None

        from aruna.upkeep.manfaat import PenilaiManfaat

        return PenilaiManfaat(
            memory=self.memory_store, app_state=self.app_state
        )

    def _build_scenario(self) -> Any:
        """Fase simulasi berpemicu (bagian 16.17), atau ``None``.

        Mesin eksternal sengaja tidak dioper: MiroFish tidak ada, dan
        ``coba_simulasi(None, ...)`` memulangkan ``DEGRADED`` - persis jalur
        yang bagian 16.12 minta, dijalankan setiap siklus alih-alih disimpan
        sebagai cabang yang belum pernah diambil.
        """
        if not self.settings.upkeep.scenario_enabled:
            return None

        from aruna.db.repositories.futures_metrics import FuturesMetricsRepository
        from aruna.db.repositories.konteks_pemicu import KonteksPemicuRepository
        from aruna.db.repositories.scenario import ScenarioRepository
        from aruna.upkeep.skenario import PenyimulasiSkenario

        return PenyimulasiSkenario(
            repo=ScenarioRepository(self.db),
            # Tiga pemicu bagian 16.2 yang datanya sudah tersimpan tapi tidak
            # pernah dibaca: perubahan regime, ketidakpastian tinggi, dan
            # selisih pendapat antar-agent. Tanpa baris ini deteksi kembali ke
            # enam pemicu yang lahir dari pemindai saja.
            konteks=KonteksPemicuRepository(
                self.db,
                # Dua pemicu lagi: anomali funding dan anomali open interest.
                # Keduanya diambil `futures-loop` tiap siklus lalu dibuang
                # sampai `futures_metrics` ada.
                metrik=FuturesMetricsRepository(self.db),
            ),
        )

    def _build_scenario_nilai(self) -> Any:
        """Penilai bagian 16.19, atau ``None``.

        Butuh tiga: repositori skenario untuk antrean dan penulisan, data pasar
        untuk candle sesudah skenarionya lahir, dan universe untuk menerjemahkan
        simbol menjadi ``asset_id``.
        """
        if not self.settings.upkeep.scenario_enabled:
            return None
        if self.market_data is None or self.universe is None:
            return None

        from aruna.db.repositories.scenario import ScenarioRepository
        from aruna.upkeep.skenario_nilai import PenilaiSkenario

        return PenilaiSkenario(
            repo=ScenarioRepository(self.db),
            market_data=self.market_data,
            universe=self.universe,
        )

    def _build_router(self) -> Any:
        """Fase router Phase 17, atau ``None``.

        **Berjalan tanpa bukti performa hari ini, dan itu benar.** Tidak ada
        yang menulis baris berlabel ``router-1`` ke ``strategy_performance`` -
        pengisiannya milik Phase 12. Sampai itu ada, `performa_rezim`
        memulangkan `None` dan router memeringkat dari kecocokan rezim,
        keyakinan, dan stabilitas saja.

        Tapi pembacanya **tetap dirakit**, dan itu koreksi audit 2026-08-23.
        Versi pertama tidak mengisi ``performa=`` sama sekali "sampai ada yang
        bisa dibaca" - dan akibatnya seluruh Task 3, `Kecocokan.sampel`,
        `Kecocokan.risiko`, gerbang `MIN_VALIDATION_SAMPLE`, dan potongan
        risiko di `kecocokan.nilai` menjadi kode mati yang menunggu seseorang
        ingat menyambungkannya. Dirakit sekarang, baris pertama yang muncul
        langsung terpakai.
        """
        if self.db is None:
            return None

        from aruna.db.repositories.router import RouterRepository
        from aruna.upkeep.router import FaseRouter

        repo = RouterRepository(self.db)
        # `status=` bukan pengulangan `repo=`. Tanpanya fase membaca status
        # dari katalog KODE, yang menulis setiap strategi ACTIVE - dan seluruh
        # pembedaan champion/challenger mati senyap. Terukur 2026-08-23:
        # STR-002 dan STR-005 berstatus UNDER_REVIEW di tabel, ACTIVE di kode.
        return FaseRouter(repo=repo, status=repo, performa=repo)

    def _build_daily(self) -> Any:
        """Laporan harian pukul 00:00 WIB.

        Botnya **tidak** dibaca di sini. ``_start_upkeep`` berjalan sebelum
        ``_start_telegram``, jadi pada saat metode ini dipanggil ``self.bot``
        masih ``None`` - dan versi pertama kode ini memeriksanya di sini lalu
        mengembalikan ``None``, sehingga laporan harian tidak akan pernah jalan
        sama sekali. Kegagalannya diam sempurna: tidak ada error, tidak ada
        log, hanya laporan yang tidak pernah datang.

        Jadi bot dan health monitor dibaca lewat closure, saat dipakai.
        """
        from aruna.db.repositories.daily import DailyRepository
        from aruna.db.repositories.diam import DiamRepository
        from aruna.notify.daily_service import DailyReportService

        return DailyReportService(
            repo=DailyRepository(self.db),
            # PASAL 14.32/14.33: akurasi diam. Repositori terpisah karena
            # pertanyaannya terpisah - `DailyRepository` menghitung baris yang
            # sudah ada, sementara yang ini membaca harga sesudah keputusan
            # yang justru TIDAK menghasilkan baris apa pun.
            diam_repo=DiamRepository(self.db),
            # PASAL 15.43. Repositori yang sama dengan yang memproyeksikan dan
            # yang dibaca jalur keputusan - satu tempat pembangunan.
            memory_repo=self.memory_store,
            sender=_LateSender(lambda: self.bot),
            state=self.app_state,
            health=lambda: self.monitor.latest if self.monitor else None,
            model_version=self.model_version,
            uptime_seconds=lambda: self.state.uptime_seconds,
        )

    def _build_research(self) -> Any:
        """Pengabar pertanyaan riset, sekali sehari (PASAL 11.16).

        Botnya dibaca lewat closure dengan alasan yang sama seperti
        :meth:`_build_daily`: ``_start_upkeep`` berjalan sebelum
        ``_start_telegram``, jadi membacanya sekarang akan selalu menemukan
        ``None`` - dan kegagalannya diam sempurna.

        Mengembalikan ``None`` kalau lapisan governance tidak terpasang, supaya
        loop melewatinya alih-alih menabrak atribut yang tidak ada.
        """
        if self.governance is None or self.governance_store is None:
            return None

        from aruna.notify.research import ResearchNotifier

        return ResearchNotifier(
            governance=self.governance,
            store=self.governance_store,
            sender=_LateSender(lambda: self.bot),
            state=self.app_state,
        )

    async def _start_telegram(self) -> None:
        deps = BotDeps(
            settings=self.settings,
            state=self.state,
            phase=self.phase,
            latest_health=lambda: self.monitor.latest if self.monitor else None,
            refresh_health=self.health_now,
            audit=self.audit,
            subscribers=self.subscribers,
            cache=self.cache,
            market_data=self.market_data,
            universe=self.universe,
            council=self.council_store,
            signals=self.signal_store,
            learning=self.learning_store,
            governance=self.governance_store,
            futures=self.futures_store,
            adaptive=getattr(self, "adaptive_store", None),
        )
        self.bot = TelegramBot(deps)
        try:
            await self.bot.start()
        except TelegramError as exc:
            # `await`, dan kata itu sempat hilang di sini sementara cabang
            # `ArunaError` di bawah memilikinya. Ditemukan 2026-08-24 dari
            # layar operator: pemeriksaan health mencetak "a bot token is
            # configured but the bot did not start - see the telegram
            # START_FAILED event for the reason", dan event itu TIDAK ADA.
            #
            # Coroutine yang tidak ditunggu tidak pernah dijadwalkan, jadi
            # tidak ada log `telegram.start_failed` dan tidak ada baris
            # `system_events`. Python cuma menggumamkan RuntimeWarning yang
            # tenggelam di antara ribuan baris. Dan `TelegramError` justru yang
            # dilempar `bot.start()` untuk kegagalan paling umum - termasuk
            # `Conflict` ketika dua instance memakai satu token.
            #
            # Yang hilang karena itu bukan sembarang catatan, melainkan
            # catatan kegagalan yang pesan health-nya sendiri suruh cari.
            await self._note_telegram_failure(exc)
            if not exc.permanent and self._background:
                # Jaringan yang tersumbat pulih; ARUNA harus menyusul sendiri.
                self._telegram_retry = asyncio.create_task(
                    self._retry_telegram()
                )
            return
        except ArunaError as exc:
            await self._note_telegram_failure(exc)

    async def _note_telegram_failure(self, exc: Exception) -> None:
        """Catat kegagalan menyalakan bot. Tidak pernah fatal.

        Scrubbed explicitly, because this row does NOT pass through the
        logging pipeline where the redactor lives. python-telegram-bot raises
        InvalidToken with the token interpolated into its own message, so
        ``str(exc)`` is the literal credential - and this wrote it to
        ``system_events.message`` in plaintext, where it persists in the
        database and in every backup of it.
        """
        detail = REDACTOR.scrub_text(str(exc))
        log.error("telegram.start_failed", error=detail)
        await self._record_event(
            component="telegram",
            event_type="START_FAILED",
            severity=EventSeverity.ERROR,
            message=detail,
            status=HealthStatus.DOWN,
        )

    async def _stop_telegram_retry(self) -> None:
        """Hentikan percobaan ulang Telegram, dan tunggu ia benar-benar mati.

        Seam-nya sendiri, bukan beberapa baris di dalam ``shutdown``. Alasannya
        terbukti saat ditulis: versi pertama diuji dengan memindai teks
        ``shutdown`` mencari kata ``cancel()``, dan pemindaian itu tetap hijau
        ketika seluruh pembatalannya dimatikan dengan ``if False:`` - karena
        katanya masih ada di sumbernya. Sebuah seam bisa diuji perilakunya.

        ``_stop_requested`` di-set lebih dulu supaya loop-nya keluar sendiri
        kalau ia kebetulan sedang bangun; ``cancel`` menangani yang sedang
        tidur. Task yang masih hidup saat event loop ditutup menghasilkan
        "Task was destroyed but it is pending" - keluhan tentang shutdown yang
        menutupi apa pun yang sebenarnya salah.
        """
        self._stop_requested.set()
        tugas = self._telegram_retry
        if tugas is None:
            return
        tugas.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tugas
        self._telegram_retry = None

    async def _retry_telegram(self) -> None:
        """Coba nyalakan bot lagi selama ia belum menyala.

        **Kenapa ini ada.** Versi sebelumnya memanggil ``bot.start()`` tepat
        sekali. Terukur pada 2026-08-19: ``api.telegram.org`` menolak seluruh
        koneksi dengan ReadTimeout - tiga dari tiga percobaan HTTP mentah -
        sementara ``api.binance.com`` menjawab 200. Internetnya hidup; hanya
        Telegram yang tersumbat, persis pola yang sudah terdokumentasi untuk
        Binance di jaringan Indonesia.

        Akibatnya bukan pesan yang tertunda. Bot tidak pernah menyala lagi
        sesudah itu, jadi ketika jaringannya pulih - dan ia pulih - ARUNA tetap
        diam sampai seseorang me-restart prosesnya. Operator yang sedang di
        luar kehilangan seluruh signal, hasil, dan alert kesehatan tanpa satu
        pun tanda bahwa ada yang perlu diperbaiki.

        Jedanya melebar dari :data:`TELEGRAM_RETRY_MIN_SEC` sampai
        :data:`TELEGRAM_RETRY_MAX_SEC`: sumbatan jaringan bisa berlangsung
        berjam-jam, dan mencoba tiap tiga puluh detik selama itu hanya
        memindahkan kebisingan dari Telegram ke log.

        Token yang tidak sah TIDAK pernah sampai ke sini - lihat
        ``permanent`` di :class:`~aruna.core.errors.TelegramError`. Mencoba
        ulang sesuatu yang tidak akan pernah berhasil adalah cara membuat log
        tidak terbaca.
        """
        jeda = TELEGRAM_RETRY_MIN_SEC
        while not self._stop_requested.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_requested.wait(), timeout=jeda)
            if self._stop_requested.is_set() or self.bot is None:
                return
            if self.bot.started:
                return
            try:
                await self.bot.start()
            except TelegramError as exc:
                if exc.permanent:
                    log.error(
                        "telegram.retry_abandoned",
                        error=REDACTOR.scrub_text(str(exc)),
                        detail="tokennya tidak sah; menunggu tidak akan menolong",
                    )
                    return
                jeda = min(jeda * 2, TELEGRAM_RETRY_MAX_SEC)
                log.info(
                    "telegram.retry_failed",
                    next_attempt_sec=jeda,
                    detail="Telegram belum bisa dicapai; ARUNA tetap menganalisis",
                )
                continue
            except ArunaError:
                log.exception("telegram.retry_error")
                jeda = min(jeda * 2, TELEGRAM_RETRY_MAX_SEC)
                continue

            log.info(
                "telegram.reconnected",
                detail="bot menyala sesudah gagal saat startup",
            )
            await self._record_event(
                component="telegram",
                event_type="RECONNECTED",
                severity=EventSeverity.INFO,
                message="bot Telegram menyala sesudah percobaan ulang",
                status=HealthStatus.UP,
            )
            return

    async def _start_health_monitor(self) -> None:
        def _bot_hidup() -> bool:
            """Apakah bot menyala **sekarang**, bukan saat monitor dirakit.

            Ditanyakan tiap sapuan karena jawabannya berubah:
            :meth:`_retry_telegram` menyalakan bot yang gagal saat startup, dan
            terukur 2026-08-22 ia berhasil dua detik sesudah kesehatan menandai
            Telegram DOWN. Nilai yang dibaca sekali membuat penandaan itu
            bertahan empat belas menit lebih sesudah botnya hidup lagi.
            """
            return self.bot is not None and self.bot.started

        checks = [
            DatabaseCheck(self.db, timeout=self.settings.health.db_timeout_sec),
            # Bagian 27-28. Komponen sendiri, bukan tempelan pada
            # `DatabaseCheck`: yang itu menjawab "bisa dihubungi?", ini
            # "muat berapa lama lagi?". Tanpa baris ini, pertumbuhan basis data
            # kembali hanya terlihat kalau seseorang kebetulan mengetik kueri
            # `information_schema` - yang persis keadaannya sampai 2026-08-21.
            UkuranDatabaseCheck(
                self.db,
                peringatan_mb=self.settings.health.db_size_warn_mb,
                kritis_mb=self.settings.health.db_size_critical_mb,
            ),
            RedisCheck(self.cache, timeout=self.settings.health.redis_timeout_sec),
            TelegramCheck(
                hidup=_bot_hidup,
                # Dioper tanpa syarat. Versi sebelumnya menulis
                # `self.bot.get_me if bot_started else None`, jadi bot yang
                # gagal saat startup tidak punya probe **selamanya** - bahkan
                # sesudah percobaan ulang menghidupkannya. `hidup` yang
                # menggerbangi pemanggilannya, bukan ada-tidaknya probe.
                probe=self.bot.get_me if self.bot is not None else None,
                timeout=self.settings.health.telegram_timeout_sec,
                # There are three states, not two, and the middle one is easy
                # to lose:
                #   not configured            -> a choice   (DISABLED)
                #   configured, not attempted -> a choice   (DISABLED)
                #   configured, attempt failed-> a fault    (DOWN)
                # A headless command has a token in its .env and deliberately
                # never starts a bot; reporting that as a fault made every
                # `plan` and `futures-loop` run report itself DEGRADED for
                # doing exactly what it was asked to do.
                configured=self.settings.telegram.active and self._background,
            ),
            ConfigCheck(self.settings),
            ClockCheck(self.settings.app.timezone),
            ProcessCheck(
                self.state,
                instance=self.settings.app.instance_name,
                phase=self.phase,
            ),
        ]
        if self.ingest is not None:
            checks.extend(
                ProviderCheck(ingestor, timeout=self.settings.data.request_timeout_sec)
                for ingestor in (
                    self.ingest.ingestor(market) for market in self.ingest.markets
                )
                if ingestor is not None
            )
            checks.extend(self._candle_freshness_checks())
        # Registered even when the loop is not: a component that vanishes when
        # the thing it watches is missing reports nothing at exactly the moment
        # there is something to report.
        checks.append(
            UpkeepCheck(
                self.upkeep,
                background=self._background,
                due_count=self._due_signal_count if self.signal_store else None,
            )
        )
        # Same reasoning as the line above: registered whether or not the
        # stream was wired, so "not running" is something an operator is told
        # rather than something that quietly leaves the roster.
        checks.append(StreamCheck(self.stream, background=self._background))
        self.monitor = HealthMonitor(
            checks,
            interval_sec=self.settings.health.interval_sec,
            failure_threshold=self.settings.health.failure_threshold,
            alert_hook=self._on_health_change,
            event_hook=self._on_health_event,
        )
        await self.monitor.run_once()
        if self._background:
            await self.monitor.start()

    def _candle_freshness_checks(self) -> list[CandleFreshnessCheck]:
        """One component per market, reading MySQL rather than the loop.

        Two sources, deliberately. ``upkeep`` can only say the loop is turning;
        these say whether anything actually landed in the database. A loop that
        runs and writes nothing is the defect this project has shipped before,
        and only the second question catches it.
        """
        assert self.ingest is not None and self.market_data is not None
        checks: list[CandleFreshnessCheck] = []
        for market in self.ingest.markets:
            ingestor = self.ingest.ingestor(market)
            if ingestor is None:
                continue
            supported = ingestor.provider.capabilities.supported_intervals
            intervals = (
                self.upkeep.refresher.intervals_for(market)
                if self.upkeep is not None
                else refresh_intervals(market, supported)
            )
            checks.append(
                CandleFreshnessCheck(
                    self.market_data,
                    market=market,
                    intervals=intervals,
                    factor=self.settings.upkeep.stale_tolerance_factor,
                    locked_horizons=(
                        self.signal_store.locked_horizons if self.signal_store else None
                    ),
                    supported=supported,
                )
            )
        return checks

    async def _due_signal_count(self) -> int:
        return await self.signal_store.due_count(reference=now_utc())

    # ---- health hooks ---------------------------------------------------

    async def health_now(self) -> HealthReport:
        if self.monitor is None:
            raise ArunaError("health monitor is not running")
        report = await self.monitor.run_once()
        await self.cache.set_json(
            *HEALTH_CACHE_KEY, value=report.to_dict(), ttl_sec=HEALTH_CACHE_TTL_SEC
        )
        return report

    async def _on_health_event(
        self,
        component: ComponentHealth,
        overall: HealthStatus,
        severity: EventSeverity,
    ) -> None:
        await self._record_event(
            component=component.name,
            event_type="HEALTH_TRANSITION",
            severity=severity,
            message=component.message or component.status.value,
            status=component.status,
            details={
                "overall": overall.value,
                "latency_ms": component.latency_ms,
                "consecutive_failures": component.consecutive_failures,
            },
        )

    async def _on_health_change(
        self, report: HealthReport, changed: tuple[ComponentHealth, ...]
    ) -> None:
        await self.cache.set_json(
            *HEALTH_CACHE_KEY, value=report.to_dict(), ttl_sec=HEALTH_CACHE_TTL_SEC
        )
        if not self.settings.telegram.alert_on_health_change:
            return
        if self.bot is None or not self.bot.started:
            return

        # The very first sweep transitions every component from "no prior
        # status"; that is startup, not an incident, and the startup message
        # already covers it.
        #
        # This has to be a ONE-SHOT FLAG, not a test on the statuses. As a
        # status test it read "every changed component is operational" - which
        # is exactly what a RECOVERY looks like. So DOWN alerts went out and
        # DOWN->UP alerts never did, for the life of the process: MySQL falls
        # over at 03:00 and the operator is told, MySQL comes back at 03:02 and
        # the operator is told nothing. The last word about a component was its
        # failure, permanently, and a two-minute blip was indistinguishable
        # from an ongoing outage.
        first_sweep = not self._health_alerted
        self._health_alerted = True
        if first_sweep and all(c.status.is_operational for c in changed):
            return

        # PASAL 17 dan 18. Sebelum kebijakan ini ada, SETIAP peralihan dikirim -
        # dan pada jaringan yang sedang lambat itu berarti lima pesan dalam lima
        # menit tentang satu keadaan yang tidak berubah:
        #
        #   05:28  binance-spot DOWN      no response within 15s
        #   05:30  binance-spot DEGRADED  skew jam venue +15.0s
        #   05:31  binance-spot DOWN      no response within 15s
        #
        # Separuhnya terkirim berjudul HEALTH PULIH, karena DEGRADED terhitung
        # operasional - memberi tahu operator bahwa sesuatu telah pulih, dua
        # kali, padahal tidak pernah pulih.
        putusan = self._alert_policy.decide(changed, now=now_utc())
        if putusan.suppressed:
            log.info("health.alert_suppressed", detail=list(putusan.suppressed))
        if not putusan.anything:
            return

        for kelompok in (putusan.alerts, putusan.recoveries):
            if kelompok:
                await self.bot.send(fmt.health_alert(report, kelompok))

    # ---- shutdown -------------------------------------------------------

    async def shutdown(self) -> None:
        if not self._running:
            return
        self._running = False
        log.info("aruna.stopping")

        await self._stop_telegram_retry()

        await self._audit("system", "APPLICATION_STOP")
        await self._record_event(
            component="application",
            event_type="SHUTDOWN",
            severity=EventSeverity.INFO,
            message="ARUNA shutting down",
        )
        await self._close_resources()

        log.info("aruna.stopped")

    async def _close_resources(self) -> None:
        """Close everything that was built, in the order that keeps it quiet.

        Split out of :meth:`shutdown` so the startup-failure path can reach it
        too. ``shutdown()`` returns immediately unless ``_running`` is set, and
        ``_running`` is only set at the *end* of ``startup()`` - so a failure in
        one of the last startup steps used to leave whatever had already been
        started with nothing to close it. That was survivable while the leak was
        passive; it is not now, because ``_start_upkeep`` starts a task that
        goes on calling providers and writing candles.
        """
        if self.monitor is not None:
            await _safe("health monitor", self.monitor.stop())
        # Before the providers close. A cycle still in flight would otherwise
        # run its next request into an HTTP session that has already been shut,
        # and the resulting noise would look like a venue outage. Its own grace
        # is longer than a normal step, hence the wider timeout.
        if self.upkeep is not None:
            await _safe(
                "upkeep", self.upkeep.stop(), timeout=UPKEEP_SHUTDOWN_TIMEOUT_SEC
            )
        # Stopped before the ingest service: the stream holds a socket of its
        # own and nothing downstream depends on it, so it is the cheapest thing
        # to let go of first.
        if self.stream is not None:
            await _safe("stream", self.stream.stop())
        if self.ingest is not None:
            await _safe("ingest", self.ingest.close())
        if self.news is not None:
            await _safe("news", self.news.close())
        if self.bot is not None:
            if self.bot.started:
                await self.bot.send("ARUNA\n\nShutting down. No signals until restart.")
            await _safe("telegram", self.bot.stop())
        await _safe("cache", self.cache.close())
        await _safe("database", self.db.close())

    # ---- run ------------------------------------------------------------

    async def run(self) -> None:
        """Start, then block until SIGINT/SIGTERM, then shut down cleanly."""
        try:
            await self.startup()
        except BaseException:
            # `startup()` starts the upkeep task at a point where `_running` is
            # still False, and the `finally` below only reaches `shutdown()`,
            # which returns at once in that state. A failure in one of the steps
            # after it - Telegram, the health monitor - therefore left a live
            # loop refreshing candles and resolving signals against a database
            # nobody was going to close, in a process that was already exiting.
            log.warning("aruna.startup_failed", detail="closing what was started")
            await self._close_resources()
            raise
        self._install_signal_handlers()
        try:
            await self._stop_requested.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    def request_stop(self) -> None:
        self._stop_requested.set()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:
                # Windows ProactorEventLoop has no add_signal_handler; the
                # synchronous handler must hop back onto the loop thread.
                signal.signal(
                    sig, lambda *_: loop.call_soon_threadsafe(self.request_stop)
                )

    # ---- helpers --------------------------------------------------------

    async def _record_event(
        self,
        *,
        component: str,
        event_type: str,
        severity: EventSeverity,
        message: str,
        status: HealthStatus | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.events is None:
            return
        try:
            await self.events.record(
                component=component,
                event_type=event_type,
                severity=severity,
                message=message,
                status=status,
                details=details,
            )
        except ArunaError as exc:
            log.warning("system_event.write_failed", component=component, error=str(exc))

    async def _audit(self, actor: str, action: str, **kwargs: Any) -> None:
        if self.audit is None:
            return
        try:
            await self.audit.record(actor=actor, action=action, **kwargs)
        except ArunaError as exc:
            log.warning("audit.write_failed", action=action, error=str(exc))


#: Pesan sambutan yang dulu ada di sini sudah dihapus (PASAL 14.38).
#:
#: Isinya - build, market, berapa perintah yang menjawab - tidak hilang:
#: `/status` melaporkan hal yang sama, dan `/status` dikirim karena
#: operator memintanya. Yang berhenti hanyalah mendorongnya ke ponsel
#: setiap kali proses menyala.
#:
#: Dihapus, bukan disimpan "kalau-kalau dibutuhkan": kode yang tidak
#: pernah dipanggil tapi terlihat hidup adalah keluarga cacat yang paling
#: sering muncul di sistem ini.
#: How long one shutdown step may take before the rest are allowed to proceed.
SHUTDOWN_STEP_TIMEOUT_SEC = 10.0

#: What the upkeep step gets instead, because it is the one step that is
#: *supposed* to take a while.
#:
#: :func:`~aruna.upkeep.loop.UpkeepLoop.stop` deliberately waits up to
#: :data:`~aruna.upkeep.loop.STOP_GRACE_SEC` for the cycle in flight, so that a
#: resolution pass is never cut between its writes - a scored prediction cannot
#: be edited afterwards (SPEC 22). Under the ordinary ten-second step timeout
#: that grace was unreachable: ``_safe`` cancelled ``stop()`` first, which
#: cancels the loop task exactly the way the grace exists to prevent, and
#: without even logging ``upkeep.stop_forced`` - the two numbers were chosen in
#: different files and nothing made them agree. Derived from the grace rather
#: than written out, so they cannot drift apart again; the extra step timeout on
#: top is slack for the forced cancel to be delivered and logged.
UPKEEP_SHUTDOWN_TIMEOUT_SEC = STOP_GRACE_SEC + SHUTDOWN_STEP_TIMEOUT_SEC


async def _safe(what: str, coro: Any, *, timeout: float = SHUTDOWN_STEP_TIMEOUT_SEC) -> None:
    """Await a shutdown step; log and continue if it fails **or hangs**.

    A hang is not an exception, and this function only caught exceptions. One
    blocked component therefore stopped every later step from running at all:
    Telegram processes updates one at a time, each command's first act is a
    MySQL write, and a pool waiting on an unreachable database waits forever.
    ``Application.stop()`` cannot drain its queue behind that handler, so the
    cache and the database were never closed and the process needed SIGKILL -
    with Ctrl+C already reassigned to the graceful path that was stuck.

    ``TimeoutError`` is caught before ``Exception`` deliberately: the builtin
    subclasses ``OSError`` subclasses ``Exception``, so the order is what makes
    the distinction reachable rather than decorative.
    """
    with contextlib.suppress(asyncio.CancelledError):
        try:
            await asyncio.wait_for(coro, timeout)
        except TimeoutError:
            log.warning(
                "shutdown.step_timeout",
                component=what,
                timeout_sec=timeout,
                impact="step abandoned so the remaining steps can still run",
            )
        except Exception as exc:  # noqa: BLE001 - shutdown must reach every component
            log.warning("shutdown.step_failed", component=what, error=str(exc))


__all__ = ["ArunaApplication"]
