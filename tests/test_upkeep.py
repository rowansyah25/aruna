"""Upkeep: refresh the candles, then score what is due.

Two defects made this loop necessary, and they fed each other. Spot candles
were only ever written by a manual ``aruna fetch``, so 1m/5m/15m/30m/1h froze
at whatever minute the last backfill happened to run; and ``resolve_due()``
existed, worked, and was reachable from exactly one CLI flag - so 118 signals
sat LOCKED and ``paper_trades`` was empty. Resolution reads candles. Frozen
candles meant the few resolutions that were attempted answered ``no_prices``.

So the properties worth testing here are not "the classes work". They are:

* the loop is **actually reached from a live process** - this repo's recurring
  defect is code that is written, exported, unit-tested and never called
  (``verify()``, the discipline engine, a ``stance='OPPOSE'`` filter matching a
  value no enum has). ``TestKabelKeAppStartup`` fails if the wire is cut;
* ``background=False`` starts **no** periodic loop, because the last violation
  of that promise had every short CLI command steal the Telegram token from
  ``aruna run`` and announce "ARUNA ONLINE" then "Shutting down";
* each interval keeps **its own cadence** - refreshing all of them every tick
  is the rate-limit bill this design exists to avoid, and so is one dead symbol
  dragging a whole market down to the retry floor (measured: 28,800 requests a
  day where a healthy market spends 7,810);
* work is **actually done**, not merely permitted - the IDX settlement sweep is
  finished when the day's closing bars are stored, and the tests below assert
  ``RefreshResult.refreshed``, never just the gate that let the pass in;
* a failing tick **does not end the run**, because an unattended loop that dies
  on the first network hiccup is worse than no loop at all;
* candles are refreshed **before** resolution inside one cycle, since a score
  written against a stale series is permanent (SPEC 22).

Nothing here touches the network, a provider, or the wall clock: the loop takes
an injected ``sleep``/``now`` for exactly that reason.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from tests.conftest import make_settings

from aruna.core.claims import find_forbidden
from aruna.core.clock import now_utc
from aruna.core.config import UpkeepSettings
from aruna.core.enums import HealthStatus, Horizon, Market, horizons_for_market
from aruna.data.ingest import IngestResult
from aruna.health.upkeep import CandleFreshnessCheck, UpkeepCheck, lag_text
from aruna.signals.outcome import STORED_INTERVALS, sampling_intervals
from aruna.upkeep.candles import (
    SAMPLING_FALLBACK_DEPTH,
    CandleRefresher,
    RefreshResult,
    bar_start,
    refresh_intervals,
)
from aruna.upkeep.loop import MAX_ERRORS, UpkeepLoop, UpkeepStats

# Monday. CRYPTO ignores the calendar; the IDX moments below are chosen against
# the real exchange windows (WIB = UTC+7).
MONDAY = datetime(2026, 8, 17, 0, 0, tzinfo=UTC)
IDX_OPEN = datetime(2026, 8, 17, 3, 0, tzinfo=UTC)  # Mon 10:00 WIB, session 1
# 16:15 WIB exactly: post-trading close, and the 15m bar boundary itself - the
# instant the settlement sweep used to be spent on, before anything was due.
IDX_SETTLEMENT = datetime(2026, 8, 17, 9, 15, tzinfo=UTC)
IDX_AFTER_CLOSE = datetime(2026, 8, 17, 9, 20, tzinfo=UTC)  # Mon 16:20 WIB
IDX_WEEKEND = datetime(2026, 8, 16, 5, 0, tzinfo=UTC)  # Sunday


# ---------------------------------------------------------------------------
# stand-ins - no provider, no database, no clock
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Asset:
    id: int
    symbol: str


@dataclass(frozen=True, slots=True)
class _Caps:
    supported_intervals: tuple[Horizon, ...]

    def supports(self, interval: Horizon) -> bool:
        return interval in self.supported_intervals


class _Provider:
    def __init__(self, market: Market, intervals: tuple[Horizon, ...]) -> None:
        self.name = "fake-provider"
        self.market = market
        self.capabilities = _Caps(intervals)


@dataclass(frozen=True, slots=True)
class _Call:
    """One ``backfill()`` the refresher asked for.

    ``symbols`` matters as much as ``limit``: the refresher sizes the pull per
    asset now, so "how many bars" is only half the question - "for whom" is the
    other half, and getting it wrong is what made one symbol with no stored
    candles pull five hundred bars on everybody's behalf.
    """

    intervals: tuple[Horizon, ...]
    limit: int | None
    quiet: bool
    detect_gaps: bool
    symbols: tuple[str, ...] = ()

    @property
    def interval(self) -> Horizon:
        return self.intervals[0]


class _Ingestor:
    """Stands in for ``MarketIngestor``: records the ask, answers from a flag.

    ``fail_with`` is mutable so a test can make one pass fail and the next
    succeed without a script - which is what the retry rules are about.
    ``fail_symbols`` is the other shape of failure and the more dangerous one:
    ``backfill`` reports a dead symbol *inside* its result, per asset, while the
    rest of the market answers normally.
    """

    def __init__(
        self,
        market: Market = Market.CRYPTO,
        *,
        intervals: tuple[Horizon, ...] | None = None,
        assets: int = 1,
        fail_with: Exception | str | None = None,
        fail_symbols: tuple[str, ...] = (),
    ) -> None:
        self.market = market
        self.provider = _Provider(
            market, tuple(Horizon) if intervals is None else intervals
        )
        self.calls: list[_Call] = []
        self.fail_with = fail_with
        self.fail_symbols = {symbol.upper() for symbol in fail_symbols}
        self._assets = [_Asset(i + 1, f"SYM{i + 1}") for i in range(assets)]

    async def assets(self) -> list[_Asset]:
        return list(self._assets)

    async def backfill(
        self,
        intervals: tuple[Horizon, ...],
        *,
        limit: int | None = None,
        symbols: tuple[str, ...] | None = None,
        quiet: bool = False,
        detect_gaps: bool = True,
    ) -> IngestResult:
        wanted = {symbol.upper() for symbol in symbols} if symbols else None
        chosen = [
            asset
            for asset in self._assets
            if wanted is None or asset.symbol.upper() in wanted
        ]
        self.calls.append(
            _Call(
                tuple(intervals),
                limit,
                quiet,
                detect_gaps,
                tuple(asset.symbol for asset in chosen),
            )
        )
        if isinstance(self.fail_with, Exception):
            raise self.fail_with
        result = IngestResult(market=self.market, provider="fake-provider")
        for asset in chosen:
            if self.fail_with == "result" or asset.symbol.upper() in self.fail_symbols:
                result.failures.append(
                    f"{asset.symbol} {intervals[0].value}: provider returned none"
                )
            else:
                result.candles += 3
        return result

    def intervals_called(self) -> list[Horizon]:
        return [call.interval for call in self.calls]


class _LogSpy:
    """Captures the module logger, because some fixes only speak in warnings.

    A starved interval and a hole deeper than the ceiling both have to be
    *said*; asserting they are is the only way to keep them from becoming
    another silent condition.
    """

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **fields: Any) -> None:
        self.warnings.append((event, fields))

    def info(self, event: str, **fields: Any) -> None:
        return None

    def debug(self, event: str, **fields: Any) -> None:
        return None

    def exception(self, event: str, **fields: Any) -> None:
        return None

    def events(self, name: str) -> list[dict[str, Any]]:
        return [fields for event, fields in self.warnings if event == name]


class _Ingest:
    """Stands in for ``IngestService`` - only what the refresher reads."""

    def __init__(self, *ingestors: _Ingestor) -> None:
        self._by_market = {ingestor.market: ingestor for ingestor in ingestors}
        self.closed = False

    @property
    def markets(self) -> tuple[Market, ...]:
        return tuple(self._by_market)

    def ingestor(self, market: Market) -> _Ingestor | None:
        return self._by_market.get(market)

    async def close(self) -> None:
        self.closed = True


class _Store:
    """Answers the single ``newest_closed_candles`` query the refresher makes."""

    def __init__(
        self,
        newest: dict[tuple[int, str], datetime] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.newest = dict(newest or {})
        self.error = error
        self.queries: list[tuple[Market, tuple[Horizon, ...]]] = []

    async def newest_closed_candles(
        self, market: Market, intervals: tuple[Horizon, ...]
    ) -> dict[tuple[int, str], datetime]:
        self.queries.append((market, tuple(intervals)))
        if self.error is not None:
            raise self.error
        codes = {interval.value for interval in intervals}
        return {key: value for key, value in self.newest.items() if key[1] in codes}


@dataclass
class _Resolved:
    """A ``ResolveResult`` shaped answer - the loop reads it by attribute."""

    resolved: int = 0
    awaiting_candles: int = 0
    no_prices: int = 0
    failures: list[str] = field(default_factory=list)


class _Resolver:
    """Stands in for ``SignalService`` - only ``resolve_due`` is ever called."""

    def __init__(
        self,
        result: _Resolved | None = None,
        *,
        error: Exception | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[datetime, int]] = []
        self.result = result or _Resolved()
        self.error = error
        self.log = log

    async def resolve_due(self, *, reference: datetime, limit: int) -> _Resolved:
        if self.log is not None:
            self.log.append("resolve")
        self.calls.append((reference, limit))
        if self.error is not None:
            raise self.error
        return self.result


class _RecordingRefresher:
    """Stands in for ``CandleRefresher`` when only the phase order matters."""

    def __init__(
        self,
        log: list[str] | None = None,
        *,
        error: Exception | None = None,
        result: RefreshResult | None = None,
    ) -> None:
        self.log = log
        self.error = error
        self.result = result or RefreshResult()
        self.calls: list[datetime] = []

    async def refresh(self, *, now: datetime) -> RefreshResult:
        if self.log is not None:
            self.log.append("refresh")
        self.calls.append(now)
        if self.error is not None:
            raise self.error
        return self.result

    def state(self) -> dict[str, dict[str, object]]:
        return {}


class _Clock:
    """A clock the test moves by hand, so no test waits on real time."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def _settings(**overrides: object) -> UpkeepSettings:
    return UpkeepSettings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _refresher(
    ingest: _Ingest, store: _Store | None = None, **overrides: object
) -> CandleRefresher:
    return CandleRefresher(
        ingest=ingest,  # type: ignore[arg-type]
        store=store or _Store(),  # type: ignore[arg-type]
        settings=_settings(**overrides),
    )


