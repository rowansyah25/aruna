"""Configuration.

Everything ARUNA needs to run comes from the environment (``.env`` locally,
real environment variables on a VPS).  Nothing is hardcoded in source, and no
secret is ever written back out at full value - see :meth:`Settings.secrets`,
which feeds the log redactor.

Each settings group is its own ``BaseSettings`` with its own prefix.  That
keeps ``ARUNA_DB_MIN_POOL`` unambiguous, which a single nested model with an
underscore delimiter cannot guarantee.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from aruna.core.enums import Horizon, Market, parse_market
from aruna.core.errors import ConfigError

#: Repository root - ``src/aruna/core/config.py`` -> up four levels.
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Build stage of this checkout.  Guards that must not be relaxed until the
#: relevant phase is actually built read this.
CURRENT_PHASE = 10


def _default_env_file() -> str | None:
    """Absolute path to the ``.env`` in use, or ``None`` if there is none.

    ``ARUNA_ENV_FILE`` overrides the location so a VPS deployment can point at
    a file outside the repository.
    """
    override = os.environ.get("ARUNA_ENV_FILE")
    candidate = Path(override) if override else PROJECT_ROOT / ".env"
    return str(candidate) if candidate.is_file() else None


ENV_FILE = _default_env_file()

_BASE_CONFIG = SettingsConfigDict(
    env_file=ENV_FILE,
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
)

Port = Annotated[int, Field(ge=1, le=65535)]

#: Forced on every MySQL connection.  Strict mode makes MySQL reject values it
#: would otherwise truncate or coerce, which is the difference between a failed
#: write and a silently wrong row (SPEC 4).
SQL_MODE = (
    "STRICT_ALL_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,"
    "ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION"
)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_")

    env: Literal["development", "staging", "production"] = "development"
    instance_name: str = "aruna-local"
    timezone: str = "Asia/Jakarta"

    paper_trading_enabled: bool = True
    real_trading_enabled: bool = False

    # NoDecode: without it pydantic-settings tries to JSON-parse a collection
    # field before any validator runs, and "CRYPTO,IDX" fails there with an
    # error that says nothing useful about markets.
    enabled_markets: Annotated[tuple[Market, ...], NoDecode] = (Market.CRYPTO, Market.IDX)

    @field_validator("enabled_markets", mode="before")
    @classmethod
    def _parse_markets(cls, value: object) -> object:
        """Accept ``CRYPTO,IDX`` and refuse the removed forex market by name."""
        if isinstance(value, str):
            names = [part for part in (p.strip() for p in value.split(",")) if part]
        elif isinstance(value, (list, tuple)):
            names = [str(part).strip() for part in value if str(part).strip()]
        else:
            return value
        if not names:
            raise ValueError("ARUNA_ENABLED_MARKETS must list at least one market")
        return tuple(parse_market(name) for name in names)

    @model_validator(mode="after")
    def _enforce_paper_only(self) -> AppSettings:
        # SPEC 46: real execution does not exist in this build.  Refusing at
        # config time means no operator can believe they enabled it.
        if self.real_trading_enabled:
            raise ValueError(
                "ARUNA_REAL_TRADING_ENABLED=true is rejected: real order execution is "
                f"not implemented in PHASE {CURRENT_PHASE} and the MVP is paper-only "
                "(SPEC 46). Set it to false."
            )
        if not self.paper_trading_enabled:
            raise ValueError(
                "ARUNA_PAPER_TRADING_ENABLED=false leaves ARUNA with no trading mode at "
                "all (SPEC 34). Set it to true."
            )
        return self


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_LOG_")

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    format: Literal["console", "json"] = "console"
    dir: Path = Path("logs")
    file_enabled: bool = True

    @field_validator("level", mode="before")
    @classmethod
    def _upper(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("format", mode="before")
    @classmethod
    def _lower(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    def resolved_dir(self) -> Path:
        return self.dir if self.dir.is_absolute() else PROJECT_ROOT / self.dir


class DatabaseSettings(BaseSettings):
    """MySQL connection settings.

    MySQL 8.0.16 is the floor: the schema relies on CHECK constraints, which
    earlier versions parse and then silently ignore - a constraint that is
    quietly not enforced is worse than none at all.
    """

    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_DB_")

    host: str = "127.0.0.1"
    port: Port = 3306
    name: str = "aruna"
    user: str = "root"
    password: SecretStr = SecretStr("")
    min_pool: int = Field(default=1, ge=1, le=64)
    #: Sepuluh dulu, dan sepuluh terlalu sedikit sejak tick futures memproses
    #: dua puluh simbol bersamaan: sepuluh menulis, sepuluh mengantre menunggu
    #: koneksi. Terukur, ``council_store.save`` menghabiskan 1206 ms rata-rata
    #: - biaya terbesar seluruh tick - dan sebagian besarnya adalah antrean,
    #: bukan MySQL. Dua puluh empat memberi satu koneksi per simbol dengan
    #: sisa untuk loop upkeep, health check dan perintah Telegram yang berjalan
    #: di saat yang sama. MySQL 8.4 bawaan mengizinkan 151 koneksi.
    max_pool: int = Field(default=24, ge=1, le=256)
    connect_timeout_sec: float = Field(default=10.0, gt=0)
    command_timeout_sec: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _check_pool(self) -> DatabaseSettings:
        if self.min_pool > self.max_pool:
            raise ValueError(
                f"ARUNA_DB_MIN_POOL ({self.min_pool}) cannot exceed "
                f"ARUNA_DB_MAX_POOL ({self.max_pool})"
            )
        return self

    def connect_kwargs(self, *, database: str | None = "") -> dict[str, object]:
        """Arguments for ``asyncmy.connect``.  Contains the password.

        ``database=None`` connects to the server without selecting a schema,
        which is what CREATE DATABASE needs.  The default sentinel ``""`` means
        "use the configured database".

        Two session settings are forced here rather than left to the server:

        * ``time_zone='+00:00'`` - MySQL converts ``TIMESTAMP`` values and
          evaluates ``CURRENT_TIMESTAMP`` in the *session* timezone.  The
          server's is ``SYSTEM``, so two clients in different zones would read
          different instants for the same row.  ARUNA stores UTC everywhere and
          pins the session so ``NOW(6)`` defaults are UTC too.
        * strict ``sql_mode`` - without it MySQL truncates over-long strings and
          coerces out-of-range numbers instead of failing, which is exactly the
          silent data fabrication SPEC 4 forbids.

        ``max_execution_time`` rides along too.  It is a server-side cap, so a
        slow query is killed by MySQL rather than abandoned mid-flight by a
        client-side cancel that would leave the pooled connection holding unread
        results.  It applies to read-only SELECT statements only - writes are
        bounded by ``innodb_lock_wait_timeout`` instead.

        All three go in ``init_command`` rather than using asyncmy's own
        ``sql_mode=`` parameter, which interpolates the value into
        ``SET sql_mode = <value>`` unquoted and therefore rejects any mode list
        containing a comma.  One statement also means the three settings arrive
        together or not at all.
        """
        timeout_ms = int(self.command_timeout_sec * 1000)
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password.get_secret_value(),
            "database": self.name if database == "" else database,
            "charset": "utf8mb4",
            "autocommit": True,
            "connect_timeout": self.connect_timeout_sec,
            "init_command": (
                "SET time_zone = '+00:00'"
                f", SESSION max_execution_time = {timeout_ms}"
                f", SESSION sql_mode = '{SQL_MODE}'"
            ),
        }

    @property
    def dsn(self) -> str:
        """Display form including the password.  Never log this."""
        pw = self.password.get_secret_value()
        return f"mysql://{self.user}:{pw}@{self.host}:{self.port}/{self.name}"

    @property
    def safe_dsn(self) -> str:
        """Display form with the password masked - safe for logs and Telegram."""
        masked = "***" if self.password.get_secret_value() else ""
        return f"mysql://{self.user}:{masked}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_REDIS_")

    enabled: bool = True
    host: str = "127.0.0.1"
    port: Port = 6379
    db: int = Field(default=0, ge=0, le=15)
    password: SecretStr = SecretStr("")
    namespace: str = "aruna"

    @property
    def url(self) -> str:
        """Connection URL, with the password percent-encoded.

        Unencoded, a password containing ``@``, ``/``, ``#``, ``?`` or ``:`` -
        which is to say a strong one - either changes what the URL means or
        fails to parse at all. The parse error carries the malformed URL, and
        therefore a fragment of the password, into a startup traceback that no
        redactor sees because the exception escapes before logging is
        configured (SPEC 43).

        A password of ``p@ss/w0rd`` currently reads as host ``ss`` on the way
        to database ``w0rd``. Encoding is not a nicety here; it is the
        difference between connecting and leaking.
        """
        pw = self.password.get_secret_value()
        auth = f":{quote(pw, safe='')}@" if pw else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"

    @property
    def safe_url(self) -> str:
        auth = ":***@" if self.password.get_secret_value() else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"

    def key(self, *parts: str) -> str:
        return ":".join((self.namespace, *parts))


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_TELEGRAM_")

    enabled: bool = True
    bot_token: SecretStr = SecretStr("")
    chat_id: str = ""
    # See the NoDecode note on AppSettings.enabled_markets.
    allowed_chat_ids: Annotated[tuple[str, ...], NoDecode] = ()
    alert_on_health_change: bool = True

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @property
    def active(self) -> bool:
        """Telegram only runs when it is enabled *and* actually configured.

        A missing token is not fatal: ARUNA runs headless and the health
        monitor reports Telegram as DISABLED (SPEC 49 - do not claim a feature
        works when it is not wired up).
        """
        return self.enabled and bool(self.bot_token.get_secret_value())

    @property
    def authorized_chat_ids(self) -> frozenset[str]:
        """Chats permitted to issue commands.  Defaults to the primary chat."""
        ids = set(self.allowed_chat_ids)
        if self.chat_id:
            ids.add(self.chat_id)
        return frozenset(ids)

    def is_authorized(self, chat_id: int | str) -> bool:
        allowed = self.authorized_chat_ids
        # An unconfigured allowlist must fail closed, not open.
        return bool(allowed) and str(chat_id) in allowed


class HealthSettings(BaseSettings):
    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_HEALTH_")

    interval_sec: float = Field(default=30.0, gt=0)
    db_timeout_sec: float = Field(default=5.0, gt=0)
    redis_timeout_sec: float = Field(default=3.0, gt=0)
    telegram_timeout_sec: float = Field(default=10.0, gt=0)
    failure_threshold: int = Field(default=2, ge=1, le=100)

    #: Ambang ukuran basis data, dalam MB (bagian 27-28 spec).
    #:
    #: Sampai 2026-08-21 tidak ada yang mengawasi pertumbuhan sama sekali:
    #: 506 MB, nol retention, 69.048 baris snapshot sehari, dan satu-satunya
    #: yang membuatnya terlihat adalah kueri `information_schema` yang diketik
    #: tangan. Sesudah gerbang perubahan dan retensi, proyeksi mantapnya ada di
    #: sekitar 400-500 MB - dua kali lipatnya berarti sesuatu tumbuh dengan
    #: cara yang tidak dirancang.
    db_size_warn_mb: float = Field(default=1_000.0, gt=0)
    db_size_critical_mb: float = Field(default=2_000.0, gt=0)


class UpkeepSettings(BaseSettings):
    """The maintenance loop: candle refresh, then resolution of due signals.

    Its own group rather than a corner of :class:`DataSettings`, which owns the
    SPEC 5 quality thresholds and the quote poll cadence.  ``resolve_limit``
    filed under ``ARUNA_DATA_`` would be in the wrong room.

    **There is deliberately no cadence knob.**  Each interval is refreshed once
    per bar, aligned to the bar boundary: 1m just after the minute closes, 15m
    after :00/:15/:30/:45, 1h after the hour, 1d after 00:00 UTC.  A cadence
    setting is one more thing to mis-set into the rate-limit abuse this design
    exists to avoid - refreshing every interval on every tick is exactly what
    the schedule below prevents.  The brakes that do exist are
    ``max_requests_per_cycle``, ``candle_intervals`` and ``enabled``.
    """

    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_UPKEEP_")

    #: Master switch for the whole loop.  Defaults on: the defect this loop
    #: exists to fix was a loop that never existed, and shipping it off by
    #: default would reproduce that exactly.  It is here so an operator who hits
    #: a provider rate limit can stop the maintenance work *without* also
    #: stopping the quote poll, which is a separate loop.
    enabled: bool = True

    #: How often the loop wakes up to see what is due.  Not a rate-limit knob:
    #: the work is bounded by the per-interval schedule, not by this.  At 15s a
    #: bar boundary is picked up at most 15s late, and an idle wake-up issues no
    #: provider request at all.
    tick_sec: float = Field(default=15.0, gt=0, le=300)

    #: Pause after a bar boundary before pulling that bar.  Requesting a 15m bar
    #: at exactly :00 often returns a bar the venue has not finalised, which is
    #: then rewritten on the next pass.
    candle_settle_sec: float = Field(default=15.0, ge=0)

    #: Bars pulled on a routine refresh: one still forming plus two already
    #: closed.  The floor of 2 exists because the forming bar *must* be pulled
    #: again to be stored closed (``upsert_candles`` is idempotent).  Tolerance
    #: for a long outage does not come from this number - catch-up is measured
    #: against the newest stored bar instead.
    candle_overlap_bars: int = Field(default=3, ge=2, le=50)

    #: Ceiling on a catch-up pull.  Equal to ``ARUNA_DATA_BACKFILL_CANDLES`` so
    #: the loop never asks for more than one explicit ``aruna fetch`` would.
    #: Whatever hole remains beyond it is reported as a gap, never filled
    #: (SPEC 4).
    candle_max_bars: int = Field(default=500, ge=1, le=5000)

    #: Minimum spacing between attempts for an interval that just failed.  This
    #: is what rescues 1d: without it a single failed daily pull would wait 24
    #: hours for its next chance.
    candle_retry_sec: float = Field(default=60.0, gt=0)

    #: Ceiling on provider *calls* in one cycle - not on HTTP round trips.
    #: ``HttpFetcher`` retries the statuses in
    #: :data:`~aruna.data.http.RETRY_STATUSES` (408/425/429/500/502/503/504) up
    #: to ``ARUNA_DATA_MAX_RETRIES`` times underneath each one, so the real
    #: request count can be several times this number.  Named honestly here
    #: because an operator sizing this against a venue's published limit would
    #: otherwise be sizing it against the wrong quantity.
    #:
    #: The multiplier is not the same on every adapter, and the difference is
    #: largest exactly where a published limit matters.  Yahoo and the RSS
    #: reader take the default set, 429 included.  The Binance transport does
    #: not: it subtracts 429 per call, because retrying through a rate limit is
    #: how a 429 becomes a 418.  This comment claimed the opposite - "most of
    #: all on 429" - for as long as that was true of every caller, and it is
    #: recorded here rather than quietly overwritten because an operator who
    #: sized against the old sentence sized against a request count the crypto
    #: path no longer spends.  What crypto spends instead is host rotation:
    #: every host but the last gets a single attempt and the last keeps the full
    #: budget, so one call costs at most ``len(HOSTS) - 1 + (1 +
    #: ARUNA_DATA_MAX_RETRIES)`` round trips - measured at 6 with three hosts
    #: and the default of 3.
    #:
    #: Work over the ceiling is deferred rather than dropped - the interval's
    #: success timestamp is not advanced, so it comes due again on the next
    #: tick.  One interval per cycle is always let through regardless: a market
    #: holding more assets than the ceiling would otherwise defer everything on
    #: every tick for ever, which is deferral in name and starvation in fact.
    max_requests_per_cycle: int = Field(default=40, ge=1, le=500)

    #: Empty means the refresh set is *derived* per market from the intervals
    #: outcome sampling actually reads, so the two cannot drift apart silently.
    #: Set it by hand (e.g. ``15m,30m,1h,4h,1d``) only when locking signals at
    #: horizons whose own evidence needs to stay fresh too.
    candle_intervals: str = ""

    #: Resolution and refresh fail independently, so they switch independently:
    #: refresh may keep running while resolution is paused to investigate a
    #: suspicious outcome.
    resolve_enabled: bool = True

    #: The finest horizon that can be locked is 1m and the finest stored
    #: sampling interval is 1m, so checking once a minute means a prediction is
    #: scored at most ~60s late - less than the one bar that would become its
    #: sample.  Waiting longer cannot produce a better sample.
    resolve_interval_sec: float = Field(default=60.0, gt=0)

    #: Not 50.  ``SignalRepository.due()`` orders by ``resolves_at``, so signals
    #: that cannot be scored yet accumulate at the *head* of the queue and are
    #: re-read every pass; too small a limit is spent entirely on them and the
    #: scoreable ones behind never get looked at.
    resolve_limit: int = Field(default=100, ge=1, le=1000)

    #: Whether the loop refreshes news of its own accord (PASAL 11).
    #:
    #: Off, news arrives only when a human types ``aruna news`` - which is what
    #: it did before this existed, and what left 280 stored items sixty hours
    #: old while ``NewsAgent`` went on reading them as present context. That is
    #: worse than having no news at all: missing evidence is visible, stale
    #: evidence is not (SPEC 4).
    news_enabled: bool = True

    #: RSS feeds publish on the order of minutes and ARUNA reads a handful of
    #: them, so this is polite rather than eager.  It also bounds how stale the
    #: council's news can be, which is the number that actually matters.
    news_interval_sec: float = Field(default=300.0, gt=0)

    #: Apakah loop menghitung korelasi pasangan sendiri (PASAL 14.41).
    #:
    #: Mati, korelasi hanya lahir ketika seseorang mengetik ``aruna correlate``
    #: - dan terukur pada 2026-08-21 tidak seorang pun pernah mengetiknya:
    #: tabelnya nol baris sementara empat puluh amatan berturut-turut
    #: melaporkan ``CORRELATION_RISK`` hilang dari keputusan.
    correlation_enabled: bool = True

    #: Dihitung dari candle yang sudah tersimpan, jadi ongkosnya bukan
    #: jaringan melainkan bacaan database - dan itulah kenapa angkanya sejam,
    #: bukan semenit: bar yang dipakai (4h) berubah tiap empat jam, jadi
    #: menghitung lebih sering hanya menghasilkan koefisien yang sama.
    correlation_interval_sec: float = Field(default=3600.0, gt=0)

    #: Apakah loop memproyeksikan ingatan pasar sendiri (PASAL 15.2).
    #:
    #: Mati, ``market_memories`` berhenti tumbuh dan pencarian kemiripan
    #: menjawab pertanyaan hari ini dengan sejarah kemarin - yang persis
    #: kegagalan `AdaptiveLearningService` sebelum ia disambungkan: kode yang
    #: benar, diuji, dan hanya berjalan saat seseorang mengetik perintahnya.
    memory_enabled: bool = True

    #: Sepuluh menit. Signal baru diresolusi beberapa per jam, jadi lebih cepat
    #: dari ini adalah kueri yang jawabannya kosong berulang-ulang - dan lebih
    #: lambat berarti keputusan membaca ingatan yang ketinggalan satu jam.
    memory_interval_sec: float = Field(default=600.0, gt=0)

    #: Apakah pembersih retensi berjalan sendiri (bagian 25-26 spec).
    #:
    #: Mati, basis data tumbuh selamanya - yang persis keadaannya sampai
    #: 2026-08-21: 506 MB, nol retention di seluruh basis kode, dan
    #: ``market_snapshots`` menyumbang 62% dengan 69.048 baris sehari.
    retensi_enabled: bool = True

    #: Sehari sekali. Retensi menghitung dalam hari, jadi menjalankannya lebih
    #: sering hanya mengulangi kueri yang jawabannya nol - dan setiap kueri itu
    #: memindai indeks tabel terbesar di basis data.
    retensi_interval_sec: float = Field(default=86_400.0, gt=0)

    #: Batas atas baris yang boleh dihapus dalam SATU sapuan, seluruh rencana.
    #:
    #: Bagian 26: cleanup harus bisa dihentikan. Sapuan pertama sesudah fitur
    #: ini hidup punya ratusan ribu baris kandidat; menghapusnya sekaligus
    #: memegang basis data selama seluruh sistem menunggu. Dengan batas ini
    #: tunggakan itu habis dalam beberapa hari, dan tidak ada satu siklus pun
    #: yang tersandera.
    retensi_batas_sapuan: int = Field(default=20_000, gt=0)

    #: Apakah fase simulasi berpemicu berjalan (bagian 16.17).
    #:
    #: Mati, ARUNA kembali ke standard analysis sepenuhnya - yang bagian 16.2
    #: sebut sebagai jalur normal, jadi mematikannya tidak merusak apa pun yang
    #: menghasilkan keputusan. Yang hilang cuma buktinya.
    #:
    #: Ada sebagai sakelar karena fase ini baru dan menulis ke tabel baru;
    #: fase baru yang tidak bisa dimatikan berarti satu-satunya cara
    #: menghentikannya adalah menghentikan ARUNA.
    scenario_enabled: bool = True

    #: Apakah penilaian PASAL 15.44 berjalan sendiri.
    #:
    #: Mati, gerbang per timeframe tidak pernah menutup: putusannya tidak
    #: pernah ditulis, dan `susun()` tanpa putusan tidak menggerbangi apa pun.
    #: Terukur 2026-08-21: ingatan membantu di 15m (+14) dan tidak di 1h (-7),
    #: sementara 1h justru yang dipinjam jalur keputusan langsung.
    manfaat_enabled: bool = True

    #: Sehari sekali. Perbandingan kemiripannya kuadratik atas ribuan ingatan,
    #: dan jawabannya bergerak dalam hitungan hari - bukan menit.
    manfaat_interval_sec: float = Field(default=86_400.0, gt=0)

    #: Apakah kalibrasi dan reliability diukur ulang sendiri (SPEC 29, 30).
    #:
    #: Mati, keduanya berhenti di pengukuran terakhir yang seseorang ketik
    #: dengan tangan. Terukur 2026-08-21: `calibration_snapshots` berisi tiga
    #: baris, terakhir 2026-08-15 - enam hari sebelumnya - sementara
    #: verdict-nya sendiri berbunyi "OVERCONFIDENT" di seluruh pita keyakinan.
    review_enabled: bool = True

    #: Sehari sekali. Kalibrasi bergerak seiring outcome yang menumpuk, dan
    #: outcome menumpuk dalam hitungan jam, bukan menit.
    review_interval_sec: float = Field(default=86_400.0, gt=0)

    #: Berapa prediksi yang ditinjau tiap sapuan.
    #:
    #: Lima ribu, bukan lima ratus. Terukur 2026-08-21: pada 500 hanya 67 baris
    #: lolos saringan kalibrasi dan **tiga dari empat pita kekurangan sampel**;
    #: pada 5.000, 777 lolos dan keempat pita terukur. Batas yang terlalu kecil
    #: tidak membuat kalibrasi salah - ia membuatnya tidak ada, lalu diam.
    review_limit: int = Field(default=5000, gt=0)

    #: Whether the loop locks new predictions of its own accord (SPEC 10, 20).
    #:
    #: Off, ARUNA refreshes candles and scores whatever a human locked with
    #: ``aruna signal`` - which is what it did before this existed, and what
    #: left the record frozen at nine paper trades from one afternoon.  On, it
    #: convenes the council on a schedule and freezes each verdict, so the
    #: sample grows across days instead of describing a single one.
    #:
    #: Nothing here executes anything.  A locked prediction is a paper record
    #: (SPEC 46): no order is placed and no funds move.
    lock_enabled: bool = True

    #: Markets locked automatically.  CRYPTO only by default: it trades around
    #: the clock, and it is the only market that has ever produced a
    #: directional verdict here - the last 88 IDX predictions were all
    #: NO_SIGNAL.  Adding IDX is a config change, not a code change.
    lock_markets: str = "CRYPTO"

    #: Horizons locked automatically - the SPEC 10 default set, matching
    #: ``aruna signal``.
    #:
    #: **One prediction per horizon per bar.**  The cadence is the horizon's
    #: own length, so each prediction gets a fresh, non-overlapping window and
    #: the observations are independent.  Locking faster would multiply rows
    #: without multiplying evidence: the nine paper trades on record are nine
    #: losses and *one* observation - all SELL, one afternoon, three correlated
    #: assets, overlapping horizons.  A sample built that way looks like proof
    #: and measures almost nothing.
    lock_horizons: str = "15m,1h,1d"

    #: Perpetual yang dianalisis loop futures, dipisah koma.
    #:
    #: Di sini, bukan sebagai bawaan argumen CLI, supaya operator mengubahnya
    #: di satu tempat dan tidak perlu menyentuh ``ARUNA.bat``. Berkas itu
    #: memanggil ``supervise`` tanpa argumen sama sekali, jadi bawaan CLI-lah
    #: yang selalu berlaku - dan bawaan yang hanya bisa diubah dengan mengedit
    #: kode bukan konfigurasi.
    #:
    #: Dua puluh nama di bawah **diverifikasi ke bursa**, bukan ditulis dari
    #: ingatan, dan keduanya ternyata berbeda: ``MATIC`` sudah diganti ``POL``,
    #: dan ``TON`` tidak lolos pemeriksaan TRADING+PERPETUAL. Simbol yang salah
    #: tidak gagal dengan berisik - ia muncul sebagai penarikan candle yang
    #: kosong berhari-hari, dan itu terlihat persis seperti pasar yang sepi.
    #:
    #: Yang **tidak** dipakai sebagai kriteria: peringkat volume satu hari. Ia
    #: diukur juga, dan hasilnya memuat tujuh token yang baru listing - besar
    #: hari ini, mungkin hilang sebulan lagi. Rekam jejak yang dibangun di
    #: atasnya mengukur likuiditas yang menguap, bukan analisis.
    #:
    #: Ongkosnya terukur: satu tick memproses tiga simbol dalam lima detik -
    #: council penuh per simbol - jadi dua puluh simbol sekitar tiga puluh
    #: empat detik dari interval sembilan ratus detik.
    futures_symbols: str = (
        "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,TRXUSDT,SUIUSDT,DOGEUSDT,"
        "NEARUSDT,LINKUSDT,ADAUSDT,FILUSDT,AVAXUSDT,LTCUSDT,POLUSDT,UNIUSDT,"
        "DOTUSDT,APTUSDT,OPUSDT,INJUSDT"
    )

    @property
    def futures_symbol_list(self) -> tuple[str, ...]:
        """Simbol futures sebagai tuple, huruf besar, tanpa yang kosong."""
        return tuple(
            token.strip().upper()
            for token in self.futures_symbols.split(",")
            if token.strip()
        )

    #: Health threshold for ``candles:<MARKET>``: a bar older than this many
    #: interval-lengths is reported.  Below 2x it would blink on every network
    #: hiccup and train the operator to ignore the colour.
    stale_tolerance_factor: float = Field(default=3.0, ge=1.0)

    @field_validator("candle_intervals")
    @classmethod
    def _validate_intervals(cls, value: str) -> str:
        """Reject an unknown interval here rather than at the first refresh.

        A typo in this list would otherwise be discovered by an unattended loop
        at three in the morning, as a silently smaller refresh set.
        """
        tokens = [token.strip().lower() for token in value.split(",") if token.strip()]
        for token in tokens:
            try:
                Horizon(token)
            except ValueError:
                raise ValueError(
                    f"ARUNA_UPKEEP_CANDLE_INTERVALS lists unknown interval {token!r}; "
                    "valid: " + ", ".join(h.value for h in Horizon)
                ) from None
        return ",".join(tokens)

    @property
    def candle_interval_overrides(self) -> tuple[Horizon, ...]:
        """The explicit interval list, empty when the derived default applies."""
        return tuple(Horizon(token) for token in self.candle_intervals.split(",") if token)

    @field_validator("lock_horizons")
    @classmethod
    def _validate_lock_horizons(cls, value: str) -> str:
        """Same reasoning as ``candle_intervals``: a typo must fail at startup.

        Left to the loop, an unknown horizon would silently shrink the set that
        gets locked, and the symptom - fewer predictions than expected - is one
        nobody notices until the sample is needed.
        """
        tokens = [token.strip().lower() for token in value.split(",") if token.strip()]
        for token in tokens:
            try:
                Horizon(token)
            except ValueError:
                raise ValueError(
                    f"ARUNA_UPKEEP_LOCK_HORIZONS lists unknown horizon {token!r}; "
                    "valid: " + ", ".join(h.value for h in Horizon)
                ) from None
        return ",".join(tokens)

    @field_validator("lock_markets")
    @classmethod
    def _validate_lock_markets(cls, value: str) -> str:
        tokens = [token.strip().upper() for token in value.split(",") if token.strip()]
        for token in tokens:
            try:
                Market(token)
            except ValueError:
                raise ValueError(
                    f"ARUNA_UPKEEP_LOCK_MARKETS lists unknown market {token!r}; "
                    "valid: " + ", ".join(m.value for m in Market)
                ) from None
        return ",".join(tokens)

    @property
    def lock_horizon_set(self) -> tuple[Horizon, ...]:
        return tuple(Horizon(token) for token in self.lock_horizons.split(",") if token)

    @property
    def lock_market_set(self) -> tuple[Market, ...]:
        return tuple(Market(token) for token in self.lock_markets.split(",") if token)


class ProviderSettings(BaseSettings):
    """Market data provider selection and credentials.

    A provider left blank means that data source is unconfigured, and ARUNA
    reports ``DATA SOURCE UNAVAILABLE`` rather than substituting a guess
    (SPEC 4).

    The only registered crypto adapter is ``binance-spot``, and there is no
    second one to fall back to (PASAL 5).  A blank ``crypto_provider`` disables
    crypto outright; it never quietly selects something else.

    An earlier version of this docstring stated that Binance was unreachable
    from Indonesian networks because its domains resolve to the Kominfo
    TrustPositif block page.  That was measured once, on one network.  It is
    not true here: on 2026-08-17 ``api.binance.com``, ``api1``, ``api2`` and
    ``fapi.binance.com`` each answered from this machine.  Reachability is a
    property of the network a deployment sits on, not of the host, so ARUNA
    calls the venue and reports whichever answer it gets instead of assuming
    one (SPEC 49).

    What did not change with the network: Binance is not registered with
    Bappebti.  That is a legal fact rather than a network one, it is recorded
    in the adapter's ``regulatory_note``, and SPEC 47 leaves the consequence to
    the operator.

    **There is no crypto API key or secret here, deliberately.**  ARUNA reads
    Binance through public market-data endpoints only - nothing under
    :mod:`aruna.data.crypto` reads a credential or signs a request - and
    PASAL 41 forbids a trading API outright.  ``crypto_provider_api_key`` and
    ``crypto_provider_api_secret`` were fields on this class until they were
    removed under PASAL 34; a named slot invites an operator to paste a real
    Binance key into a system that has no execution path to use it, which is
    the one way this configuration could become dangerous.

    The argument for keeping them was that :meth:`Settings.secrets` feeds the
    log redactor, so a name the redactor does not know is a name that can be
    printed.  It does not hold, and the reason is one line above:
    ``_BASE_CONFIG`` sets ``extra="ignore"``.  An
    ``ARUNA_CRYPTO_PROVIDER_API_KEY`` left in a ``.env`` is therefore never
    loaded into this process at all, and a value ARUNA never reads is a value
    ARUNA cannot print.  ``TestKredensialCryptoTidakAda`` in
    ``tests/test_config.py`` pins exactly that, because it is the whole of the
    argument for the deletion.
    """

    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_")

    crypto_provider: str = "binance-spot"

    idx_provider: str = "yahoo"
    idx_provider_api_key: SecretStr = SecretStr("")

    news_provider: str = "rss"
    news_provider_api_key: SecretStr = SecretStr("")

    fundamental_provider: str = "yahoo"
    fundamental_provider_api_key: SecretStr = SecretStr("")

    @property
    def configured(self) -> dict[str, bool]:
        return {
            "crypto": bool(self.crypto_provider),
            "idx": bool(self.idx_provider),
            "news": bool(self.news_provider),
            "fundamental": bool(self.fundamental_provider),
        }


class DataSettings(BaseSettings):
    """Ingestion cadence and the SPEC 5 data-quality thresholds.

    Every threshold here decides whether ARUNA trusts a price.  They are
    configuration rather than constants because the right value differs sharply
    between a 24/7 crypto venue and a delayed equities feed.
    """

    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_DATA_")

    #: Seconds between polls of a provider that has no usable stream.
    poll_interval_sec: float = Field(default=5.0, gt=0)

    #: Jarak minimum antar poll untuk IDX, terpisah dari cadence crypto.
    #:
    #: Bukan angka yang dipilih dari selera. Diukur dari enam jam snapshot
    #: tersimpan: stempel waktu provider Yahoo untuk saham IDX bergerak paling
    #: cepat sekitar sekali per 21 detik (BBCA: 1011 stempel berbeda dari 1269
    #: panggilan) dan paling lambat sekali per 72 detik (GOTO: 301). Memanggil
    #: tiap lima detik karena itu menghasilkan observasi yang sama dibaca empat
    #: sampai empat belas kali, tanpa satu pun angka baru.
    #:
    #: Dua puluh detik menyamai simbol yang paling cepat berubah, jadi tidak
    #: ada pembaruan yang terlewat, sementara jumlah permintaan ke Yahoo turun
    #: empat kali lipat. Crypto tidak ikut melambat - Binance berubah hampir
    #: pada setiap panggilan, dan di sana lima detik memang menghasilkan data
    #: baru.
    idx_poll_interval_sec: float = Field(default=20.0, gt=0)
    #: How many candles to pull per interval on a backfill.
    backfill_candles: int = Field(default=500, ge=1, le=5000)

    request_timeout_sec: float = Field(default=15.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)

    # ---- SPEC 5 thresholds ------------------------------------------------
    #: A crypto quote older than this is STALE.
    stale_tick_sec: float = Field(default=60.0, gt=0)
    #: Equities feeds are delayed by design; this is measured *on top of* the
    #: provider's declared delay, not from now.
    stale_idx_tick_sec: float = Field(default=300.0, gt=0)
    #: Bid/ask wider than this is ABNORMAL_SPREAD.
    max_spread_bps: float = Field(default=200.0, gt=0)
    #: Move larger than this between consecutive quotes is ABNORMAL_PRICE.
    max_price_jump_pct: float = Field(default=10.0, gt=0)
    #: Provider round trip above this is a LATENCY_SPIKE.
    latency_spike_ms: float = Field(default=5_000.0, gt=0)
    #: A provider timestamp further ahead than this is a TIMESTAMP_MISMATCH.
    #:
    #: Sized for clock skew, not for leakage.  An unsynchronised consumer
    #: machine routinely sits seconds off a venue's clock, and rejecting every
    #: observation over that would make the feed unusable while revealing
    #: nothing.  The future data SPEC 24 forbids arrives minutes or hours
    #: early, not seconds.
    #:
    #: The 30 came from a venue ARUNA no longer uses, so treat it as an
    #: unverified inheritance rather than a threshold sized for Binance.  What
    #: *is* measured: on 2026-08-17 this machine's clock ran ~12.1s behind, and
    #: both ``/api/v3/time`` and ``time.windows.com`` agreed on that figure -
    #: two independent references, so the skew is local, not the venue's.  That
    #: leaves roughly 18s of headroom here.  ``aruna providers`` prints the
    #: live skew from ``/api/v3/time``; size this against that reading rather
    #: than against this comment.
    future_tolerance_sec: float = Field(default=30.0, ge=0)

    #: Assumed delay of the IDX feed, in seconds.
    #:
    #: Yahoo does not return ``exchangeDataDelayedBy`` for Jakarta symbols, so
    #: ARUNA cannot read the real figure and assumes the conventional 15
    #: minutes.  Treat it as a floor, not a measurement: the true delay may be
    #: larger, never smaller.  Nothing may describe this feed as realtime.
    idx_declared_delay_sec: int = Field(default=900, ge=0)


class DecisionSettings(BaseSettings):
    """The Decision Score threshold (PASAL 14.17).

    PASAL 14.17 requires this to be configurable and warns it is not universal
    law - it is calibrated against historical performance.  What it does *not*
    grant is self-calibration: PASAL 11.16 and 12.26 forbid ARUNA modifying its
    own model, so the path is measure, propose, human approves.  Nothing in
    ``aruna.decision`` can write this value.

    **The bounds are the point of this class.**  A threshold above the highest
    reachable score silences ARUNA permanently while logging nothing wrong, and
    a threshold at or below zero turns every symbol into a direction.  Both are
    rejected at startup rather than at the moment somebody wonders why the bot
    has said nothing for a week.
    """

    model_config = SettingsConfigDict(**_BASE_CONFIG, env_prefix="ARUNA_DECISION_")

    #: LONG at ``>= +threshold``, SHORT at ``<= -threshold``.
    #:
    #: The ceiling equals ``aruna.decision.score.MAX_ARAH`` - the sum of every
    #: directional weight.  It is spelled literally here rather than imported so
    #: that ``core`` keeps depending on nothing above it; a test asserts the two
    #: numbers stay equal, so drift fails loudly instead of quietly permitting
    #: an unreachable threshold.
    threshold: float = Field(default=60.0, gt=0, le=81.0)


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """The whole configuration, assembled from the groups above.

    Deliberately a plain model rather than ``BaseSettings``: each group already
    binds its own prefix, and a composite settings class would additionally try
    to resolve bare names like ``db`` or ``log`` from the environment.
    """

    app: AppSettings = Field(default_factory=AppSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    upkeep: UpkeepSettings = Field(default_factory=UpkeepSettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)

    # ---- introspection -------------------------------------------------

    def secrets(self) -> frozenset[str]:
        """Every secret value currently in play.

        The log redactor scrubs these from rendered output, so anything that
        must never appear in a log file or a Telegram message belongs here.
        """
        candidates = (
            self.db.password.get_secret_value(),
            self.redis.password.get_secret_value(),
            self.telegram.bot_token.get_secret_value(),
            # No crypto entry: the Binance adapter holds no credential to leak.
            # See ProviderSettings for why deleting the field was safer than
            # keeping it so the redactor would know the name.
            self.providers.idx_provider_api_key.get_secret_value(),
            self.providers.news_provider_api_key.get_secret_value(),
            self.providers.fundamental_provider_api_key.get_secret_value(),
        )
        # Very short values would scrub unrelated text out of every log line.
        return frozenset(value for value in candidates if len(value) >= 6)

    def phase_notices(self) -> list[str]:
        """Gaps that are expected at the current build stage.

        Kept apart from :meth:`startup_warnings` deliberately.  Unconfigured
        PHASE 2 providers are not a misconfiguration in a PHASE 1 build, and
        reporting them as degraded health would leave the system permanently
        yellow - which trains an operator to ignore the colour.  These are
        stated plainly (SPEC 4, 49) without counting against health.
        """
        notices: list[str] = []

        if self.telegram.enabled and not self.telegram.bot_token.get_secret_value():
            notices.append(
                "Telegram is enabled but ARUNA_TELEGRAM_BOT_TOKEN is empty - "
                "running headless, no signals or alerts will be delivered"
            )
        if not self.redis.enabled:
            notices.append("Redis is disabled - caching and rate limiting are unavailable")
        if not self.db.password.get_secret_value() and self.app.env == "development":
            # Laragon ships MySQL with root and no password.
            notices.append(
                f"ARUNA_DB_PASSWORD is empty - normal for a local Laragon install, "
                f"but MySQL user {self.db.user!r} must have a password anywhere else"
            )

        unconfigured = [name for name, ok in self.providers.configured.items() if not ok]
        if unconfigured:
            notices.append(
                "DATA SOURCE UNAVAILABLE for: "
                + ", ".join(sorted(unconfigured))
                + " - set the corresponding ARUNA_PROVIDER_* key to enable it"
            )
        return notices

    def startup_warnings(self) -> list[str]:
        """Configuration that is wrong *now* and should be fixed.

        Anything here degrades health, so it must stay limited to things an
        operator can and should act on today.
        """
        warnings: list[str] = []

        if self.telegram.active and not self.telegram.authorized_chat_ids:
            warnings.append(
                "Telegram has no authorized chat ids - every command will be refused. "
                "Set ARUNA_TELEGRAM_CHAT_ID"
            )
        if not self.db.password.get_secret_value() and self.app.env != "development":
            warnings.append(
                f"ARUNA_DB_PASSWORD is empty and ARUNA_ENV={self.app.env} - "
                f"MySQL user {self.db.user!r} is reachable without a password"
            )
        if self.app.env == "production" and self.log.format != "json":
            warnings.append("production environment should use ARUNA_LOG_FORMAT=json")
        return warnings

    def describe(self) -> dict[str, object]:
        """Redacted snapshot for logs, ``/status``, and health output."""
        return {
            "instance": self.app.instance_name,
            "env": self.app.env,
            "phase": CURRENT_PHASE,
            "markets": [m.value for m in self.app.enabled_markets],
            "trading_mode": "PAPER",
            "real_trading_enabled": self.app.real_trading_enabled,
            "database": self.db.safe_dsn,
            "redis": self.redis.safe_url if self.redis.enabled else "disabled",
            "telegram": "active" if self.telegram.active else "disabled",
            "upkeep": "active" if self.upkeep.enabled else "disabled",
            "log_level": self.log.level,
            "env_file": ENV_FILE or "(none - using process environment)",
            "providers": self.providers.configured,
        }


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, loaded once.

    Raises :class:`ConfigError` with a readable message instead of a pydantic
    traceback, because this is usually the first thing a new operator hits.
    """
    try:
        return Settings()
    except Exception as exc:
        raise ConfigError(f"invalid ARUNA configuration: {exc}") from exc


def reset_settings_cache() -> None:
    """Drop the cached settings.  Used by tests and by ``aruna doctor``."""
    get_settings.cache_clear()


__all__ = [
    "CURRENT_PHASE",
    "ENV_FILE",
    "PROJECT_ROOT",
    "AppSettings",
    "DatabaseSettings",
    "DecisionSettings",
    "HealthSettings",
    "LogSettings",
    "ProviderSettings",
    "RedisSettings",
    "Settings",
    "TelegramSettings",
    "UpkeepSettings",
    "get_settings",
    "reset_settings_cache",
]
