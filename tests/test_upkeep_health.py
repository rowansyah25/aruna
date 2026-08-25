"""The honesty layer, held to its own standard.

``aruna.health.upkeep`` opens by promising to catch this project's recurring
defect - "a loop spinning happily while writing no rows... A component that read
the loop's statistics would call that UP. This one calls it DOWN." It then
committed that defect four times over, and every test below is one of them,
written so that undoing the fix turns the test red rather than quietly restoring
the lie:

* fifty cycles in which **every** provider call failed and no candle was ever
  stored were reported **UP**, because the only failure counter anything read
  was ``failed_cycles`` - which ``cycle()`` keeps at zero by construction, since
  it catches both phases itself;
* IDX freshness was measured against the *previous session's close* even while
  the exchange was trading, so through the whole trading day - the only hours in
  which dead IDX ingestion can be seen at all - a three-day-old series compared
  as favourably as a perfect one;
* freshness collapsed every asset in a market into one ``max()``, so a single
  live symbol hid any number of frozen ones, and the evidence was not kept
  either;
* a failed ``locked_horizons`` query returned ``[]``, which is indistinguishable
  from "asked, nothing to report" - clean, for a question nobody answered.

Two more properties are locked here because both are permanent when broken:
``stop()`` must let the resolution pass finish its four writes rather than
cancelling between them (a scored prediction may not be edited afterwards, SPEC
22), and a batch that is entirely ``unavailable_interval`` must count as clogged
- it is the one category that never clears on its own.

Nothing here touches the network, the database or the wall clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aruna.core.claims import find_forbidden
from aruna.core.clock import now_utc, to_jakarta
from aruna.core.config import UpkeepSettings
from aruna.core.enums import HealthStatus, Horizon, Market
from aruna.health.upkeep import (
    CandleFreshnessCheck,
    UpkeepCheck,
    bar_market_seconds,
    idx_session_seconds,
    idx_trading_seconds,
    last_idx_trading_instant,
)
from aruna.upkeep.candles import RefreshResult
from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

# WIB = UTC+7. The IDX moments below are chosen against the real exchange
# windows: 09:00-12:00 and 13:30-15:49:59 on a Monday.
FRIDAY_CLOSE = datetime(2026, 8, 14, 8, 45, tzinfo=UTC)  # Fri 15:45 WIB
MONDAY_PREOPEN = datetime(2026, 8, 17, 1, 30, tzinfo=UTC)  # Mon 08:30 WIB
MONDAY_JUST_OPEN = datetime(2026, 8, 17, 2, 5, tzinfo=UTC)  # Mon 09:05 WIB
MONDAY_MIDSESSION = datetime(2026, 8, 17, 7, 0, tzinfo=UTC)  # Mon 14:00 WIB
MONDAY_LUNCH = datetime(2026, 8, 17, 5, 30, tzinfo=UTC)  # Mon 12:30 WIB
MONDAY_EVENING = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)  # Mon 19:00 WIB
SUNDAY = datetime(2026, 8, 16, 5, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# stand-ins
# ---------------------------------------------------------------------------


class _Store:
    """Answers the one ``newest_closed_candles`` query the probe makes."""

    def __init__(
        self,
        newest: dict[tuple[int, str], datetime] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.newest = dict(newest or {})
        self.error = error

    async def newest_closed_candles(
        self, market: Market, intervals: tuple[Horizon, ...]
    ) -> dict[tuple[int, str], datetime]:
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
    unavailable_interval: int = 0
    failures: list[str] = field(default_factory=list)


class _Resolver:
    def __init__(self, result: _Resolved | None = None) -> None:
        self.result = result or _Resolved()
        self.calls = 0

    async def resolve_due(self, *, reference: datetime, limit: int) -> _Resolved:
        self.calls += 1
        return self.result


class _Refresher:
    """Stands in for ``CandleRefresher``; ``state()`` is what health reads."""

    def __init__(
        self,
        *,
        error: Exception | None = None,
        result: RefreshResult | None = None,
        schedule: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self.error = error
        self.result = result or RefreshResult()
        self.schedule = schedule or {}

    async def refresh(self, *, now: datetime) -> RefreshResult:
        if self.error is not None:
            raise self.error
        return self.result

    def state(self) -> dict[str, dict[str, Any]]:
        return self.schedule


def _settings(**overrides: object) -> UpkeepSettings:
    return UpkeepSettings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _loop(refresher: object, resolver: object, **overrides: object) -> UpkeepLoop:
    return UpkeepLoop(
        refresher=refresher,  # type: ignore[arg-type]
        resolver=resolver,
        settings=_settings(**overrides),
    )


@asynccontextmanager
async def _alive(loop: UpkeepLoop) -> AsyncIterator[UpkeepLoop]:
    """Make ``loop.running`` true without letting it cycle.

    ``UpkeepCheck`` reports DOWN for a loop that is not running, so these tests
    need a live task - but they are about what the *counters* say, and a real
    task racing them would make the numbers under test move on their own. The
    task is parked; the statistics are whatever the test set.
    """
    loop._task = asyncio.create_task(asyncio.sleep(3600))
    try:
        yield loop
    finally:
        loop._task.cancel()
        with suppress(asyncio.CancelledError):
            await loop._task
        loop._task = None


def _at(moment: datetime, monkeypatch) -> None:
    """Freeze the clock the freshness probe reads."""
    import aruna.health.upkeep as module

    monkeypatch.setattr(module, "now_utc", lambda: moment)


def _idx_store(**stamps: datetime) -> _Store:
    return _Store({(1, code): stamp for code, stamp in stamps.items()})


# ---------------------------------------------------------------------------
# IDX freshness: the reference, and the hours it was blind in
# ---------------------------------------------------------------------------

IDX_INTERVALS = (Horizon.M15, Horizon.H1, Horizon.D1)


def _idx_check(store: _Store) -> CandleFreshnessCheck:
    return CandleFreshnessCheck(
        store,  # type: ignore[arg-type]
        market=Market.IDX,
        intervals=IDX_INTERVALS,
    )


class TestReferensiIdx:
    def test_referensi_saat_bursa_buka_adalah_sekarang(self) -> None:
        """Anything earlier and a dead feed compares as well as a live one."""
        assert last_idx_trading_instant(MONDAY_MIDSESSION) == MONDAY_MIDSESSION

    def test_referensi_saat_jeda_makan_siang_adalah_akhir_sesi_satu(self) -> None:
        """12:00-13:30 is shut, but the morning's bars are already owed."""
        reference = to_jakarta(last_idx_trading_instant(MONDAY_LUNCH))
        assert (reference.hour, reference.minute) == (12, 0)
        assert reference.date() == to_jakarta(MONDAY_LUNCH).date()

    def test_referensi_sebelum_pembukaan_adalah_penutupan_terakhir(self) -> None:
        reference = to_jakarta(last_idx_trading_instant(MONDAY_PREOPEN))
        assert reference.weekday() == 4  # Friday
        assert (reference.hour, reference.minute) == (15, 49)

    def test_referensi_akhir_pekan_adalah_penutupan_jumat(self) -> None:
        """Measuring the weekend against now is how a component cries every
        Sunday until nobody reads it."""
        reference = to_jakarta(last_idx_trading_instant(SUNDAY))
        assert reference.weekday() == 4
        assert (reference.hour, reference.minute) == (15, 49)

    def test_waktu_perdagangan_melewati_akhir_pekan_dan_jeda(self) -> None:
        friday = datetime(2026, 8, 14, 8, 45, tzinfo=UTC)  # Fri 15:45 WIB
        monday_noon = datetime(2026, 8, 17, 5, 0, tzinfo=UTC)  # Mon 12:00 WIB
        elapsed = idx_trading_seconds(friday, monday_noon)
        # Friday 15:45-15:49:59 plus Monday 09:00-12:00. The weekend counts for
        # nothing, which is the whole point.
        assert elapsed == 4 * 60 + 59 + 3 * 3600

    def test_satu_bar_1d_idx_adalah_satu_sesi_bukan_dua_puluh_empat_jam(self) -> None:
        """Tolerance is quoted in bar lengths; once age is counted in open time
        the length has to be too, or 1d silently means thirteen trading days."""
        assert bar_market_seconds(Horizon.D1, market=Market.IDX) == idx_session_seconds()
        assert bar_market_seconds(Horizon.D1, market=Market.CRYPTO) == 86_400.0
        assert bar_market_seconds(Horizon.M15, market=Market.IDX) == 900.0