async def _wait_until(predicate, *, what: str, timeout: float = 5.0) -> None:
    """Wait for a background task to actually do something."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.005)


# ---------------------------------------------------------------------------
# the wire into app.startup()
# ---------------------------------------------------------------------------


class _FakeDb:
    async def close(self) -> None:
        return None

    async def execute(self, *_args: object, **_kwargs: object) -> int:
        """Fase retensi menyapu lewat `execute`.

        Dulu tidak pernah sampai ke sini: fase resolusi berjalan lebih dulu dan
        palsu ini cukup. Sejak jalur spot dicabut, resolusi tidak ada lagi dan
        siklusnya berjalan sampai retensi - bentuk palsu yang kurang lengkap
        baru terlihat sekarang.
        """
        return 0

    async def fetch(self, *_args: object, **_kwargs: object) -> list:
        return []

    async def fetchrow(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeCache:
    async def connect(self) -> bool:
        return False

    async def close(self) -> None:
        return None

    async def set_json(self, *_args: object, **_kwargs: object) -> None:
        return None


def _wired_app(ingest: _Ingest, resolver: _Resolver, **upkeep: object):
    """A real ``ArunaApplication`` with only its I/O steps stubbed out.

    Everything from ``_start_ingestion`` onwards runs exactly as it does in
    production - and that is the whole point. If ``_start_upkeep()`` is dropped
    from ``startup()``, or its ``start()`` call loses the ``_background`` guard,
    the tests built on this fail rather than passing against dead code.
    """
    from aruna.app import ArunaApplication

    class _App(ArunaApplication):
        def configure_logging(self) -> None:
            return None

        async def _start_database(self) -> None:
            return None

        def _build_repositories(self) -> None:
            self.market_data = _Store()  # type: ignore[assignment]
            self.signals = resolver
            self.signal_store = None

        async def _verify_schema(self) -> None:
            return None

        async def _load_runtime_state(self) -> None:
            return None

        async def _load_measured_history(self) -> None:
            return None

        async def _start_ingestion(self) -> None:
            self.ingest = ingest  # type: ignore[assignment]

        async def _start_telegram(self) -> None:
            return None

        async def _start_health_monitor(self) -> None:
            return None

        async def _record_event(self, **_kwargs: object) -> None:
            return None

        async def _audit(self, *_args: object, **_kwargs: object) -> None:
            return None

    app = _App(make_settings(upkeep=_settings(**upkeep)))
    app.db = _FakeDb()  # type: ignore[assignment]
    app.cache = _FakeCache()  # type: ignore[assignment]
    return app


class TestKabelKeAppStartup:
    """The wire itself. Cut it and these go red - that is their only job.

    This repo has shipped, more than once, a component that was written,
    exported, unit-tested and never reached by a live process. Testing
    ``UpkeepLoop`` in isolation would reproduce that mistake exactly.
    """

    async def test_startup_background_benar_benar_menggerakkan_ingestor(
        self,
    ) -> None:
        """``aruna run`` must really pull candles.

        Driven through the real ``startup()``, so removing ``_start_upkeep()``
        from it - or the ``await self.upkeep.start()`` inside it - fails here.

        **Bagian resolusinya hilang 2026-08-25**, bersama jalur spot: yang dulu
        diperiksa di sini ``resolver.calls``, dan penilainya adalah
        ``SignalService``. Rencana futures dinilai ``FuturesScheduler``, bukan
        loop ini - jadi yang tersisa untuk dibuktikan loop ini benar-benar
        berdetak adalah candle-nya.
        """
        ingestor = _Ingestor(Market.CRYPTO)
        app = _wired_app(
            _Ingest(ingestor),
            _Resolver(_Resolved(resolved=2)),
            tick_sec=0.01,
            # A 1m bar is not due during the first `candle_settle_sec` of any
            # minute, so against the real clock a settle delay would make this
            # assertion fail roughly a quarter of the time. The settle rule
            # itself is pinned separately, deterministically.
            candle_settle_sec=0.0,
        )

        await app.startup(background=True)
        try:
            await _wait_until(
                lambda: bool(ingestor.calls),
                what="the upkeep loop to refresh candles",
            )
            assert app.upkeep is not None
            assert app.upkeep.running
            assert Horizon.M1 in ingestor.intervals_called()
        finally:
            await app.shutdown()

    async def test_startup_background_false_tidak_menjalankan_loop_periodik(self) -> None:
        """SPEC rule A, and a promise ``startup()`` has already broken once.

        The Telegram poller ran under ``background=False`` regardless of the
        docstring, so every short CLI command stole the single getUpdates
        consumer from the live process and announced "ARUNA ONLINE" then
        "Shutting down. No signals until restart." on the way past.
        """
        ingestor = _Ingestor(Market.CRYPTO)
        resolver = _Resolver()
        app = _wired_app(
            _Ingest(ingestor), resolver, tick_sec=0.01, candle_settle_sec=0.0
        )

        await app.startup(background=False)
        try:
            # Long enough for several ticks, had any loop been started.
            await asyncio.sleep(0.1)
            assert app.upkeep is not None, "the object is still built for `aruna upkeep`"
            assert app.upkeep.running is False
            assert ingestor.calls == []
            assert resolver.calls == []
        finally:
            await app.shutdown()

    async def test_perintah_sekali_jalan_tetap_bisa_menjalankan_satu_siklus(self) -> None:
        """``aruna upkeep`` drives one cycle by hand - built, just not started."""
        ingestor = _Ingestor(Market.CRYPTO)
        resolver = _Resolver()
        app = _wired_app(
            _Ingest(ingestor), resolver, tick_sec=0.01, candle_settle_sec=0.0
        )

        await app.startup(background=False)
        try:
            assert app.upkeep is not None
            stats = await app.upkeep.cycle()
            assert stats.cycles == 1
            assert ingestor.calls, "a hand-driven cycle must still refresh candles"
        finally:
            await app.shutdown()

    async def test_shutdown_menghentikan_loop_sebelum_provider_ditutup(self) -> None:
        """A cycle still in flight would otherwise fire into a closed session,
        and the resulting noise reads exactly like a venue outage."""
        ingestor = _Ingestor(Market.CRYPTO)
        ingest = _Ingest(ingestor)
        app = _wired_app(ingest, _Resolver(), tick_sec=0.01, candle_settle_sec=0.0)

        await app.startup(background=True)
        assert app.upkeep is not None
        await app.shutdown()

        assert app.upkeep.running is False
        assert ingest.closed is True

    def test_upkeep_diwiring_setelah_ingestion(self) -> None:
        """Order matters: the refresher is built from ``self.ingest``.

        Moved above ``_start_ingestion`` it would find ``None``, log
        ``upkeep.not_wired`` and return - a silent no-op, which is the exact
        shape of every "never reached" defect this project has shipped.
        """
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication.startup)
        assert "_start_upkeep" in source, "the upkeep loop is not wired into startup()"
        assert source.index("_start_ingestion") < source.index("_start_upkeep")

    async def test_langkah_upkeep_diberi_waktu_lebih_lama_dari_tenggangnya(
        self, monkeypatch
    ) -> None:
        """The shutdown step has to outlast the grace it is waiting on.

        ``UpkeepLoop.stop`` waits up to ``STOP_GRACE_SEC`` so a resolution pass
        is never cut between its writes (SPEC 22 forbids editing what it would
        leave behind). Every other shutdown step is abandoned after
        ``SHUTDOWN_STEP_TIMEOUT_SEC``, and abandoning ``stop()`` cancels the
        loop task underneath it - producing exactly the tear the grace exists
        to prevent, and without even the ``upkeep.stop_forced`` warning. The
        two numbers live in different files and nothing used to make them
        agree; this asserts the call site, not just the constant.
        """
        from aruna import app as app_module

        given: dict[str, float] = {}

        async def _recording(what: str, coro, *, timeout: float = 10.0) -> None:
            given[what] = timeout
            coro.close()  # nothing here should actually run

        monkeypatch.setattr(app_module, "_safe", _recording)

        app = _wired_app(_Ingest(_Ingestor(Market.CRYPTO)), _Resolver(), tick_sec=0.01)
        await app.startup(background=True)
        await app.shutdown()

        assert given["upkeep"] > app_module.STOP_GRACE_SEC
        # The ordinary steps keep the ordinary budget - the point is that upkeep
        # is the exception, not that every timeout was widened.
        assert given["database"] == app_module.SHUTDOWN_STEP_TIMEOUT_SEC

    async def test_startup_yang_gagal_tidak_meninggalkan_loop_hidup(self) -> None:
        """A half-built application must not leave a task calling providers.

        ``_start_upkeep()`` runs before ``_running`` is set, and ``shutdown()``
        returns immediately while ``_running`` is False - so a failure in one of
        the later startup steps used to exit the process with the loop still
        refreshing candles and resolving signals into a database nobody closed.
        """
        ingest = _Ingest(_Ingestor(Market.CRYPTO))
        app = _wired_app(ingest, _Resolver(), tick_sec=0.01)

        async def _explode() -> None:
            raise RuntimeError("health monitor gone")

        app._start_health_monitor = _explode  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await app.run()

        assert app.upkeep is not None
        assert app.upkeep.running is False
        assert ingest.closed is True


# ---------------------------------------------------------------------------
# cadence - the rate-limit rules
# ---------------------------------------------------------------------------


def _simulate(minutes: int, **overrides: object) -> tuple[_Ingestor, list[list[Horizon]]]:
    """Drive the refresher a minute at a time and record what it asked for."""

    async def run() -> tuple[_Ingestor, list[list[Horizon]]]:
        ingestor = _Ingestor(Market.CRYPTO)
        refresher = _refresher(_Ingest(ingestor), candle_settle_sec=0.0, **overrides)
        per_tick: list[list[Horizon]] = []
        for minute in range(minutes):
            before = len(ingestor.calls)
            await refresher.refresh(now=MONDAY + timedelta(minutes=minute))
            per_tick.append([call.interval for call in ingestor.calls[before:]])
        return ingestor, per_tick

    return asyncio.run(run())


class TestCadencePerInterval:
    """1m every minute, 1d once a day - and never everything at once.

    Refreshing all four intervals on every tick would be a request bill four
    times larger for data that has not changed, and the provider would answer
    with a rate limit rather than candles.
    """

    def test_satu_menit_disegarkan_jauh_lebih_sering_daripada_satu_hari(self) -> None:
        ingestor, _ = _simulate(180)
        counts = Counter(ingestor.intervals_called())

        assert counts[Horizon.M1] == 180
        assert counts[Horizon.M15] == 12
        assert counts[Horizon.H1] == 3
        assert counts[Horizon.D1] == 1
        assert counts[Horizon.M1] > counts[Horizon.M15] > counts[Horizon.H1] > counts[Horizon.D1]

    def test_tidak_semua_interval_disegarkan_setiap_tick(self) -> None:
        """The rate-limit property, stated as a bound rather than a vibe."""
        ingestor, per_tick = _simulate(180)
        intervals = 4  # 1m, 15m, 1h, 1d

        every_interval = [tick for tick in per_tick if len(tick) == intervals]
        assert len(every_interval) == 1, "only the very first tick has everything due"

        naive = intervals * len(per_tick)
        assert len(ingestor.calls) == 196
        assert len(ingestor.calls) < naive * 0.3, (
            "refreshing every interval every tick is the rate-limit failure this "
            "schedule exists to prevent"
        )

    def test_tick_di_tengah_menit_hanya_menarik_satu_interval(self) -> None:
        _, per_tick = _simulate(180)
        assert per_tick[7] == [Horizon.M1]
        assert per_tick[16] == [Horizon.M1]
        assert sorted(per_tick[15], key=lambda i: i.duration) == [Horizon.M1, Horizon.M15]

    def test_interval_yang_tidak_jatuh_tempo_tidak_menghasilkan_request_sama_sekali(
        self,
    ) -> None:
        """An idle wake-up must cost nothing at all."""

        async def run() -> None:
            ingestor = _Ingestor(Market.CRYPTO)
            refresher = _refresher(_Ingest(ingestor), candle_settle_sec=0.0)
            first = await refresher.refresh(now=MONDAY)
            assert first.requests == 4
            # Same minute, nothing has closed since.
            second = await refresher.refresh(now=MONDAY + timedelta(seconds=20))
            assert second.requests == 0
            assert second.refreshed == []

        asyncio.run(run())

    async def test_settle_menunda_bar_sampai_venue_memfinalisasinya(self) -> None:
        """Asking at the boundary often returns a bar the venue has not closed,
        which then has to be rewritten on the next pass."""
        refresher = _refresher(_Ingest(_Ingestor()), candle_settle_sec=15.0)
        boundary = MONDAY + timedelta(hours=1)

        assert refresher.is_due(Market.CRYPTO, Horizon.M1, now=boundary) is False
        assert (
            refresher.is_due(
                Market.CRYPTO, Horizon.M1, now=boundary + timedelta(seconds=5)
            )
            is False
        )
        assert (
            refresher.is_due(
                Market.CRYPTO, Horizon.M1, now=boundary + timedelta(seconds=16)
            )
            is True
        )


class TestUkuranTarikan:
    """A routine pass pulls a few bars; only a real gap pulls more."""

    async def test_seri_yang_mutakhir_hanya_menarik_beberapa_bar(self) -> None:
        """500 candles per interval per minute is the rate-limit failure."""
        newest = {(1, "1m"): MONDAY - timedelta(minutes=1)}
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))
        refresher = _refresher(
            _Ingest(ingestor), _Store(newest), candle_settle_sec=0.0
        )

        await refresher.refresh(now=MONDAY)

        assert [call.limit for call in ingestor.calls] == [3]

    def test_seri_yang_tertinggal_meminta_selisihnya_plus_overlap(self) -> None:
        refresher = _refresher(_Ingest(_Ingestor()))
        behind = MONDAY - timedelta(minutes=90)
        assert refresher.bars_needed(behind, Horizon.M1, now=MONDAY) == 92

    def test_seri_kosong_meminta_plafon(self) -> None:
        """"never fetched" and "fetched and empty" cannot be told apart without
        asking, so an empty series asks for the ceiling - once."""
        refresher = _refresher(_Ingest(_Ingestor()), candle_max_bars=500)
        assert refresher.bars_needed(None, Horizon.M1, now=MONDAY) == 500

    def test_seri_yang_lama_ditinggal_tetap_dibatasi_plafon(self) -> None:
        refresher = _refresher(_Ingest(_Ingestor()), candle_max_bars=500)
        ancient = MONDAY - timedelta(days=365)
        assert refresher.bars_needed(ancient, Horizon.M1, now=MONDAY) == 500

    def test_permintaan_tidak_pernah_nol(self) -> None:
        refresher = _refresher(_Ingest(_Ingestor()))
        ahead = MONDAY + timedelta(hours=5)
        assert refresher.bars_needed(ahead, Horizon.H1, now=MONDAY) >= 1

    async def test_gap_hanya_dilaporkan_saat_jendela_cukup_lebar(self) -> None:
        """SPEC 4: gaps are reported, never filled. Re-reporting the same hole
        every minute is how an operator learns to ignore GAP_DETECTED."""
        newest = {(1, "1m"): MONDAY - timedelta(minutes=1)}
        routine = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))
        await _refresher(
            _Ingest(routine), _Store(newest), candle_settle_sec=0.0
        ).refresh(now=MONDAY)
        assert routine.calls[0].detect_gaps is False

        catching_up = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))
        stale = {(1, "1m"): MONDAY - timedelta(hours=5)}
        await _refresher(
            _Ingest(catching_up), _Store(stale), candle_settle_sec=0.0
        ).refresh(now=MONDAY)
        assert catching_up.calls[0].detect_gaps is True

    async def test_pass_rutin_di_steady_state_tidak_pernah_melaporkan_gap(self) -> None:
        """The state the loop is actually in, minute after minute.

        ``is_due`` allows one pass per bar and the previous pass stored
        everything up to the bar that was forming then, so a healthy series is
        always exactly *one* bar behind and asks for ``overlap + 1``. A guard
        written as ``bars > overlap`` therefore never once said no: measured,
        every routine pass asked for 4 bars against an overlap of 3 and turned
        gap detection on, rewriting the same GAP_DETECTED row every minute per
        asset - the precise thing the comment above it says must not happen.
        """
        store = _Store({(1, "1m"): MONDAY - timedelta(minutes=2)})
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))
        refresher = _refresher(_Ingest(ingestor), store, candle_settle_sec=0.0)

        for minute in range(4):
            moment = MONDAY + timedelta(minutes=minute)
            # steady state: the newest *closed* bar trails the forming one
            store.newest[(1, "1m")] = moment - timedelta(minutes=2)
            await refresher.refresh(now=moment)

        assert [call.limit for call in ingestor.calls] == [4, 4, 4, 4]
        assert [call.detect_gaps for call in ingestor.calls] == [False] * 4

    async def test_lubang_yang_sama_tidak_ditanyakan_dua_kali(self) -> None:
        """A catch-up that stays stuck must not re-report on every pass.

        Gap detection only sees the window just fetched, so asking again from a
        window that has not moved can only produce the same answer - and that
        answer is a row in ``provider_events`` (SPEC 4 reports gaps, it does not
        fill them). One question per position of the series.
        """
        store = _Store({(1, "1m"): MONDAY - timedelta(hours=5)})
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))
        refresher = _refresher(_Ingest(ingestor), store, candle_settle_sec=0.0)

        await refresher.refresh(now=MONDAY)
        await refresher.refresh(now=MONDAY + timedelta(minutes=1))

        assert [call.detect_gaps for call in ingestor.calls] == [True, False]

    async def test_tarikan_yang_gagal_tidak_membakar_kesempatan_probe_gap(self) -> None:
        """A blip must not buy the one gap detection this position ever gets.

        The mark used to be written while the pull was being *planned*, and
        nothing rolled it back when the pull then raised. So the sequence that
        matters most - a wide catch-up that fails once and succeeds on the
        retry - ran the successful pull, the one carrying the whole window, with
        ``detect_gaps=False``. The holes inside those five hundred bars were
        never asked about, and a zero that means "not asked" reads exactly like
        a zero that means "nothing missing" (SPEC 4, rule C).

        The three passes below are the whole property: ask, ask again because
        the first ask never happened, then stop asking because the series has
        not moved. Marking on intent gives ``[True, False, False]``; marking on
        an answer gives ``[True, True, False]``.
        """
        store = _Store({(1, "1m"): MONDAY - timedelta(hours=5)})
        ingestor = _Ingestor(
            Market.CRYPTO,
            intervals=(Horizon.M1,),
            fail_with=RuntimeError("network went away"),
        )
        refresher = _refresher(
            _Ingest(ingestor), store, candle_settle_sec=0.0, candle_retry_sec=1.0
        )

        await refresher.refresh(now=MONDAY)
        ingestor.fail_with = None
        await refresher.refresh(now=MONDAY + timedelta(seconds=2))
        # Same position of the series, a bar later: now it really has been asked.
        await refresher.refresh(now=MONDAY + timedelta(seconds=61))

        assert [call.detect_gaps for call in ingestor.calls] == [True, True, False]

    async def test_seri_kosong_yang_gagal_tetap_ditanyakan_lubangnya(self) -> None:
        """The same rule where the mark is ``None``, which is a real value here.

        An asset that has never been fetched has no stored bar, so its mark is
        ``None`` - and ``None`` is also what a plain dictionary lookup returns
        for a symbol that was never probed. Collapsing the two would make the
        first five-hundred-bar backfill of a brand-new asset look already
        probed the moment one attempt failed.
        """
        ingestor = _Ingestor(
            Market.CRYPTO,
            intervals=(Horizon.M1,),
            fail_with=RuntimeError("429 Too Many Requests"),
        )
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=1.0
        )

        await refresher.refresh(now=MONDAY)
        ingestor.fail_with = None
        await refresher.refresh(now=MONDAY + timedelta(seconds=2))

        assert [call.limit for call in ingestor.calls] == [500, 500]
        assert [call.detect_gaps for call in ingestor.calls] == [True, True]

    async def test_satu_aset_tanpa_candle_tidak_menyeret_yang_lain_ke_plafon(
        self,
    ) -> None:
        """Measured: one empty symbol took every asset's three-bar pass to 500.

        ``max()`` across the market meant the payload for *everyone* was sized
        by the worst-off asset - the same request count carrying 167 times the
        rows, on every pass, for ever. The bar count is per asset now, and the
        request count is unchanged because ``backfill`` charges one request per
        asset either way.
        """
        newest = {
            (1, "1m"): MONDAY - timedelta(minutes=2),
            (2, "1m"): MONDAY - timedelta(minutes=2),
        }
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,), assets=3)
        result = await _refresher(
            _Ingest(ingestor), _Store(newest), candle_settle_sec=0.0
        ).refresh(now=MONDAY)

        asked = {call.symbols: call.limit for call in ingestor.calls}
        assert asked == {("SYM1", "SYM2"): 4, ("SYM3",): 500}
        assert result.requests == 3, "one request per asset, however it is split"

    async def test_lubang_lebih_dalam_dari_plafon_dikatakan_sekali(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A hole past the ceiling is a reported shortfall, not a silent one.

        The provider builds its window from *now*, so a catch-up clamped at
        ``candle_max_bars`` jumps over the older part of the hole: the newest
        stored bar leaps forward, the next pass looks up to date, and nothing
        anywhere mentions the stretch that was skipped. A zero that means "not
        asked" must never read as a zero that means "nothing missing".
        """
        spy = _LogSpy()
        monkeypatch.setattr("aruna.upkeep.candles.log", spy)
        store = _Store({(1, "1m"): MONDAY - timedelta(hours=31)})
        refresher = _refresher(
            _Ingest(_Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))),
            store,
            candle_settle_sec=0.0,
        )

        await refresher.refresh(now=MONDAY)
        capped = spy.events("upkeep.catchup_capped")
        assert len(capped) == 1
        assert capped[0]["bars_wanted"] > capped[0]["bars_pulled"] == 500
        assert capped[0]["symbol"] == "SYM1"

        # Same position, same answer: said once, not once a minute.
        await refresher.refresh(now=MONDAY + timedelta(minutes=1))
        assert len(spy.events("upkeep.catchup_capped")) == 1

    async def test_penyegaran_rutin_tidak_membanjiri_log(self) -> None:
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,))
        await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(now=MONDAY)
        assert ingestor.calls[0].quiet is True


class TestBatasAwalBar:
    def test_bar_dijajarkan_ke_epoch_bukan_ke_hari(self) -> None:
        moment = datetime(2026, 8, 17, 12, 7, 33, tzinfo=UTC)
        assert bar_start(moment, Horizon.M15) == datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
        assert bar_start(moment, Horizon.H1) == datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
        assert bar_start(moment, Horizon.D1) == datetime(2026, 8, 17, 0, 0, tzinfo=UTC)

    def test_batas_bar_stabil_di_dalam_bar_yang_sama(self) -> None:
        start = bar_start(MONDAY + timedelta(minutes=7), Horizon.M15)
        assert bar_start(MONDAY + timedelta(minutes=14, seconds=59), Horizon.M15) == start


# ---------------------------------------------------------------------------
# which intervals - derived, never typed out
# ---------------------------------------------------------------------------


class TestHimpunanIntervalDiturunkan:
    """A hand-written list is free to drift from what sampling actually reads,
    and the symptom of that drift is not an error: it is predictions that
    quietly cannot be scored."""

    def test_crypto_dan_idx_punya_himpunan_yang_berbeda(self) -> None:
        every = tuple(Horizon)
        assert refresh_intervals(Market.CRYPTO, every) == (
            Horizon.M1,
            Horizon.M15,
            Horizon.H1,
            Horizon.D1,
        )
        assert refresh_intervals(Market.IDX, every) == (
            Horizon.M15,
            Horizon.H1,
            Horizon.D1,
        )

    def test_interval_di_luar_himpunan_tidak_disegarkan(self) -> None:
        """5m, 30m and 4h sit in the database from manual fetches and none of
        them is refreshed."""
        derived = refresh_intervals(Market.CRYPTO, tuple(Horizon))
        for unused in (Horizon.M5, Horizon.M30, Horizon.H4, Horizon.D3, Horizon.W1):
            assert unused not in derived

    def test_dua_sebab_berbeda_menjatuhkan_4h_dan_5m(self) -> None:
        """They are absent for different reasons, and the difference matters.

        4h is never reached by sampling within the fallback depth. 5m and 30m
        *are* reached - they are the second candidate for their own horizons -
        and are dropped by the ``STORED_INTERVALS`` filter, which means a 5m or
        30m prediction rests entirely on 1m bars. A reader who believes the
        single "sampling never reaches them" story concludes those horizons are
        covered when they are one series away from unscoreable.
        """
        for reached in (Horizon.M5, Horizon.M30):
            assert reached in sampling_intervals(reached)[:SAMPLING_FALLBACK_DEPTH]
            assert reached not in STORED_INTERVALS

        assert Horizon.H4 not in sampling_intervals(Horizon.H4)[:SAMPLING_FALLBACK_DEPTH]

    def test_setiap_interval_yang_disegarkan_memang_tersimpan(self) -> None:
        for market in (Market.CRYPTO, Market.IDX):
            for interval in refresh_intervals(market, tuple(Horizon)):
                assert interval in STORED_INTERVALS

    def test_pilihan_sampling_pertama_setiap_horizon_ikut_disegarkan(self) -> None:
        """The drift guard. If ``STORED_INTERVALS`` or ``sampling_intervals()``
        changes, this fails instead of the resolution quietly starving."""
        for market in (Market.CRYPTO, Market.IDX):
            derived = refresh_intervals(market, tuple(Horizon))
            for horizon in horizons_for_market(market):
                first = sampling_intervals(horizon)[0]
                if first in STORED_INTERVALS:
                    assert first in derived, f"{market.value} {horizon.value}"

    def test_interval_yang_tidak_disediakan_provider_tidak_diminta(self) -> None:
        assert refresh_intervals(Market.CRYPTO, (Horizon.H1, Horizon.D1)) == (
            Horizon.H1,
            Horizon.D1,
        )

    def test_override_operator_dipotong_ke_kemampuan_provider(self) -> None:
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M15, Horizon.D1))
        refresher = _refresher(_Ingest(ingestor), candle_intervals="15m,4h")
        assert refresher.intervals_for(Market.CRYPTO) == (Horizon.M15,)

    def test_himpunan_diselesaikan_sekali_lalu_diingat(self) -> None:
        refresher = _refresher(_Ingest(_Ingestor(Market.CRYPTO)))
        assert refresher.intervals_for(Market.CRYPTO) is refresher.intervals_for(
            Market.CRYPTO
        )