class TestKesegaranIdxSaatBursaBuka:
    """The blocker: through the whole trading day this reported UP."""

    async def test_seri_beku_ketahuan_di_tengah_sesi(self, monkeypatch) -> None:
        _at(MONDAY_MIDSESSION, monkeypatch)
        store = _idx_store(
            **{"15m": FRIDAY_CLOSE, "1h": FRIDAY_CLOSE, "1d": FRIDAY_CLOSE}
        )
        health = await _idx_check(store).check()
        assert health.status is HealthStatus.DEGRADED
        assert "15m" in health.message
        assert "waktu perdagangan" in health.message

    async def test_seri_beku_ketahuan_saat_jeda_makan_siang(self, monkeypatch) -> None:
        """The lunch break is shut, so a plain open/closed predicate hands back
        Friday's close and the morning's missing bars go unreported."""
        _at(MONDAY_LUNCH, monkeypatch)
        store = _idx_store(
            **{"15m": FRIDAY_CLOSE, "1h": FRIDAY_CLOSE, "1d": FRIDAY_CLOSE}
        )
        health = await _idx_check(store).check()
        assert health.status is HealthStatus.DEGRADED
        assert "15m" in health.message

    async def test_pembukaan_pagi_bukan_alarm_palsu(self, monkeypatch) -> None:
        """Five minutes after the open the last bar really is Friday's, and
        nothing is wrong. A reference of plain ``now`` would report three days
        broken every single morning."""
        _at(MONDAY_JUST_OPEN, monkeypatch)
        store = _idx_store(
            **{
                "15m": datetime(2026, 8, 14, 8, 45, tzinfo=UTC),
                "1h": datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
                "1d": datetime(2026, 8, 14, 2, 0, tzinfo=UTC),
            }
        )
        health = await _idx_check(store).check()
        assert health.status is HealthStatus.UP

    async def test_akhir_pekan_bukan_alarm_palsu(self, monkeypatch) -> None:
        _at(SUNDAY, monkeypatch)
        store = _idx_store(
            **{
                "15m": datetime(2026, 8, 14, 8, 45, tzinfo=UTC),
                "1h": datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
                "1d": datetime(2026, 8, 14, 2, 0, tzinfo=UTC),
            }
        )
        health = await _idx_check(store).check()
        assert health.status is HealthStatus.UP

    async def test_1d_beku_empat_sesi_dilaporkan(self, monkeypatch) -> None:
        """Four trading days of silence on the series every 1d IDX prediction
        is scored from. Against a 24-hour "bar length" this stays quiet."""
        _at(MONDAY_MIDSESSION, monkeypatch)
        stale_daily = datetime(2026, 8, 11, 2, 0, tzinfo=UTC)  # Tue 09:00 WIB
        store = _Store(
            {
                (1, "15m"): datetime(2026, 8, 17, 6, 45, tzinfo=UTC),
                (1, "1h"): datetime(2026, 8, 17, 6, 0, tzinfo=UTC),
                (1, "1d"): stale_daily,
            }
        )
        health = await _idx_check(store).check()
        assert health.status is HealthStatus.DEGRADED
        assert "1d" in health.message