# ---------------------------------------------------------------------------
# failure handling inside one refresh pass
# ---------------------------------------------------------------------------


class TestKegagalanRefresh:
    async def test_satu_interval_gagal_tidak_menghentikan_sisanya(self) -> None:
        ingestor = _Ingestor(Market.CRYPTO, fail_with=RuntimeError("network went away"))
        result = await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(
            now=MONDAY
        )
        assert len(ingestor.calls) == 4, "every due interval was still attempted"
        assert len(result.failures) == 4
        assert result.refreshed == []

    async def test_satu_market_gagal_tidak_menghentikan_market_lain(self) -> None:
        broken = _Ingestor(Market.CRYPTO, fail_with=RuntimeError("boom"))
        healthy = _Ingestor(Market.IDX)
        result = await _refresher(
            _Ingest(broken, healthy), candle_settle_sec=0.0
        ).refresh(now=IDX_OPEN)
        assert any(market is Market.IDX for market, _ in result.refreshed)

    async def test_kegagalan_tidak_memajukan_jadwal(self) -> None:
        """A failed pull must stay due, otherwise the interval is parked for a
        whole bar because of one network blip.

        The *first* retry is still exactly ``candle_retry_sec`` later, which is
        the promise this rule was written for. What happens to the second,
        third and thousandth retry is a separate property, pinned below.
        """
        ingestor = _Ingestor(
            Market.CRYPTO, intervals=(Horizon.D1,), fail_with=RuntimeError("boom")
        )
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=60.0
        )

        await refresher.refresh(now=MONDAY)
        assert refresher.is_due(Market.CRYPTO, Horizon.D1, now=MONDAY) is False, (
            "inside the retry window it backs off"
        )
        later = MONDAY + timedelta(seconds=61)
        assert refresher.is_due(Market.CRYPTO, Horizon.D1, now=later) is True, (
            "a failed 1d pull must not wait twenty-four hours for its next chance"
        )

    async def test_retry_melebar_saat_kegagalan_berulang(self) -> None:
        """One blip is not a broken venue, and the spacing has to tell them
        apart. A flat 60s retry is the same answer to both."""
        ingestor = _Ingestor(
            Market.CRYPTO, intervals=(Horizon.H1,), fail_with=RuntimeError("boom")
        )
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=60.0
        )

        await refresher.refresh(now=MONDAY)
        assert refresher.retry_delay(Market.CRYPTO, Horizon.H1) == 60.0

        second = MONDAY + timedelta(seconds=61)
        await refresher.refresh(now=second)
        assert refresher.retry_delay(Market.CRYPTO, Horizon.H1) == 120.0
        assert (
            refresher.is_due(
                Market.CRYPTO, Horizon.H1, now=second + timedelta(seconds=61)
            )
            is False
        ), "the second retry is no longer sixty seconds after the first"
        assert (
            refresher.is_due(
                Market.CRYPTO, Horizon.H1, now=second + timedelta(seconds=121)
            )
            is True
        )

    def test_jeda_retry_interval_yang_belum_pernah_gagal(self) -> None:
        """The clean-state answer, which used to come from an unreachable branch.

        ``retry_delay`` opened with ``if state.consecutive_failures <= 0: return
        candle_retry_sec``, and its only production caller is ``is_due``, whose
        ``and`` chain short-circuits unless that counter is already above zero -
        so the branch could only ever be taken for a negative value, which
        nothing writes. Coverage said so: the line was never executed by any
        test in the repository. The arithmetic answers the clean case on its
        own now, and this pins the answer it gives.
        """
        refresher = _refresher(_Ingest(_Ingestor(Market.CRYPTO)), candle_retry_sec=60.0)
        assert refresher.retry_delay(Market.CRYPTO, Horizon.H1) == 60.0
        assert refresher.retry_delay(Market.CRYPTO, Horizon.M1) == 60.0

    async def test_retry_tidak_pernah_melebar_melewati_bar_nya_sendiri(self) -> None:
        """Waiting longer than one bar buys nothing: the bar makes it due anyway."""
        ingestor = _Ingestor(
            Market.CRYPTO, intervals=(Horizon.M1,), fail_with=RuntimeError("boom")
        )
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=60.0
        )

        moment = MONDAY
        for _ in range(6):
            await refresher.refresh(now=moment)
            moment += timedelta(seconds=61)

        assert refresher.retry_delay(Market.CRYPTO, Horizon.M1) == 60.0

    async def test_venue_mati_seharian_tidak_menembak_1d_tiap_menit(self) -> None:
        """The measured cost of a flat retry, stated as a bound.

        A symbol that fails for ever kept every interval - 1d included - at the
        60s retry floor: 1,440 daily-bar pulls a day where the cadence asks for
        one, measured at 28,800 requests per 24h against 7,810 for the same
        market healthy. The spacing widens to the interval's own length instead.
        """
        ingestor = _Ingestor(
            Market.CRYPTO,
            intervals=(Horizon.D1,),
            fail_with=RuntimeError("venue gone"),
        )
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=60.0
        )

        moment = MONDAY
        while moment < MONDAY + timedelta(days=1):
            await refresher.refresh(now=moment)
            moment += timedelta(seconds=15)

        assert len(ingestor.calls) == 11, "1,440 attempts is what this replaces"
        delay = refresher.retry_delay(Market.CRYPTO, Horizon.D1)
        assert 3600.0 <= delay <= Horizon.D1.duration.total_seconds(), (
            "wide enough to stop hammering, never wider than the bar itself"
        )

    async def test_satu_simbol_rusak_tidak_menyeret_cadence_seluruh_market(self) -> None:
        """``backfill`` collects failures per *asset* into one result.

        Treating that result as a failed ``(market, interval)`` pair meant one
        delisted pair out of five took 15m, 1h and 1d off their bar cadence and
        onto the retry floor - measured at 3.7x the daily request bill, with 1d
        pulled 7,200 times instead of ten. The dead symbol is still reported;
        it just no longer decides the schedule for the healthy ones.
        """
        ingestor = _Ingestor(Market.CRYPTO, assets=3, fail_symbols=("SYM2",))
        newest = {
            (asset, interval.value): MONDAY - interval.duration
            for asset in (1, 2, 3)
            for interval in (Horizon.M1, Horizon.M15, Horizon.H1, Horizon.D1)
        }
        refresher = _refresher(
            _Ingest(ingestor), _Store(newest), candle_settle_sec=0.0
        )

        for tick in range(240):  # one hour of 15s ticks
            await refresher.refresh(now=MONDAY + timedelta(seconds=15 * tick))

        counts = Counter(call.interval for call in ingestor.calls)
        assert counts[Horizon.M1] == 60
        assert counts[Horizon.M15] == 4
        assert counts[Horizon.H1] == 1
        assert counts[Horizon.D1] == 1

    async def test_simbol_rusak_tetap_dilaporkan_setiap_bar(self) -> None:
        """Keeping the cadence must not turn into hiding the failure."""
        ingestor = _Ingestor(
            Market.CRYPTO, intervals=(Horizon.M1,), assets=3, fail_symbols=("SYM2",)
        )
        result = await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(
            now=MONDAY
        )
        assert result.refreshed == [(Market.CRYPTO, Horizon.M1)]
        assert any("SYM2" in failure for failure in result.failures)

    async def test_kegagalan_membaca_candle_tersimpan_tidak_mengakhiri_pass(
        self,
    ) -> None:
        """A database blip on one market must not end the pass for the other."""
        broken = _Ingestor(Market.CRYPTO)
        result = await _refresher(
            _Ingest(broken),
            _Store(error=RuntimeError("mysql gone")),
            candle_settle_sec=0.0,
        ).refresh(now=MONDAY)

        assert broken.calls == []
        assert result.failures and "mysql gone" in result.failures[0]

    async def test_percobaan_ulang_berhasil_mengunci_jadwal_lagi(self) -> None:
        ingestor = _Ingestor(
            Market.CRYPTO, intervals=(Horizon.M1,), fail_with=RuntimeError("boom")
        )
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=5.0
        )
        await refresher.refresh(now=MONDAY)

        ingestor.fail_with = None
        result = await refresher.refresh(now=MONDAY + timedelta(seconds=6))
        assert result.refreshed == [(Market.CRYPTO, Horizon.M1)]
        assert (
            refresher.is_due(
                Market.CRYPTO, Horizon.M1, now=MONDAY + timedelta(seconds=7)
            )
            is False
        )

    async def test_hasil_dengan_failures_dihitung_gagal_bukan_sukses(self) -> None:
        """``backfill`` reports a dead symbol in its result rather than raising;
        counting that as success would park the interval for a whole bar."""
        ingestor = _Ingestor(Market.CRYPTO, intervals=(Horizon.M1,), fail_with="result")
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, candle_retry_sec=1.0
        )
        result = await refresher.refresh(now=MONDAY)

        assert result.refreshed == []
        assert result.failures
        assert (
            refresher.is_due(
                Market.CRYPTO, Horizon.M1, now=MONDAY + timedelta(seconds=2)
            )
            is True
        )

    async def test_market_tanpa_aset_dilaporkan_bukan_didiamkan(self) -> None:
        """And in Indonesian: this string is printed straight at the operator by
        ``aruna upkeep``, next to health lines that already say it that way."""
        ingestor = _Ingestor(Market.CRYPTO, assets=0)
        result = await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(
            now=MONDAY
        )
        assert result.failures
        assert "tidak ada aset aktif" in result.failures[0]
        assert "aruna seed" in result.failures[0]
        assert find_forbidden(result.failures[0]) == ()


class TestBatasRequestPerSiklus:
    async def test_pekerjaan_di_atas_plafon_ditunda_bukan_dibuang(self) -> None:
        ingestor = _Ingestor(Market.CRYPTO, assets=1)
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, max_requests_per_cycle=2
        )

        first = await refresher.refresh(now=MONDAY)
        assert len(first.refreshed) == 2
        assert len(first.deferred) == 2
        assert len(ingestor.calls) == 2

        # The deferred intervals kept their place in the schedule, so the very
        # next tick picks them up rather than losing them for a bar.
        second = await refresher.refresh(now=MONDAY + timedelta(seconds=1))
        assert sorted(i.value for _, i in second.refreshed) == sorted(
            i.value for _, i in first.deferred
        )

    async def test_biaya_dihitung_per_aset(self) -> None:
        """``backfill`` iterates the assets itself: one call per interval costs
        one request per asset, and the ceiling has to know that."""
        ingestor = _Ingestor(Market.CRYPTO, assets=4)
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, max_requests_per_cycle=4
        )
        result = await refresher.refresh(now=MONDAY)
        assert result.requests == 4
        assert len(result.refreshed) == 1

    async def test_aset_lebih_banyak_dari_plafon_tetap_membuat_kemajuan(self) -> None:
        """Measured starvation: 41 assets against the default ceiling of 40
        refreshed **nothing** in 120 ticks, for ever, while the loop went on
        counting healthy cycles and health went on reporting UP.

        "Deferred, not dropped" is only true if something eventually goes
        through, so the first interval of a cycle always does; the ceiling still
        shapes everything after it. ``aruna seed`` raising the IDX universe to
        45 emiten is all it took to reach this.
        """
        ingestor = _Ingestor(Market.CRYPTO, assets=41)
        refresher = _refresher(
            _Ingest(ingestor), candle_settle_sec=0.0, max_requests_per_cycle=40
        )

        refreshed: list[Horizon] = []
        for tick in range(8):
            result = await refresher.refresh(now=MONDAY + timedelta(seconds=15 * tick))
            refreshed.extend(interval for _, interval in result.refreshed)
            assert result.refreshed or not result.deferred, (
                "a cycle that defers work must also do some"
            )

        assert set(refreshed) == {Horizon.M1, Horizon.M15, Horizon.H1, Horizon.D1}

    async def test_market_kedua_tidak_kelaparan_karena_market_pertama(self) -> None:
        """The progress guarantee is per *market*, and it has to be.

        Counted against the cycle-wide total, the free slot belongs to whichever
        market ``IngestService.markets`` happens to yield first, and the second
        market never gets one on any tick where the first spent anything. Six
        simulated hours at CRYPTO 45 assets / IDX 5 against the default ceiling
        of 40, 60s tick landing mid-minute, measured three ways:

            no guarantee at all   CRYPTO   0   IDX 24
            guarantee per cycle   CRYPTO 360   IDX  0
            guarantee per market  CRYPTO 360   IDX 24

        The middle row is the regression: the guarantee cured CRYPTO's
        starvation by *moving* it onto IDX, taking away refreshes the unguarded
        code still performed. A fix that removes work the old code did is not a
        fix, so the guarantee was narrowed to the market rather than widened
        away - the bottom row keeps both.

        The 60s step matters: at the default 15s tick CRYPTO's 1m is due on one
        tick in four, so IDX always finds a tick with nothing spent and the
        starvation is latent rather than absent.
        """
        crypto = _Ingestor(Market.CRYPTO, assets=45)
        idx = _Ingestor(Market.IDX, assets=5)
        refresher = _refresher(
            _Ingest(crypto, idx), candle_settle_sec=0.0, max_requests_per_cycle=40
        )

        served: list[tuple[Market, Horizon]] = []
        for tick in range(6):
            result = await refresher.refresh(now=IDX_OPEN + timedelta(seconds=60 * tick))
            served.extend(result.refreshed)
            assert sum(1 for market, _ in result.refreshed if market is Market.CRYPTO) == 1, (
                "the ceiling still shapes everything after each market's first "
                "interval"
            )

        assert {interval for market, interval in served if market is Market.IDX} == {
            Horizon.M15,
            Horizon.H1,
            Horizon.D1,
        }, "the market iterated second is starved by a cycle-wide free slot"

    async def test_interval_yang_kelaparan_diteriakkan_bukan_didiamkan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """"Deferred" sounds temporary. Said twice in a row it is not, and the
        operator needs the number that makes it permanent."""
        spy = _LogSpy()
        monkeypatch.setattr("aruna.upkeep.candles.log", spy)
        refresher = _refresher(
            _Ingest(_Ingestor(Market.CRYPTO, assets=41)),
            candle_settle_sec=0.0,
            max_requests_per_cycle=40,
        )

        for tick in range(4):
            await refresher.refresh(now=MONDAY + timedelta(seconds=15 * tick))

        starved = spy.events("upkeep.interval_starved")
        assert starved, "an interval deferred on every cycle must say so"
        assert starved[0]["cost"] == 41
        assert starved[0]["ceiling"] == 40