class TestKesegaranPerAset:
    async def test_satu_aset_segar_tidak_menutupi_aset_beku(self) -> None:
        """The likeliest partial failure there is: backfill reports failures per
        asset, so one delisted symbol freezes one series while the rest answer."""
        reference = now_utc()
        store = _Store(
            {
                (1, "1h"): reference - timedelta(hours=1),
                (2, "1h"): reference - timedelta(days=7),
                (3, "1h"): reference - timedelta(days=30),
            }
        )
        check = CandleFreshnessCheck(
            store,  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.H1,),
        )
        health = await check.check()
        assert health.status is HealthStatus.DEGRADED
        assert "2 dari 3 aset" in health.message
        interval = health.details["intervals"]["1h"]  # type: ignore[index]
        assert interval["stale_assets"] == [2, 3]
        assert interval["assets"] == 3

    async def test_semua_aset_segar_tetap_up(self) -> None:
        reference = now_utc()
        store = _Store(
            {
                (asset, "1h"): reference - timedelta(minutes=30)
                for asset in (1, 2, 3)
            }
        )
        check = CandleFreshnessCheck(
            store,  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.H1,),
        )
        health = await check.check()
        assert health.status is HealthStatus.UP
        assert health.details["intervals"]["1h"]["stale_assets"] == []  # type: ignore[index]


class TestPertanyaanYangTidakTerjawab:
    async def test_horizon_terkunci_gagal_dibaca_dicatat_bukan_dianggap_bersih(
        self,
    ) -> None:
        """"Tidak tahu" must not read as "bersih" - the discipline engine's
        ``clean=true`` for a question it never asked, in the component built to
        catch exactly that."""

        async def boom() -> list[str]:
            raise RuntimeError("signals table unreachable")

        check = CandleFreshnessCheck(
            _Store({(1, "1h"): now_utc() - timedelta(minutes=30)}),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.H1,),
            locked_horizons=boom,
        )
        health = await check.check()
        assert health.status is HealthStatus.DEGRADED
        assert "signals table unreachable" in str(health.details["locked_horizons_error"])
        assert "tidak bisa dijalankan" in health.message

    async def test_horizon_terkunci_yang_terbaca_tidak_mencatat_kesalahan(self) -> None:
        async def locked() -> list[str]:
            return ["1h"]

        check = CandleFreshnessCheck(
            _Store({(1, "1h"): now_utc() - timedelta(minutes=30)}),  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.H1,),
            locked_horizons=locked,
        )
        health = await check.check()
        assert health.status is HealthStatus.UP
        assert "locked_horizons_error" not in health.details


# ---------------------------------------------------------------------------
# the loop's own counters
# ---------------------------------------------------------------------------