class TestGerbangBursaIdx:
    """IDX trades roughly six hours a day; polling a shut venue re-records the
    same close and burns rate limit doing it (SPEC 3)."""

    async def test_bursa_tutup_tidak_menghabiskan_request(self) -> None:
        ingestor = _Ingestor(Market.IDX)
        result = await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(
            now=IDX_WEEKEND
        )
        assert ingestor.calls == []
        assert result.skipped_closed == [Market.IDX]

    async def test_bursa_buka_disegarkan(self) -> None:
        ingestor = _Ingestor(Market.IDX)
        await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(now=IDX_OPEN)
        assert ingestor.calls

    async def test_crypto_tidak_pernah_digerbang(self) -> None:
        """Crypto does not close, and a Sunday must not stop it."""
        ingestor = _Ingestor(Market.CRYPTO)
        await _refresher(_Ingest(ingestor), candle_settle_sec=0.0).refresh(
            now=IDX_WEEKEND
        )
        assert ingestor.calls

    async def test_sapuan_settlement_benar_benar_menyimpan_bar_penutup_sesi(
        self,
    ) -> None:
        """The whole point of the sweep, asserted as candles and not as a gate.

        Measured before this test existed: the sweep fired at exactly 16:15:00
        WIB, refreshed **one** interval, and marked the day done. 16:15 WIB is
        the 15m boundary itself, so the session's last 15m bar had not settled
        yet; a tick later the flag was already spent. The 1d bar could not be
        reached at all, because IDX daily bars open at 02:00 UTC and a
        midnight-UTC boundary counted the day's own bar as already refreshed.

        So: the pre-closing auction bar (15:50-16:00 WIB) and the closing 1h
        bar waited for the next morning, and an IDX prediction resolving
        overnight lost its final observation - which SPEC 22 then forbids
        anyone to correct.

        Two intervals, not three. The 1d leg was measured and it stored nothing
        a reader could read; see
        ``test_bar_harian_idx_datang_dari_sesi_bukan_dari_sapuan``.
        """
        ingestor = _Ingestor(Market.IDX, assets=2)
        refresher = _refresher(_Ingest(ingestor))

        # A normal session pass first, so every interval carries the
        # last_success_at it would carry in production by 16:15.
        await refresher.refresh(now=IDX_OPEN)
        during_session = len(ingestor.calls)

        swept: list[Horizon] = []
        moment = IDX_SETTLEMENT - timedelta(seconds=15)
        while moment <= IDX_SETTLEMENT + timedelta(minutes=30):
            result = await refresher.refresh(now=moment)
            swept.extend(interval for _, interval in result.refreshed)
            moment += timedelta(seconds=15)

        assert sorted(interval.value for interval in swept) == ["15m", "1h"]
        assert len(ingestor.calls) > during_session, "the sweep issued real pulls"

    async def test_sapuan_berhenti_begitu_barnya_tersimpan(self) -> None:
        """The other half: a gate held open all evening polls a shut venue."""
        ingestor = _Ingestor(Market.IDX, assets=2)
        refresher = _refresher(_Ingest(ingestor))

        await refresher.refresh(now=IDX_OPEN)
        moment = IDX_SETTLEMENT
        while moment <= IDX_SETTLEMENT + timedelta(minutes=5):
            await refresher.refresh(now=moment)
            moment += timedelta(seconds=15)
        settled = len(ingestor.calls)

        evening = await refresher.refresh(now=IDX_SETTLEMENT + timedelta(hours=3))
        assert evening.skipped_closed == [Market.IDX]
        assert len(ingestor.calls) == settled, "nothing is polled after the sweep"

    def test_gerbang_sapuan_tidak_terbakar_hanya_karena_ditanya(self) -> None:
        """Asking is not doing.

        ``market_gate`` used to record the day as swept the moment it was
        called - before ``assets()``, before the newest-bar query, before the
        request ceiling had its say. Any of those failing lost the day's
        settlement bars entirely, and the one that fired every day was the
        settle delay: nothing was due yet at 16:15:00.
        """
        refresher = _refresher(_Ingest(_Ingestor(Market.IDX)))

        first = refresher.market_gate(Market.IDX, now=IDX_SETTLEMENT)
        second = refresher.market_gate(Market.IDX, now=IDX_AFTER_CLOSE)

        assert first.sweep_day == date(2026, 8, 17)
        assert second.sweep_day == first.sweep_day
        assert second.intervals == first.intervals

    async def test_sapuan_bertahan_melewati_tick_yang_gagal(self) -> None:
        """One MySQL blip at 16:15 must not cost the day its closing bars."""
        store = _Store(error=RuntimeError("mysql gone"))
        ingestor = _Ingestor(Market.IDX, assets=1)
        refresher = _refresher(_Ingest(ingestor), store)

        failed = await refresher.refresh(now=IDX_SETTLEMENT + timedelta(seconds=15))
        assert failed.failures and failed.refreshed == []

        store.error = None
        recovered = await refresher.refresh(now=IDX_SETTLEMENT + timedelta(minutes=1))
        assert sorted(i.value for _, i in recovered.refreshed) == ["15m", "1h"]

    def test_bar_harian_idx_dijajarkan_ke_pembukaan_bursa(self) -> None:
        """Every one of the 5,500 stored IDX 1d rows opens at 02:00 UTC.

        Crypto's daily bar starts at midnight UTC and the arithmetic is right
        for it; IDX's starts at the opening auction, 09:00 WIB.
        """
        assert bar_start(IDX_SETTLEMENT, Horizon.D1, market=Market.IDX) == datetime(
            2026, 8, 17, 2, 0, tzinfo=UTC
        )
        assert bar_start(IDX_SETTLEMENT, Horizon.D1) == datetime(
            2026, 8, 17, 0, 0, tzinfo=UTC
        )
        pre_open = datetime(2026, 8, 17, 1, 45, tzinfo=UTC)  # 08:45 WIB
        assert bar_start(pre_open, Horizon.D1, market=Market.IDX) == datetime(
            2026, 8, 16, 2, 0, tzinfo=UTC
        )

    def test_bar_harian_idx_jatuh_tempo_saat_bar_kemarin_benar_benar_tutup(self) -> None:
        """A day-anchored 1d schedule asks for yesterday's bar before it closes.

        Yesterday's IDX daily bar runs 02:00 UTC to 02:00 UTC, so at 07:30 WIB
        it is not closed yet and pulling it stores a bar that has to be
        rewritten. At 09:01 WIB it is final and worth asking for.
        """
        refresher = _refresher(_Ingest(_Ingestor(Market.IDX)))
        state = refresher._state(Market.IDX, Horizon.D1)
        state.last_success_at = IDX_SETTLEMENT  # stored by Monday's sweep

        assert (
            refresher.is_due(
                Market.IDX, Horizon.D1, now=datetime(2026, 8, 18, 0, 30, tzinfo=UTC)
            )
            is False
        )
        assert (
            refresher.is_due(
                Market.IDX, Horizon.D1, now=datetime(2026, 8, 18, 2, 1, tzinfo=UTC)
            )
            is True
        )

    def test_sapuan_tidak_terjadi_di_hari_libur(self) -> None:
        refresher = _refresher(_Ingest(_Ingestor(Market.IDX)))
        sunday_evening = IDX_WEEKEND + timedelta(hours=5)
        assert refresher.market_gate(Market.IDX, now=sunday_evening).intervals == ()

    async def test_bar_harian_idx_datang_dari_sesi_bukan_dari_sapuan(self) -> None:
        """The sweep's 1d leg was one provider call a day for a row nobody reads.

        Measured over two simulated trading days, with and without the sweep:
        the 15m and 1h closing bars are stored hours earlier because of it, and
        the 1d bar is stored at *exactly* the same moment either way. The reason
        is arithmetic, not luck. The IDX adapter stamps ``close_time =
        open_time + interval.duration`` and ``is_closed = close_time <=
        received`` (``data/idx/yahoo.py``), and IDX daily bars open at the
        opening auction - 02:00 UTC, every one of the 5,500 stored rows. So the
        bar pulled at 16:15 WIB lands ``is_closed = 0``, and every reader on the
        resolution path asks ``closed_only=True``: resolution cannot see it,
        the scheduler cannot see it, and the next morning's ordinary pass -
        which runs with or without a sweep - is what finally stores it closed.

        Overnight 1d predictions are not left worse off: ``sampling_intervals``
        reaches for 1h and 15m before 1d, and those two the sweep really does
        close. What is gone is the cost (rule D) and a docstring that promised
        a bar the code could not deliver.
        """
        ingestor = _Ingestor(Market.IDX, assets=1)
        refresher = _refresher(_Ingest(ingestor))

        session = await refresher.refresh(now=IDX_OPEN)
        assert (Market.IDX, Horizon.D1) in session.refreshed, (
            "the daily bar is refreshed on the ordinary session cadence"
        )

        gate = refresher.market_gate(Market.IDX, now=IDX_SETTLEMENT)
        assert gate.sweep_day == date(2026, 8, 17)
        assert sorted(interval.value for interval in gate.intervals) == ["15m", "1h"]

    async def test_sapuan_yang_sebagian_gagal_tetap_terutang_hari_itu(self) -> None:
        """One symbol failing at 16:15 must not spend the day's only chance.

        ``_refresh_one`` treats a pass as successful once *one* asset answers,
        which is right for routine refreshing - a delisted pair must not drag a
        whole market to the retry floor. The sweep is different in kind: it is
        the single pass per trading day that can store a final bar, and marking
        it settled on a partial answer means the symbols that failed lose that
        bar until the next session, even though the window stays open until WIB
        midnight for exactly this reason.

        The retry is spaced, not per tick. Waiving the once-per-bar rule without
        spacing would turn an eight-hour window into one pull per tick - 119
        pulls in the thirty minutes below instead of the eleven the widening
        backoff allows.
        """
        ingestor = _Ingestor(Market.IDX, assets=2, fail_symbols=("SYM2",))
        refresher = _refresher(_Ingest(ingestor), candle_retry_sec=1.0)

        await refresher.refresh(now=IDX_OPEN)
        during_session = len(ingestor.calls)

        moment = IDX_SETTLEMENT
        ticks = 0
        while moment <= IDX_SETTLEMENT + timedelta(minutes=30):
            await refresher.refresh(now=moment)
            moment += timedelta(seconds=15)
            ticks += 1

        gate = refresher.market_gate(Market.IDX, now=IDX_SETTLEMENT + timedelta(hours=2))
        assert gate.sweep_day == date(2026, 8, 17), (
            "the day is still owed while a symbol is missing its closing bar"
        )
        assert sorted(interval.value for interval in gate.intervals) == ["15m", "1h"]

        tried = Counter(call.interval for call in ingestor.calls[during_session:])
        assert tried[Horizon.M15] >= 2, "the failing symbol got another chance"
        assert tried[Horizon.M15] < ticks // 4, (
            "and the retries are spaced - a waived cadence is not a licence to "
            "pull on every tick for eight hours"
        )

    async def test_sapuan_yang_penuh_menutup_gerbangnya(self) -> None:
        """The other half of the rule above: a complete sweep is done, once.

        Without this the "still owed" branch would be free to keep the gate open
        on a healthy market all evening, which is the rate-limit failure the
        gate exists to prevent.
        """
        ingestor = _Ingestor(Market.IDX, assets=2)
        refresher = _refresher(_Ingest(ingestor), candle_retry_sec=1.0)

        await refresher.refresh(now=IDX_OPEN)
        moment = IDX_SETTLEMENT
        while moment <= IDX_SETTLEMENT + timedelta(minutes=5):
            await refresher.refresh(now=moment)
            moment += timedelta(seconds=15)

        gate = refresher.market_gate(Market.IDX, now=IDX_SETTLEMENT + timedelta(hours=2))
        assert gate.intervals == ()


# ---------------------------------------------------------------------------
# one cycle: refresh, then resolve
# ---------------------------------------------------------------------------


def _loop(
    refresher: object, resolver: object, **overrides: object
) -> UpkeepLoop:
    return UpkeepLoop(
        refresher=refresher,  # type: ignore[arg-type]
        resolver=resolver,
        settings=_settings(**overrides),
        stats=UpkeepStats(started_at=MONDAY),
    )


class TestUrutanSatuSiklus:
    """SPEC 22: a scored prediction may not be edited afterwards.

    Resolution reads candles, takes the last observation it can find as final,
    and that number becomes the recorded outcome and the paper trade's exit
    price. Scoring against a series that has not caught up records a mid-horizon
    price as a final result, permanently.
    """

    async def test_candle_disegarkan_sebelum_resolusi(self) -> None:
        order: list[str] = []
        loop = _loop(_RecordingRefresher(order), _Resolver(log=order))
        await loop.cycle(now=MONDAY)
        assert order == ["refresh", "resolve"]

    async def test_urutan_bertahan_meski_refresh_gagal(self) -> None:
        """A venue that is down must not cancel scoring of predictions whose
        candles are already stored."""
        order: list[str] = []
        refresher = _RecordingRefresher(order, error=RuntimeError("venue down"))
        resolver = _Resolver(log=order)
        loop = _loop(refresher, resolver)

        stats = await loop.cycle(now=MONDAY)

        assert order == ["refresh", "resolve"]
        assert resolver.calls, "resolution still ran"
        assert stats.refresh_failures == 1
        assert stats.cycles == 1

    async def test_resolusi_gagal_tidak_membatalkan_siklus(self) -> None:
        refresher = _RecordingRefresher()
        loop = _loop(refresher, _Resolver(error=RuntimeError("db gone")))
        stats = await loop.cycle(now=MONDAY)
        assert stats.resolve_failures == 1
        assert stats.cycles == 1
        assert refresher.calls == [MONDAY]

    async def test_resolver_menerima_waktu_siklus_dan_batasnya(self) -> None:
        resolver = _Resolver()
        loop = _loop(_RecordingRefresher(), resolver, resolve_limit=100)
        await loop.cycle(now=MONDAY)
        assert resolver.calls == [(MONDAY, 100)]

    async def test_resolusi_punya_cadence_sendiri(self) -> None:
        """Refresh is cheap and local; resolution is a database sweep. Running
        it on every 15s tick would be four times the work for the same answer."""
        refresher = _RecordingRefresher()
        resolver = _Resolver()
        loop = _loop(refresher, resolver, resolve_interval_sec=60.0)

        for tick in range(5):
            await loop.cycle(now=MONDAY + timedelta(seconds=15 * tick))

        assert len(refresher.calls) == 5
        assert len(resolver.calls) == 2

    async def test_resolusi_bisa_dimatikan_tanpa_mematikan_refresh(self) -> None:
        refresher = _RecordingRefresher()
        resolver = _Resolver()
        loop = _loop(refresher, resolver, resolve_enabled=False)
        await loop.cycle(now=MONDAY)
        assert refresher.calls == [MONDAY]
        assert resolver.calls == []

    async def test_pass_resolusi_yang_gagal_tetap_pada_cadence_nya(self) -> None:
        """Stamped on the attempt, not on success: a resolution that keeps
        failing must not be retried on every tick."""
        resolver = _Resolver(error=RuntimeError("db gone"))
        loop = _loop(_RecordingRefresher(), resolver, resolve_interval_sec=60.0)

        await loop.cycle(now=MONDAY)
        await loop.cycle(now=MONDAY + timedelta(seconds=15))

        assert len(resolver.calls) == 1


class TestJahitanHasilKeStatistik:
    """The three lines that carry per-asset failures out of a phase result.

    ``cycle()`` copies ``RefreshResult.failures`` into ``stats.refresh_failures``
    and ``ResolveResult.failures`` into ``stats.resolve_failures``. Both were at
    zero coverage: no test in the repository executed them, so deleting them
    left the suite green. That is this project's recurring defect one layer
    deeper - not code that is never reached, but the wire between two things
    that are.

    It matters because of a fix in the same round. Since one dead symbol stopped
    holding ``last_success_at`` back, ``consecutive_failures`` stays at 0 and
    every streak branch in ``UpkeepCheck`` is blind to it: these lines are the
    *only* road from a dead symbol to health. Measured with them replaced by
    ``pass`` - 5 assets, one of them always failing, 20 cycles: refresh_failures
    24 -> 0, and health DEGRADED ("24 refresh gagal sejak pemeriksaan terakhir")
    -> UP ("20 siklus, terakhir 0 detik lalu, 60 sinyal dinilai").

    So these two tests drive a real ``cycle()`` over a real ``CandleRefresher``
    and read the far end. The existing tests stop at ``RefreshResult``, and the
    health tests assign ``stats.refresh_failures`` by hand - which is testing
    the far end of a wire nobody checked was connected.
    """

    async def test_simbol_mati_dari_refresh_sampai_ke_stats_dan_health(self) -> None:
        ingestor = _Ingestor(
            Market.CRYPTO, intervals=(Horizon.M1,), assets=3, fail_symbols=("SYM2",)
        )
        refresher = _refresher(_Ingest(ingestor), candle_settle_sec=0.0)
        loop = _loop(refresher, _Resolver(), tick_sec=0.05)

        await loop.start()
        try:
            # One completed cycle is enough: ``cycles`` is incremented after the
            # refresh phase, so the failure has already been counted or has
            # already been lost.
            await _wait_until(lambda: loop.stats.cycles >= 1, what="one cycle")
            health = await UpkeepCheck(loop, background=True).check()
        finally:
            await loop.stop()

        assert loop.stats.refresh_failures >= 1, (
            "RefreshResult.failures never reached UpkeepStats"
        )
        assert loop.stats.candles > 0, "the healthy symbols were refreshed as usual"
        assert any("SYM2" in error for error in loop.stats.errors), (
            "the operator has to be able to see which symbol"
        )
        assert health.status is HealthStatus.DEGRADED
        assert "refresh gagal" in health.message

    async def test_kegagalan_per_sinyal_dari_resolver_sampai_ke_stats_dan_health(
        self,
    ) -> None:
        resolver = _Resolver(
            _Resolved(resolved=1, failures=["IDX ANTM 1h: mysql gone"])
        )
        loop = _loop(
            _RecordingRefresher(),
            resolver,
            tick_sec=0.05,
            resolve_interval_sec=0.001,
        )

        await loop.start()
        try:
            await _wait_until(lambda: loop.stats.cycles >= 1, what="one cycle")
            health = await UpkeepCheck(loop, background=True).check()
        finally:
            await loop.stop()

        assert loop.stats.resolve_failures >= 1, (
            "ResolveResult.failures never reached UpkeepStats"
        )
        assert loop.stats.resolved >= 1, "the pass still scored what it could"
        assert any("ANTM" in error for error in loop.stats.errors)
        assert health.status is HealthStatus.DEGRADED
        assert "resolusi gagal" in health.message


class TestAntreanTersumbat:
    """``due()`` orders by due time, so signals that cannot be scored sit at the
    head of the queue and are re-read every pass. Unreported, that looks exactly
    like an idle system."""

    async def test_satu_batch_penuh_yang_tidak_terskor_dihitung_tersumbat(self) -> None:
        resolver = _Resolver(_Resolved(awaiting_candles=60, no_prices=40))
        loop = _loop(_RecordingRefresher(), resolver, resolve_limit=100)
        stats = await loop.cycle(now=MONDAY)

        assert stats.clogged_passes == 1
        assert stats.awaiting_candles == 60
        assert stats.no_prices == 40

    async def test_pass_yang_menghasilkan_skor_tidak_dianggap_tersumbat(self) -> None:
        resolver = _Resolver(_Resolved(resolved=40, awaiting_candles=30, no_prices=20))
        loop = _loop(_RecordingRefresher(), resolver, resolve_limit=100)
        stats = await loop.cycle(now=MONDAY)

        assert stats.clogged_passes == 0
        assert stats.resolved == 40


# ---------------------------------------------------------------------------
# the loop survives
# ---------------------------------------------------------------------------


class TestSiklusGagalTidakMengakhiriLoop:
    """An unattended loop that dies on the first network hiccup is worse than
    no loop, because the operator believes it is still watching and a frozen
    series looks exactly like a quiet market."""

    async def test_run_until_bertahan_melewati_refresh_yang_selalu_gagal(self) -> None:
        clock = _Clock(MONDAY)
        refresher = _RecordingRefresher(error=RuntimeError("network went away"))
        resolver = _Resolver()
        loop = _loop(refresher, resolver, tick_sec=15.0, resolve_interval_sec=0.001)

        stats = await loop.run_until(
            MONDAY + timedelta(minutes=1), sleep=clock.sleep, now=clock
        )

        assert stats.cycles == 4
        assert stats.refresh_failures == 4
        assert len(resolver.calls) == 4, "resolution kept running throughout"
        assert any("network went away" in error for error in stats.errors)

    async def test_siklus_yang_meledak_di_luar_kedua_fase_tetap_dihitung(self) -> None:
        """``cycle()`` guards each phase, so reaching the outer handler means
        something else broke - and the run still has to survive it."""
        clock = _Clock(MONDAY)
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=15.0)

        async def explode(**_kwargs: object) -> UpkeepStats:
            raise RuntimeError("something outside the phases")

        loop.cycle = explode  # type: ignore[method-assign]
        stats = await loop.run_until(
            MONDAY + timedelta(seconds=45), sleep=clock.sleep, now=clock
        )

        assert stats.failed_cycles == 3
        assert any("something outside" in error for error in stats.errors)

    async def test_task_latar_tetap_hidup_setelah_siklus_gagal(self) -> None:
        """The guard that matters in production is the one inside the task."""
        refresher = _RecordingRefresher(error=RuntimeError("boom"))
        loop = _loop(refresher, _Resolver(), tick_sec=0.01)

        await loop.start()
        try:
            await _wait_until(
                lambda: loop.stats.cycles >= 3, what="three failing cycles"
            )
            assert loop.running is True
            assert loop.stats.refresh_failures >= 3
        finally:
            await loop.stop()
        assert loop.running is False

    async def test_task_latar_bertahan_saat_siklus_meledak_seluruhnya(self) -> None:
        """The guard inside ``_loop`` itself, not the one inside ``cycle()``.

        Without it the task ends on the first unexpected error and nothing says
        so: ``running`` goes false, the candles freeze, and the operator is
        still looking at a process that appears to be up.
        """
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=0.01)

        async def explode(**_kwargs: object) -> UpkeepStats:
            raise RuntimeError("something outside the phases")

        loop.cycle = explode  # type: ignore[method-assign]

        await loop.start()
        try:
            await _wait_until(
                lambda: loop.stats.failed_cycles >= 3, what="three exploded cycles"
            )
            assert loop.running is True
        finally:
            await loop.stop()

    async def test_start_dua_kali_tidak_membuat_dua_task(self) -> None:
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=0.01)
        await loop.start()
        try:
            first = loop._task
            await loop.start()
            assert loop._task is first
        finally:
            await loop.stop()

    async def test_jendela_yang_sudah_lewat_tidak_menjalankan_siklus(self) -> None:
        refresher = _RecordingRefresher()
        loop = _loop(refresher, _Resolver())
        clock = _Clock(MONDAY)
        stats = await loop.run_until(
            MONDAY - timedelta(hours=1), sleep=clock.sleep, now=clock
        )
        assert refresher.calls == []
        assert stats.cycles == 0

    async def test_tidur_tidak_pernah_melewati_akhir_jendela(self) -> None:
        clock = _Clock(MONDAY)
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=15.0)
        await loop.run_until(MONDAY + timedelta(seconds=10), sleep=clock.sleep, now=clock)
        assert clock.slept == [10.0]


class TestCatatanLoop:
    def test_daftar_error_dibatasi(self) -> None:
        """An unattended week of failures must not grow without limit."""
        stats = UpkeepStats(started_at=MONDAY)
        for i in range(MAX_ERRORS + 50):
            stats.note_error(f"problem {i}")
        assert len(stats.errors) == MAX_ERRORS

    def test_error_dipotong_supaya_tidak_menelan_log(self) -> None:
        stats = UpkeepStats(started_at=MONDAY)
        stats.note_error("x" * 500)
        assert len(stats.errors[0]) == 200

    def test_to_dict_memakai_key_inggris_karena_itu_data(self) -> None:
        stats = UpkeepStats(started_at=MONDAY, cycles=2, resolved=3)
        payload = stats.to_dict()
        assert payload["cycles"] == 2
        assert payload["resolved"] == 3
        assert payload["started_at"].startswith("2026-08-17")
        assert all(key.isascii() for key in payload)


# ---------------------------------------------------------------------------
# what the operator reads (rules F and G)
# ---------------------------------------------------------------------------