class TestLoopYangBerputarTanpaMenjangkauApaPun:
    async def test_semua_refresh_gagal_dilaporkan_down(self) -> None:
        """The measured scenario, exactly: 50 cycles, every provider call
        failing, zero candles - reported UP because ``failed_cycles`` stays at
        zero when ``cycle()`` catches both phases itself."""
        loop = _loop(
            _Refresher(error=ConnectionError("provider unreachable")),
            _Resolver(),
            resolve_enabled=False,
        )
        for _ in range(50):
            await loop.cycle()

        assert loop.stats.failed_cycles == 0
        assert loop.stats.refresh_failures == 50
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DOWN
        assert "50 refresh gagal" in health.message
        assert "0 candle tersimpan" in health.message

    async def test_kegagalan_refresh_baru_dilaporkan_meski_ada_candle(self) -> None:
        """A loop that is mostly working still has to say what broke."""
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.refresh_failures = 4
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DEGRADED
        assert "4 refresh gagal sejak pemeriksaan terakhir" in health.message

    async def test_kegagalan_resolusi_dilaporkan(self) -> None:
        """For the resolve side nothing else covers this at all - the freshness
        component reads MySQL and knows nothing about scoring."""
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.resolve_failures = 7
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DEGRADED
        assert "7 resolusi gagal sejak pemeriksaan terakhir" in health.message

    async def test_kegagalan_yang_sudah_dilaporkan_tidak_diulang(self) -> None:
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.refresh_failures = 4
        loop.stats.last_cycle_at = now_utc()
        check = UpkeepCheck(loop, background=True)
        async with _alive(loop):
            first = await check.check()
            second = await check.check()
        assert first.status is HealthStatus.DEGRADED
        assert second.status is HealthStatus.UP

    async def test_resolusi_yang_terus_gagal_tidak_kembali_up(self) -> None:
        """A delta test alone reports a standing fault only on the sweeps that
        happen to straddle a new failure.

        Health sweeps every 30s against a 60s resolve cadence, so half of them
        saw no new failure - and announced UP "0 sinyal dinilai" for a resolver
        that had thrown on every one of its attempts. The refresh side already
        had a level-triggered branch for exactly this; the resolve side had
        only the delta. A fault that is still true has to still be said.
        """
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.resolve_failures = 20
        loop.stats.resolved = 0
        loop.stats.last_cycle_at = now_utc()
        check = UpkeepCheck(loop, background=True)
        async with _alive(loop):
            sweeps = [(await check.check()) for _ in range(6)]
        assert all(s.status is HealthStatus.DEGRADED for s in sweeps), [
            s.status.value for s in sweeps
        ]
        # The later sweeps see no new failure, so they must say the standing
        # one instead of falling silent.
        assert (
            "20 resolusi gagal sejak terakhir kali ada sinyal yang berhasil dinilai"
            in sweeps[-1].message
        )

    async def test_resolusi_yang_pulih_tidak_diteriaki_selamanya(self) -> None:
        """A recovered run must not stay an alarm - noise gets ignored, and
        that is how the original blind spot survived.

        Progress is what clears it: failures older than the last successful
        scoring are history. An earlier version of this test set
        ``resolved=3`` and asserted UP *while the resolver was still dead*,
        which locked the hole below as correct behaviour.
        """
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.resolve_failures = 20
        loop.stats.resolved = 3
        loop.stats.last_cycle_at = now_utc()
        check = UpkeepCheck(loop, background=True)
        async with _alive(loop):
            await check.check()  # consumes the delta and marks the progress
            second = await check.check()
        assert second.status is HealthStatus.UP

    async def test_resolusi_yang_mati_setelah_pernah_berhasil_tetap_disebut(
        self,
    ) -> None:
        """The shape the first fix missed, and the likelier one in production.

        Gated on ``not stats.resolved``, one signal scored at startup silenced
        the branch for the life of the process - so a resolver that died *after*
        scoring once went back to announcing UP on half the sweeps, which is
        precisely the fault the branch exists to close. Measured then:
        21 UP / 19 DEGRADED against 19 failures in 20 attempts.
        """
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.resolved = 1
        loop.stats.resolve_failures = 1
        loop.stats.last_cycle_at = now_utc()
        check = UpkeepCheck(loop, background=True)

        async with _alive(loop):
            await check.check()  # the one success is registered
            # Now the resolver dies: failures climb, nothing more is scored.
            sweeps = []
            for _ in range(6):
                loop.stats.resolve_failures += 1
                sweeps.append(await check.check())
                sweeps.append(await check.check())  # the sweep with no new delta

        assert all(s.status is HealthStatus.DEGRADED for s in sweeps), [
            s.status.value for s in sweeps
        ]
        assert "sejak terakhir kali ada sinyal yang berhasil dinilai" in sweeps[-1].message


class TestSiklusMencatatKapanSelesaiBukanKapanMulai:
    """`last_cycle_at` menjawab "kapan loop terakhir MENYELESAIKAN sesuatu".

    Versi lama menyimpan `moment` - stempel yang diambil di AWAL siklus dan
    dioper ke setiap fase sebagai "as of". Benar untuk fase-fase itu, salah
    untuk pertanyaan ini: umur yang dibaca penjaga kesehatan jadi durasi siklus
    LALU ditambah waktu berjalan siklus SEKARANG. Dobel hitung, dan itu separuh
    dari kenapa penjaganya berteriak pada 100% siklus sehat.
    """

    async def test_stempelnya_tidak_mendahului_pekerjaannya(self) -> None:
        import asyncio as _asyncio

        class _Lambat:
            def __init__(self) -> None:
                self.schedule: dict = {}

            async def refresh(self, *, now: datetime) -> RefreshResult:
                await _asyncio.sleep(0.05)
                return RefreshResult()

            def state(self) -> dict:
                return self.schedule

        mulai = datetime(2026, 8, 25, 5, 0, tzinfo=UTC)
        loop = _loop(_Lambat(), _Resolver(), resolve_enabled=False)
        await loop.cycle(now=mulai)

        assert loop.stats.last_cycle_at is not None
        assert loop.stats.last_cycle_at > mulai, (
            "siklus dicap selesai pada detik ia mulai - pekerjaannya tidak "
            "terhitung, dan penjaga kesehatan membacanya sebagai umur ekstra"
        )

    async def test_durasinya_dicatat_untuk_menghitung_anggaran(self) -> None:
        loop = _loop(_Refresher(), _Resolver(), resolve_enabled=False)
        await loop.cycle()

        assert len(loop.stats.cycle_seconds) == 1
        assert loop.stats.durasi_khas is not None

    async def test_belum_ada_siklus_berarti_belum_terukur(self) -> None:
        """Belum terukur bukan nol - dan nol akan membuat anggarannya nol."""
        stats = UpkeepStats(started_at=now_utc())

        assert stats.durasi_khas is None

    async def test_jendelanya_tidak_tumbuh_tanpa_batas(self) -> None:
        """Sepekan tanpa pengawasan tidak boleh menumbuhkan daftar ini."""
        from aruna.upkeep.loop import CYCLE_WINDOW

        stats = UpkeepStats(started_at=now_utc())
        for i in range(CYCLE_WINDOW * 3):
            stats.catat_durasi(float(i))

        assert len(stats.cycle_seconds) == CYCLE_WINDOW
        # Yang tersisa harus yang TERBARU, bukan yang terlama.
        assert stats.cycle_seconds[-1] == float(CYCLE_WINDOW * 3 - 1)