class TestTeksUntukOperator:
    async def test_hasil_refresh_sampai_ke_baris_yang_dibaca_operator(self) -> None:
        """The pass's own facts have to survive the trip into the printed line.

        This used to assert ``RefreshResult.summary()``, an Indonesian sentence
        no code in ``src/aruna`` ever called: ``aruna upkeep`` prints
        ``UpkeepStats.summary()``. So the test passed while the two facts it
        checked - work deferred by the request ceiling, and a market skipped
        because its exchange was shut - never reached anyone, and "0 candle
        disegarkan" arrived with no reason attached. The rendering is gone; what
        is asserted now is that the same facts come out of the reader that
        exists.
        """
        refresher = _RecordingRefresher(
            result=RefreshResult(
                requests=4,
                candles=12,
                refreshed=[(Market.CRYPTO, Horizon.M1)],
                deferred=[(Market.CRYPTO, Horizon.D1)],
                skipped_closed=[Market.IDX],
            )
        )
        loop = UpkeepLoop(
            refresher=refresher,  # type: ignore[arg-type]
            resolver=_Resolver(),
            settings=_settings(resolve_enabled=False),
        )

        await loop.cycle(now=MONDAY)

        text = loop.stats.summary()
        assert "12 candle disegarkan" in text
        assert "4 request" in text
        assert "1 interval ditunda karena batas request" in text
        assert "IDX" in text and "bursa tutup" in text

    def test_ringkasan_loop_berbahasa_indonesia(self) -> None:
        stats = UpkeepStats(
            started_at=MONDAY,
            cycles=3,
            candles=30,
            requests=12,
            resolved=2,
            awaiting_candles=5,
            last_awaiting_candles=5,
            resolve_pass_seen=True,
            clogged_passes=1,
        )
        text = stats.summary()
        assert "3 siklus" in text
        assert "30 candle disegarkan" in text
        assert "2 sinyal dinilai" in text
        assert "menunggu candle menyusul" in text
        # Labelled, so a run total and a queue can never be read as the same
        # kind of number.
        assert "putaran terakhir" in text

    def test_loop_yang_belum_pernah_berputar_mengatakannya(self) -> None:
        assert UpkeepStats(started_at=MONDAY).summary() == (
            "belum ada siklus upkeep yang selesai"
        )

    def test_lag_dibaca_manusia(self) -> None:
        assert lag_text(45) == "45 detik"
        assert lag_text(600) == "10 menit"
        assert lag_text(7200) == "2 jam 0 menit"
        assert lag_text(90_000) == "1 hari 1 jam"
        assert lag_text(-5) == "0 detik"

    def test_tidak_ada_klaim_terlarang_di_teks_apa_pun(self) -> None:
        """SPEC: nothing ARUNA sends may promise a certainty it does not have."""
        texts = [
            UpkeepStats(started_at=MONDAY).summary(),
            UpkeepStats(
                started_at=MONDAY,
                cycles=1,
                resolved=1,
                awaiting_candles=1,
                no_prices=1,
                clogged_passes=1,
            ).summary(),
            # Every branch of the line that carries a reason, because these are
            # the words the ceiling, the closed exchange and the switched-off
            # resolver put on screen - and they were added after this sweep was
            # written.
            UpkeepStats(
                started_at=MONDAY,
                cycles=1,
                unavailable_interval=2,
                deferred=1,
                last_skipped_markets=[Market.IDX.value],
                resolve_enabled=False,
                refresh_failures=3,
                resolve_failures=1,
            ).summary(),
            lag_text(3600),
        ]
        for text in texts:
            assert find_forbidden(text) == ()


# ---------------------------------------------------------------------------
# health: two sources, on purpose
# ---------------------------------------------------------------------------


def _fresh(intervals: tuple[Horizon, ...]) -> dict[tuple[int, str], datetime]:
    reference = now_utc()
    return {
        (1, interval.value): reference - interval.duration for interval in intervals
    }


class TestKesehatanLoop:
    async def test_loop_mati_dilaporkan_down(self) -> None:
        """The alarm for this project's recurring defect: a component that is
        wired, exported and not running."""
        loop = _loop(_RecordingRefresher(), _Resolver())
        health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DOWN
        assert "tidak berjalan" in health.message

    async def test_perintah_sekali_jalan_bukan_kerusakan(self) -> None:
        """Reporting a deliberate one-shot as a fault would make every `aruna
        signal` run announce itself broken for doing what it was asked."""
        loop = _loop(_RecordingRefresher(), _Resolver())
        health = await UpkeepCheck(loop, background=False).check()
        assert health.status is HealthStatus.UP
        assert "sekali jalan" in health.message

    async def test_loop_yang_berputar_dilaporkan_up(self) -> None:
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=0.01)
        await loop.start()
        try:
            await _wait_until(lambda: loop.stats.cycles >= 1, what="one cycle")
            health = await UpkeepCheck(loop, background=True).check()
        finally:
            await loop.stop()
        assert health.status is HealthStatus.UP
        assert "siklus" in health.message

    async def test_antrean_yang_tidak_berkurang_dilaporkan_degraded(self) -> None:
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=0.01)
        loop.stats.clogged_passes = 3
        await loop.start()
        try:
            await _wait_until(lambda: loop.stats.cycles >= 1, what="one cycle")
            health = await UpkeepCheck(loop, background=True).check()
        finally:
            await loop.stop()
        assert health.status is HealthStatus.DEGRADED
        assert "tersumbat" in health.message

    async def test_backlog_di_atas_batas_pass_dilaporkan(self) -> None:
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=0.01, resolve_limit=100)

        async def due() -> int:
            return 118

        await loop.start()
        try:
            await _wait_until(lambda: loop.stats.cycles >= 1, what="one cycle")
            health = await UpkeepCheck(loop, background=True, due_count=due).check()
        finally:
            await loop.stop()
        assert health.status is HealthStatus.DEGRADED
        assert "118 sinyal jatuh tempo" in health.message


class TestKesegaranCandle:
    """Read straight out of MySQL and never from the loop. A loop that runs and
    writes nothing is exactly the defect this project has shipped before, and
    only the second question catches it."""

    async def test_candle_mutakhir_dilaporkan_up(self) -> None:
        intervals = (Horizon.M1, Horizon.H1)
        check = CandleFreshnessCheck(
            _Store(_fresh(intervals)),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=intervals,
        )
        health = await check.check()
        assert health.status is HealthStatus.UP
        assert "mutakhir" in health.message

    async def test_seri_beku_dilaporkan_degraded(self) -> None:
        """The measured symptom: 1m frozen at 16:37 while the loop looked fine."""
        frozen = {(1, "1m"): now_utc() - timedelta(hours=3)}
        check = CandleFreshnessCheck(
            _Store(frozen),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.M1,),
            factor=3.0,
        )
        health = await check.check()
        assert health.status is HealthStatus.DEGRADED
        assert "tertinggal" in health.message

    async def test_interval_tanpa_candle_sama_sekali_dilaporkan_down(self) -> None:
        check = CandleFreshnessCheck(
            _Store(_fresh((Horizon.M1,))),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.M1, Horizon.H1),
        )
        health = await check.check()
        assert health.status is HealthStatus.DOWN
        assert "1h" in health.message
        assert "aruna fetch" in health.message

    async def test_horizon_terkunci_yang_tidak_disegarkan_dilaporkan(self) -> None:
        """A prediction locked on 4h draws its evidence from 4h bars; outside
        the refresh set those bars go stale under it."""

        async def locked() -> list[str]:
            return ["4h", "1h"]

        check = CandleFreshnessCheck(
            _Store(_fresh((Horizon.M1, Horizon.H1))),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.M1, Horizon.H1),
            locked_horizons=locked,
            supported=(Horizon.M1, Horizon.H1, Horizon.H4),
        )
        health = await check.check()
        assert health.status is HealthStatus.DEGRADED
        assert "4h" in health.message
        assert health.details["locked_horizons_not_refreshed"] == ["4h"]

    async def test_probe_yang_gagal_tidak_pernah_melempar(self) -> None:
        check = CandleFreshnessCheck(
            _Store(error=RuntimeError("mysql gone")),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.M1,),
        )
        health = await check.check()
        assert health.status is HealthStatus.DOWN
        assert "mysql gone" in health.message

    async def test_loop_sehat_tapi_database_beku_tetap_ketahuan(self) -> None:
        """The two sources must be able to disagree, because that disagreement
        is the whole reason there are two of them."""
        loop = _loop(_RecordingRefresher(), _Resolver(), tick_sec=0.01)
        frozen = {(1, "1m"): now_utc() - timedelta(hours=6)}
        candles = CandleFreshnessCheck(
            _Store(frozen),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.M1,),
        )

        await loop.start()
        try:
            await _wait_until(lambda: loop.stats.cycles >= 1, what="one cycle")
            upkeep = await UpkeepCheck(loop, background=True).check()
            freshness = await candles.check()
        finally:
            await loop.stop()

        assert upkeep.status is HealthStatus.UP
        assert freshness.status is HealthStatus.DEGRADED

    async def test_pesan_kesehatan_berbahasa_indonesia_dan_bersih(self) -> None:
        messages = []
        for store, intervals in (
            (_Store(_fresh((Horizon.M1,))), (Horizon.M1,)),
            (_Store({(1, "1m"): now_utc() - timedelta(hours=3)}), (Horizon.M1,)),
            (_Store(), (Horizon.M1,)),
        ):
            check = CandleFreshnessCheck(
                store,  # type: ignore[arg-type]
                market=Market.CRYPTO,
                intervals=intervals,
            )
            messages.append((await check.check()).message)
        for message in messages:
            assert find_forbidden(message) == ()
            assert message


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class TestPengaturanUpkeep:
    def test_default_menurunkan_himpunan_interval(self) -> None:
        assert _settings().candle_intervals == ""
        assert _settings().candle_interval_overrides == ()

    def test_override_diurai_menjadi_horizon(self) -> None:
        assert _settings(candle_intervals="15m,4h").candle_interval_overrides == (
            Horizon.M15,
            Horizon.H4,
        )

    def test_interval_salah_ketik_ditolak_saat_konfigurasi(self) -> None:
        """Otherwise the typo is discovered by an unattended loop at three in
        the morning, as a silently smaller refresh set."""
        with pytest.raises(ValidationError) as exc:
            _settings(candle_intervals="15m,1hour")
        assert "1hour" in str(exc.value)

    def test_overlap_tidak_boleh_di_bawah_dua(self) -> None:
        """The forming bar has to be pulled again to be stored closed."""
        with pytest.raises(ValidationError):
            _settings(candle_overlap_bars=1)

    def test_loop_menyala_secara_default(self) -> None:
        """The defect this loop fixes was a loop that did not exist; shipping
        it off by default would reproduce that exactly."""
        assert _settings().enabled is True
        assert _settings().resolve_enabled is True

    def test_upkeep_muncul_di_describe(self) -> None:
        assert "upkeep" in make_settings().describe()