class TestAnggaranMacetDiturunkanDariDurasiSiklus:
    """Penjaga "loop macet" yang berteriak pada siklus SEHAT bukan penjaga.

    Dua cacat, dan keduanya harus dibongkar untuk memperbaikinya:

    * `last_cycle_at` menyimpan waktu MULAI siklus, jadi umur yang dibaca di
      sini adalah durasi siklus lalu ditambah waktu berjalan siklus sekarang;
    * anggarannya `tick_sec * 4`, yang mengandaikan satu siklus jauh lebih
      murah daripada satu tick.

    Terukur 2026-08-25 atas 401 siklus sehat: siklus memakan p50 64 detik
    terhadap tick 15 detik, dan penjaganya melanggar batasnya sendiri pada 100%
    siklus - 207 CRITICAL dalam 29 jam, semuanya palsu.
    """

    @staticmethod
    def _loop_dengan_siklus(detik: float, *, sejak: float):
        """Loop yang siklusnya memakan ``detik`` dan selesai ``sejak`` detik lalu."""
        loop = _loop(_Refresher(), _Resolver(), tick_sec=15.0)
        loop.stats.cycles = 20
        loop.stats.candles = 400
        loop.stats.cycle_seconds = [detik] * 10
        loop.stats.last_cycle_at = now_utc() - timedelta(seconds=sejak)
        return loop

    async def test_siklus_berat_yang_baru_selesai_bukan_macet(self) -> None:
        """Siklus 64 detik yang selesai 30 detik lalu - persis bentuk yang
        dulu memicu CRITICAL, dan tidak ada yang salah dengannya."""
        loop = self._loop_dengan_siklus(64.0, sejak=30.0)
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()

        assert health.status is not HealthStatus.DOWN
        assert "macet" not in health.message

    async def test_ekor_sebaran_yang_sehat_juga_bukan_macet(self) -> None:
        """p99 siklus adalah enam kali p50. Anggaran yang tidak memuat ekornya
        akan menyebut sebaran normal sebagai kerusakan."""
        loop = self._loop_dengan_siklus(64.0, sejak=385.0)
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()

        assert health.status is not HealthStatus.DOWN

    async def test_yang_benar_benar_macet_tetap_ditangkap(self) -> None:
        """Pasangannya. Tanpa ini, perbaikan di atas bisa "lulus" dengan
        melebarkan anggaran sampai tak terhingga."""
        loop = self._loop_dengan_siklus(64.0, sejak=3600.0)
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()

        assert health.status is HealthStatus.DOWN
        assert "macet" in health.message

    async def test_anggaran_mengikuti_mesin_yang_lambat(self) -> None:
        """Mesin yang siklusnya empat kali lebih lama melebarkan anggarannya
        sendiri - tanpa siapa pun menyunting konstanta.

        Umur yang sama dinilai macet di mesin cepat dan sehat di mesin lambat,
        dan itu memang seharusnya: yang ditanya "apakah ini lebih lama dari
        biasanya", bukan "apakah ini lebih lama dari angka yang ditulis sekali".
        """
        cepat = self._loop_dengan_siklus(10.0, sejak=300.0)
        lambat = self._loop_dengan_siklus(120.0, sejak=300.0)

        async with _alive(cepat):
            vonis_cepat = await UpkeepCheck(cepat, background=True).check()
        async with _alive(lambat):
            vonis_lambat = await UpkeepCheck(lambat, background=True).check()

        assert vonis_cepat.status is HealthStatus.DOWN
        assert vonis_lambat.status is not HealthStatus.DOWN

    async def test_sebelum_ada_siklus_selesai_anggaran_jatuh_ke_tick(self) -> None:
        """Belum terukur bukan nol. Tanpa satu pun durasi tersimpan, anggaran
        lama yang dipakai - bukan anggaran nol yang menyebut semuanya macet."""
        loop = _loop(_Refresher(), _Resolver(), tick_sec=15.0)
        loop.stats.cycles = 1
        loop.stats.candles = 10
        loop.stats.cycle_seconds = []
        loop.stats.last_cycle_at = now_utc() - timedelta(seconds=90)
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()

        assert health.status is HealthStatus.DOWN
        assert "macet" in health.message


class TestNolTidakDiucapkanSebagaiCacat:
    """Rule C: a nought that means "nothing wrong" must never be announced as
    a finding.

    The sentence about unscoreable signals used to be *gated* on a delta over
    the cumulative observation counter while *printing* the per-pass signal
    count. Two different measures on one sentence, and they came apart whenever
    more than one resolve pass fell between two sweeps that reached the block -
    which the "loop macet" early return guarantees, because it freezes the
    delta mark during an incident and thaws it wrong on recovery. The message
    went to Telegram reading "0 sinyal tidak punya interval yang bisa disampel
    - antrean itu tidak akan terkuras sendiri", about a queue that had drained.
    """

    @staticmethod
    def _loop_with(**overrides):
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()
        for key, value in overrides.items():
            setattr(loop.stats, key, value)
        return loop

    async def test_nol_pada_putaran_terakhir_tidak_dilaporkan(self) -> None:
        loop = self._loop_with(
            unavailable_interval=7,  # cumulative: seven observations, historical
            last_unavailable_interval=0,  # the queue drained
            resolved=1,
        )
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert "0 sinyal tidak punya interval" not in health.message

    async def test_antrean_yang_benar_benar_macet_tetap_dilaporkan(self) -> None:
        """The pair, so the test above cannot pass by silencing everything."""
        loop = self._loop_with(unavailable_interval=36, last_unavailable_interval=3)
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DEGRADED
        assert "3 sinyal tidak punya interval" in health.message
        assert "36" not in health.message

    async def test_tetap_dilaporkan_pada_sapuan_berikutnya(self) -> None:
        """Level-triggered, not edge: a queue that is still stuck is still
        stuck on the second sweep, and a delta-gated version fell silent."""
        loop = self._loop_with(unavailable_interval=36, last_unavailable_interval=3)
        check = UpkeepCheck(loop, background=True)
        async with _alive(loop):
            await check.check()
            second = await check.check()
        assert "3 sinyal tidak punya interval" in second.message


class TestSebabDilewatiDisebutBenar:
    """SPEC 49. An empty gate has two causes and they need opposite responses.

    These did not exist, and their absence was the finding: the whole clause
    could be deleted while 150 tests stayed green.
    """

    @staticmethod
    def _down(**stats_overrides):
        loop = _loop(_Refresher(), _Resolver())
        loop.stats.cycles = 10
        loop.stats.refresh_failures = 4
        loop.stats.candles = 0
        loop.stats.last_cycle_at = now_utc()
        for key, value in stats_overrides.items():
            setattr(loop.stats, key, value)
        return loop

    async def test_bursa_tutup_disebut(self) -> None:
        loop = self._down(last_skipped_markets=["IDX"])
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DOWN
        assert "IDX dilewati karena bursa tutup" in health.message

    async def test_tanpa_market_yang_dilewati_tidak_menyalahkan_bursa(self) -> None:
        """Without the pair, the test above could pass for the wrong reason."""
        loop = self._down()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DOWN
        assert "bursa tutup" not in health.message

    async def test_salah_konfigurasi_bukan_bursa_tutup(self) -> None:
        """The reproduction: a 24/7 market announced as a shut exchange."""
        loop = self._down(last_skipped_no_intervals=["CRYPTO"])
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert "CRYPTO dilewati karena bursa tutup" not in health.message
        assert "tidak ada interval yang disegarkan" in health.message
        assert "ARUNA_UPKEEP_CANDLE_INTERVALS" in health.message

    async def test_gerbang_kosong_karena_konfigurasi_tidak_disebut_bursa_tutup(
        self,
    ) -> None:
        """End to end through the real refresh(): a market whose refresh set is
        empty must reach health as a configuration fault, never as a shut
        exchange."""
        import sys

        sys.path.insert(0, "tests")
        from aruna.core.enums import Market
        from test_upkeep import _Ingest, _Ingestor, _refresher

        # CRYPTO is 24/7, so an empty gate here can only be configuration.
        # Emptied at the derivation rather than by guessing an interval this
        # fake provider happens not to serve - the branch under test is "the
        # refresh set came out empty", however it got that way.
        refresher = _refresher(_Ingest(_Ingestor(Market.CRYPTO)))
        refresher.intervals_for = lambda market: ()  # type: ignore[method-assign]
        result = await refresher.refresh(now=now_utc())

        assert Market.CRYPTO not in result.skipped_closed
        assert Market.CRYPTO in result.skipped_no_intervals

    async def test_interval_yang_gagal_berturut_turut_disebut_namanya(self) -> None:
        schedule = {
            "CRYPTO:1m": {"consecutive_failures": 9, "deferrals": 0},
            "CRYPTO:1h": {"consecutive_failures": 0, "deferrals": 0},
        }
        loop = _loop(_Refresher(schedule=schedule), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DEGRADED
        assert "CRYPTO:1m (9x)" in health.message
        assert "CRYPTO:1h" not in health.message

    async def test_semua_interval_gagal_dilaporkan_down(self) -> None:
        schedule = {
            "CRYPTO:1m": {"consecutive_failures": 5, "deferrals": 0},
            "CRYPTO:1h": {"consecutive_failures": 4, "deferrals": 0},
        }
        loop = _loop(_Refresher(schedule=schedule), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DOWN
        assert "tidak satu pun interval berhasil disegarkan" in health.message

    async def test_interval_yang_selalu_ditunda_disebut(self) -> None:
        """"Ditunda, bukan dibuang" is true for a cycle or two and false for
        ever after, and only the log said so."""
        schedule = {"IDX:15m": {"consecutive_failures": 0, "deferrals": 37}}
        loop = _loop(_Refresher(schedule=schedule), _Resolver())
        loop.stats.cycles = 40
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DEGRADED
        assert "IDX:15m (37x)" in health.message

    async def test_jadwal_tanpa_kunci_yang_diharapkan_tidak_meledak(self) -> None:
        """``state()`` is details written by another module; a missing key must
        cost silence, never the probe itself."""
        loop = _loop(_Refresher(schedule={"CRYPTO:1m": {}}), _Resolver())
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.UP


class TestSebabYangBenar:
    async def test_loop_tidak_terpasang_tidak_mengarang_sebab(self) -> None:
        """``self.upkeep`` is also None when startup found no market data
        provider, and that operator never set the env var they were blamed for
        (SPEC 49)."""
        health = await UpkeepCheck(None, background=True).check()
        assert health.status is HealthStatus.UP
        assert "tidak ada provider market data" in health.message
        assert "atau ARUNA_UPKEEP_ENABLED=false" in health.message

    async def test_saklar_yang_benar_benar_dimatikan_disebut_apa_adanya(self) -> None:
        loop = _loop(_Refresher(), _Resolver(), enabled=False)
        health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.UP
        assert health.message.startswith("dinonaktifkan lewat ARUNA_UPKEEP_ENABLED=false")

    async def test_resolusi_dimatikan_bukan_nol_sinyal_dinilai(self) -> None:
        """Zero that means "nobody asked" presented as zero that means
        "asked, nothing found"."""
        loop = _loop(_Refresher(), _Resolver(), resolve_enabled=False)
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.UP
        assert "ARUNA_UPKEEP_RESOLVE_ENABLED=false" in health.message
        assert "0 sinyal dinilai" not in health.message

    async def test_resolusi_dimatikan_tetap_disebut_saat_ada_masalah_lain(self) -> None:
        loop = _loop(_Refresher(), _Resolver(), resolve_enabled=False)
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.refresh_failures = 2
        loop.stats.last_cycle_at = now_utc()
        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True).check()
        assert health.status is HealthStatus.DEGRADED
        assert "ARUNA_UPKEEP_RESOLVE_ENABLED=false" in health.message

    async def test_backlog_permanen_tidak_dijanjikan_terkuras(self) -> None:
        """"Perlu beberapa putaran untuk dikuras" is a claim about the future,
        and for an unscoreable horizon it is a false one."""
        loop = _loop(_Refresher(), _Resolver(), resolve_limit=100)
        loop.stats.cycles = 10
        loop.stats.candles = 400
        # Ten passes over the same 118 unscoreable predictions. The run total
        # is 1180 observations; the queue is 118 predictions. The sentence
        # below says "N of the 118 due", so it has to read the queue.
        loop.stats.unavailable_interval = 1180
        loop.stats.last_unavailable_interval = 118
        loop.stats.resolve_pass_seen = True
        loop.stats.last_cycle_at = now_utc()

        async def due() -> int:
            return 118

        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True, due_count=due).check()
        assert health.status is HealthStatus.DEGRADED
        assert "tidak akan terkuras tanpa tindakan" in health.message
        assert "perlu beberapa putaran untuk dikuras" not in health.message
        # "118 sinyal jatuh tempo ... 118 di antaranya" - both counts are
        # predictions. The run total would have said 1180 of 118.
        assert "118 sinyal jatuh tempo" in health.message
        assert "1180" not in health.message

    async def test_backlog_yang_memang_akan_terkuras_dikatakan_begitu(self) -> None:
        loop = _loop(_Refresher(), _Resolver(), resolve_limit=100)
        loop.stats.cycles = 10
        loop.stats.candles = 400
        loop.stats.last_cycle_at = now_utc()

        async def due() -> int:
            return 118

        async with _alive(loop):
            health = await UpkeepCheck(loop, background=True, due_count=due).check()
        assert health.status is HealthStatus.DEGRADED
        assert "perlu beberapa putaran untuk dikuras" in health.message


# ---------------------------------------------------------------------------
# what the operator actually reads
# ---------------------------------------------------------------------------


class TestRingkasanUpkeep:
    async def test_ringkasan_menyebut_kegagalan_refresh(self) -> None:
        """The only failure figure on the line an operator reads was the one
        counter that stays at zero when everything fails."""
        loop = _loop(
            _Refresher(error=ConnectionError("provider unreachable")),
            _Resolver(),
            resolve_enabled=False,
        )
        for _ in range(50):
            await loop.cycle()
        text = loop.stats.summary()
        assert "50 refresh gagal" in text
        assert "0 siklus gagal" in text

    def test_ringkasan_menyebut_resolusi_yang_dimatikan(self) -> None:
        stats = UpkeepStats(started_at=now_utc(), cycles=2, resolve_enabled=False)
        text = stats.summary()
        assert "resolusi dimatikan lewat ARUNA_UPKEEP_RESOLVE_ENABLED=false" in text
        assert "0 sinyal dinilai" not in text

    async def test_ringkasan_menyebut_market_yang_dilewati(self) -> None:
        """Otherwise "0 candle disegarkan" is a nought with no reason attached,
        and "nothing was owed" reads the same as "nothing was asked"."""
        result = RefreshResult(skipped_closed=[Market.IDX])
        loop = _loop(_Refresher(result=result), _Resolver(), resolve_enabled=False)
        await loop.cycle()
        assert "IDX" in loop.stats.summary()
        assert "bursa tutup" in loop.stats.summary()

    async def test_ringkasan_menyebut_interval_yang_ditunda(self) -> None:
        result = RefreshResult(deferred=[(Market.IDX, Horizon.M15)])
        loop = _loop(_Refresher(result=result), _Resolver(), resolve_enabled=False)
        await loop.cycle()
        assert "1 interval ditunda karena batas request" in loop.stats.summary()

    async def test_teks_operator_berbahasa_indonesia_dan_bersih(self) -> None:
        loop = _loop(
            _Refresher(error=ConnectionError("provider unreachable")),
            _Resolver(_Resolved(unavailable_interval=100)),
            resolve_limit=100,
        )
        await loop.cycle()
        texts = [loop.stats.summary()]
        async with _alive(loop):
            texts.append((await UpkeepCheck(loop, background=True).check()).message)
        texts.append((await UpkeepCheck(None, background=True).check()).message)
        store = _Store({(1, "1h"): now_utc() - timedelta(days=9)})
        check = CandleFreshnessCheck(
            store,  # type: ignore[arg-type]
            market=Market.CRYPTO,
            intervals=(Horizon.H1,),
        )
        texts.append((await check.check()).message)
        for text in texts:
            assert text
            assert find_forbidden(text) == ()


# ---------------------------------------------------------------------------
# resolution accounting and shutdown
# ---------------------------------------------------------------------------


class TestResolusiYangTidakAkanPernahSelesai:
    async def test_batch_unavailable_dihitung_tersumbat(self) -> None:
        """The one unscoreable category that never clears on its own read as an
        idle system: awaiting=0, no_prices=0, resolved=0, no warning."""
        loop = _loop(
            _Refresher(),
            _Resolver(_Resolved(unavailable_interval=100)),
            resolve_limit=100,
            resolve_interval_sec=1,
        )
        base = now_utc()
        for index in range(10):
            await loop.cycle(now=base + timedelta(seconds=60 * index))
        # 1000 is a true count of *observations* - the same 100 predictions
        # re-read ten times - and a false count of predictions. It stays as a
        # measure of wasted work...
        assert loop.stats.unavailable_interval == 1000
        assert loop.stats.clogged_passes == 10
        # ...but the line the operator reads is about the queue, so it has to
        # be the queue: 100 predictions, not 1000. Saying 1000 here is how
        # "10 sinyal jatuh tempo ... 36 di antaranya" got written.
        summary = loop.stats.summary()
        assert "100 tanpa interval yang bisa disampel" in summary
        assert "1000 tanpa interval" not in summary

    async def test_sumbatan_menyebut_isi_antreannya(self) -> None:
        """"Tersumbat" alone cannot tell "wait" apart from "act".

        Since the freshness guard started holding stale series back rather than
        scoring them from one close, a queue waiting for candles to arrive
        drives ``clogged_passes`` up exactly like a queue that will never
        clear. The first fixes itself when the refresher catches up; the second
        never does. The counters are already separate - the message has to be
        too.
        """
        loop = _loop(
            _Refresher(),
            _Resolver(_Resolved(awaiting_candles=90, unavailable_interval=10)),
            resolve_limit=100,
            resolve_interval_sec=1,
        )
        base = now_utc()
        async with _alive(loop):
            for index in range(3):
                await loop.cycle(now=base + timedelta(seconds=60 * index))
            health = await UpkeepCheck(loop, background=True).check()

        assert health.status is HealthStatus.DEGRADED
        assert "3 putaran resolusi tersumbat" in health.message
        # The queue holds 90 + 10, whatever the pass count. 270 and 30 are the
        # same predictions counted three times; printed next to "3 putaran"
        # they read as a queue growing while nothing has changed.
        assert "90 menunggu candle menyusul" in health.message
        assert "10 tanpa interval yang bisa disampel" in health.message
        assert "270" not in health.message
        assert find_forbidden(health.message) == ()


class TestPenghentianYangTidakMemotongTulisan:
    async def test_stop_menunggu_siklus_selesai(self) -> None:
        """``_resolve_one`` writes samples, outcome, status and paper trade one
        after another. Cancelling between two of them leaves a signal nothing
        retries and SPEC 22 forbids editing - one corrupted prediction per
        SIGINT, at the head of the queue."""
        steps: list[str] = []

        class _FourWrites:
            async def resolve_due(self, *, reference: datetime, limit: int) -> _Resolved:
                for step in (
                    "record_samples",
                    "record_outcome",
                    "set_status",
                    "record_trade",
                ):
                    await asyncio.sleep(0.02)
                    steps.append(step)
                return _Resolved(resolved=1)

        loop = _loop(_Refresher(), _FourWrites(), tick_sec=0.01)
        await loop.start()
        await asyncio.sleep(0.05)  # mid-write
        await loop.stop()

        assert steps[:4] == [
            "record_samples",
            "record_outcome",
            "set_status",
            "record_trade",
        ]
        assert loop.stats.resolved >= 1
        assert loop.running is False

    async def test_stop_tetap_memaksa_kalau_tenggang_habis(self) -> None:
        """The cancel is still there for a cycle wedged on a socket that never
        times out. A shutdown that hangs for ever is its own failure."""

        class _Wedged:
            async def resolve_due(self, *, reference: datetime, limit: int) -> _Resolved:
                await asyncio.sleep(3600)
                return _Resolved()

        loop = _loop(_Refresher(), _Wedged(), tick_sec=0.01)
        await loop.start()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(loop.stop(grace_sec=0.05), timeout=5)
        assert loop.running is False
