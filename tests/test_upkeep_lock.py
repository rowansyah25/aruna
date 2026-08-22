"""Penguncian prediksi otomatis (SPEC 10, 20, 46).

Sebelum ini ada, `lock_signals` punya TEPAT SATU pemanggil di seluruh repo:
perintah CLI `aruna signal`. Loop upkeep menyegarkan candle dan menilai sinyal
jatuh tempo dengan sempurna - lalu kehabisan pekerjaan, karena tidak ada yang
membuat prediksi baru. Diukur pada database saat itu: sinyal terakhir dikunci
37 jam sebelumnya, oleh tangan; 88 yang tersisa semuanya IDX NO_SIGNAL yang
horizonnya tidak memuat satu bar pun; dan catatan paper trade berhenti di
sembilan, seluruhnya dari satu sore.

Ini bentuk lain cacat yang sama: bukan kode yang tak tercapai, melainkan tahap
yang tak pernah dijadwalkan.

**Tidak ada yang dieksekusi di sini.** Prediksi yang dikunci adalah catatan
kertas (SPEC 46): tidak ada order, tidak ada dana yang berpindah.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from aruna.core.config import UpkeepSettings
from aruna.core.enums import Horizon, Market
from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

MONDAY = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)


def _empty_resolve() -> SimpleNamespace:
    """A ``ResolveResult`` shaped answer with nothing in it."""
    return SimpleNamespace(
        resolved=0,
        awaiting_candles=0,
        no_prices=0,
        unavailable_interval=0,
        failures=[],
    )


@dataclass(slots=True)
class _Locked:
    """Berbentuk ``LockResult`` - loop membacanya lewat atribut."""

    locked: int = 0
    recorded_non_directional: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)


class _Locker:
    """Berdiri di tempat ``SignalService``; hanya ``lock_signals`` dipanggil."""

    def __init__(
        self,
        result: _Locked | None = None,
        *,
        error: Exception | None = None,
        log: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[Market, tuple[Horizon, ...]]] = []
        self.result = result or _Locked(locked=1)
        self.error = error
        self.log = log

    async def lock_signals(
        self, market: Market, horizons: tuple[Horizon, ...], **_: object
    ) -> _Locked:
        if self.log is not None:
            self.log.append("lock")
        self.calls.append((market, tuple(horizons)))
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def horizons_seen(self) -> list[str]:
        return [h.value for _, hs in self.calls for h in hs]


#: Candle segar untuk setiap pasangan, yaitu keadaan SEHAT produksi.
#:
#: Penguncian menunggu candle bar itu benar-benar tiba (lihat
#: `UpkeepLoop._bukti_siap`): terukur 2026-08-21, kunci menyala 18:00:15
#: sementara bar 15m baru tiba 18:00:32, dan keputusan di atas bukti satu bar
#: lalu berkinerja -4,9 poin melawan +7,2 poin untuk bar terbaru.
#:
#: `refreshed=[]` berarti "tidak ada yang perlu diambil" - keadaan saat
#: gerbangnya MEMANG harus menutup. Test di berkas ini menguji cadence
#: penguncian, bukan kesegaran candle, jadi palsunya melaporkan segar.
SEMUA_SEGAR = [(m, h) for m in Market for h in Horizon]


class _Refresher:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log

    async def refresh(self, *, now: datetime) -> object:
        if self.log is not None:
            self.log.append("refresh")
        return SimpleNamespace(
            candles=0,
            requests=0,
            refreshed=SEMUA_SEGAR,
            deferred=[],
            failures=[],
            skipped_closed=[],
            skipped_no_intervals=[],
        )

    def state(self) -> dict:
        return {}


class _Resolver:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log

    async def resolve_due(self, *, reference: datetime, limit: int) -> object:
        if self.log is not None:
            self.log.append("resolve")
        return _empty_resolve()


def _loop(locker: object | None = None, **overrides: object) -> UpkeepLoop:
    settings = UpkeepSettings(**overrides)  # type: ignore[arg-type]
    return UpkeepLoop(
        refresher=_Refresher(),  # type: ignore[arg-type]
        resolver=_Resolver(),
        locker=locker,
        settings=settings,
        stats=UpkeepStats(started_at=MONDAY),
    )


class TestSatuPrediksiPerHorizonPerBar:
    """Cadence-nya adalah panjang bar itu sendiri, bukan sebuah timer.

    Ini yang membuat pengamatannya saling bebas. Sembilan paper trade yang ada
    di catatan adalah sembilan kekalahan dan SATU pengamatan - semuanya SELL,
    satu sore, tiga aset berkorelasi, horizon tumpang tindih. Mengunci lebih
    rapat daripada bar akan melipatgandakan baris tanpa melipatgandakan bukti.
    """

    async def test_bar_yang_sama_hanya_dikunci_sekali(self) -> None:
        locker = _Locker()
        loop = _loop(locker, lock_horizons="15m")
        for minute in (0, 1, 5, 14):
            await loop.cycle(now=MONDAY + timedelta(minutes=minute))
        assert locker.horizons_seen == ["15m"], locker.horizons_seen

    async def test_bar_berikutnya_dikunci_lagi(self) -> None:
        locker = _Locker()
        loop = _loop(locker, lock_horizons="15m")
        await loop.cycle(now=MONDAY)
        await loop.cycle(now=MONDAY + timedelta(minutes=15))
        await loop.cycle(now=MONDAY + timedelta(minutes=30))
        assert locker.horizons_seen == ["15m", "15m", "15m"]

    async def test_tiap_horizon_punya_cadence_sendiri(self) -> None:
        """Enam jam: 15m harus jauh lebih sering daripada 1d, dan 1d sekali."""
        locker = _Locker()
        loop = _loop(locker, lock_horizons="15m,1h,1d")
        for minute in range(0, 360, 5):
            await loop.cycle(now=MONDAY + timedelta(minutes=minute))
        seen = locker.horizons_seen
        assert seen.count("15m") == 24, seen.count("15m")
        assert seen.count("1h") == 6, seen.count("1h")
        assert seen.count("1d") == 1, seen.count("1d")

    async def test_hanya_horizon_yang_dikonfigurasi(self) -> None:
        locker = _Locker()
        loop = _loop(locker, lock_horizons="1h")
        for minute in range(0, 180, 5):
            await loop.cycle(now=MONDAY + timedelta(minutes=minute))
        assert set(locker.horizons_seen) == {"1h"}

    async def test_hanya_market_yang_dikonfigurasi(self) -> None:
        locker = _Locker()
        loop = _loop(locker, lock_markets="CRYPTO")
        await loop.cycle(now=MONDAY)
        assert {market for market, _ in locker.calls} == {Market.CRYPTO}


class TestUrutanDalamSatuSiklus:
    async def test_penguncian_datang_setelah_resolusi(self) -> None:
        """Menilai mengosongkan antrean; mengunci menambahnya.

        Tick yang kehabisan waktu di tengah harus meninggalkan pekerjaan lebih
        sedikit daripada yang ditemukannya, bukan lebih banyak - alasan yang
        sama dipakai ``FuturesScheduler.tick`` untuk menilai sebelum
        merencanakan. Urutannya juga berarti prediksi dibuat terhadap candle
        yang baru saja disegarkan siklus ini.
        """
        order: list[str] = []
        loop = UpkeepLoop(
            refresher=_Refresher(order),  # type: ignore[arg-type]
            resolver=_Resolver(order),
            locker=_Locker(log=order),
            settings=UpkeepSettings(),
            stats=UpkeepStats(started_at=MONDAY),
        )
        await loop.cycle(now=MONDAY)
        assert order == ["refresh", "resolve", "lock"], order


class TestKegagalanTidakMenelanBar:
    async def test_penguncian_yang_gagal_dicoba_lagi_tick_berikutnya(self) -> None:
        """Menandai bar sebelum berhasil akan membuat satu venue yang tak
        terjangkau membiayai seluruh bar itu."""
        locker = _Locker(error=ConnectionError("venue unreachable"))
        loop = _loop(locker, lock_horizons="1h")
        await loop.cycle(now=MONDAY)
        await loop.cycle(now=MONDAY + timedelta(minutes=1))
        assert len(locker.calls) == 2
        assert loop.stats.lock_failures == 2

    async def test_penguncian_yang_gagal_tidak_mengakhiri_siklus(self) -> None:
        locker = _Locker(error=ConnectionError("venue unreachable"))
        loop = _loop(locker)
        stats = await loop.cycle(now=MONDAY)
        assert stats.cycles == 1
        assert stats.failed_cycles == 0

    async def test_kegagalan_per_horizon_dihitung(self) -> None:
        locker = _Locker(_Locked(locked=1, failures=["BTC/USDT 1h: stale feed"]))
        loop = _loop(locker, lock_horizons="1h")
        await loop.cycle(now=MONDAY)
        assert loop.stats.lock_failures == 1
        assert any("stale feed" in e for e in loop.stats.errors)


class TestApaYangDikatakanKeOperator:
    async def test_berarah_dan_wait_dihitung_terpisah(self) -> None:
        """Hanya yang berarah bisa jadi paper trade. Satu putaran yang isinya
        WAIT semua harus terbaca begitu, bukan sebagai sampel yang tumbuh."""
        locker = _Locker(_Locked(locked=0, recorded_non_directional=15))
        loop = _loop(locker, lock_horizons="1h")
        await loop.cycle(now=MONDAY)
        assert loop.stats.locked == 0
        assert loop.stats.locked_non_directional == 15
        text = loop.stats.summary()
        assert "0 prediksi berarah dikunci" in text
        assert "15 WAIT/NO_SIGNAL dicatat" in text

    async def test_penguncian_mati_dikatakan_bukan_dilaporkan_nol(self) -> None:
        """Nol yang berarti "tidak ada" dan nol yang berarti "tidak pernah
        ditanya" terlihat sama di layar (SPEC 49)."""
        loop = _loop(_Locker(), lock_enabled=False)
        await loop.cycle(now=MONDAY)
        text = loop.stats.summary()
        assert "penguncian dimatikan lewat ARUNA_UPKEEP_LOCK_ENABLED=false" in text
        assert "prediksi berarah dikunci" not in text

    async def test_tanpa_locker_juga_bukan_nol_yang_bohong(self) -> None:
        loop = _loop(None)
        await loop.cycle(now=MONDAY)
        assert loop.stats.lock_enabled is False
        assert "penguncian dimatikan" in loop.stats.summary()

    async def test_kegagalan_penguncian_muncul_di_ringkasan(self) -> None:
        locker = _Locker(error=ConnectionError("venue unreachable"))
        loop = _loop(locker, lock_horizons="1h")
        await loop.cycle(now=MONDAY)
        assert "1 penguncian gagal" in loop.stats.summary()


class TestKabelKeAppStartup:
    """Kabelnya sendiri. Potong, dan test ini merah - itu satu-satunya tugasnya.

    Repo ini berkali-kali mengirim komponen yang ditulis, di-export, di-test
    unit, dan tidak pernah dijangkau proses hidup. Menguji fase penguncian
    secara terpisah saja akan mengulang persis kesalahan itu - dan penguncian
    adalah tahap yang KEHILANGAN kabelnya selama ini.
    """

    async def test_startup_background_benar_benar_mengunci_prediksi(self) -> None:
        import sys

        sys.path.insert(0, "tests")
        from test_upkeep import _Ingest, _Ingestor, _wait_until, _wired_app

        class _ResolverYangJugaMengunci(_Locker):
            """`app.py` mengoper `self.signals` sebagai resolver DAN locker -
            satu service, dua bagian pekerjaannya."""

            async def resolve_due(self, *, reference: datetime, limit: int) -> object:
                return _empty_resolve()

        signals = _ResolverYangJugaMengunci(_Locked(locked=3))
        app = _wired_app(
            _Ingest(_Ingestor(Market.CRYPTO)),
            signals,
            tick_sec=0.01,
            candle_settle_sec=0.0,
        )

        await app.startup(background=True)
        try:
            # Menunggu SELURUH horizon terkunci, lintas siklus - bukan satu
            # siklus yang mengerjakan semuanya.
            #
            # `BATAS_KUNCI_PER_SIKLUS` sengaja menyebar penguncian antar siklus:
            # saat proses menyala, `_locked_bar` kosong sehingga tiap pasangan
            # jatuh tempo sekaligus, dan terukur 2026-08-22 siklus pertama tidak
            # selesai selama lima menit. Menuntut satu panggilan memuat semua
            # horizon berarti menuntut kembalinya tumpukan itu.
            #
            # Yang dijaga kelas ini tetap utuh, dan bahkan lebih kuat: dulu
            # cukup satu panggilan; sekarang antreannya harus benar-benar
            # terkuras sampai habis.
            semua = set(app.settings.upkeep.lock_horizon_set)
            await _wait_until(
                lambda: {h for _, hs in signals.calls for h in hs} >= semua,
                what="seluruh horizon terkunci lintas siklus",
            )
            assert app.upkeep is not None
            market, horizons = signals.calls[0]
            assert market is Market.CRYPTO
            assert set(horizons) <= semua
            assert app.upkeep.stats.locked >= 3
        finally:
            await app.shutdown()

    async def test_startup_background_false_tidak_mengunci_apa_pun(self) -> None:
        """Aturan A. Perintah CLI pendek tidak boleh diam-diam membuat prediksi."""
        import asyncio
        import sys

        sys.path.insert(0, "tests")
        from test_upkeep import _Ingest, _Ingestor, _wired_app

        signals = _Locker()

        async def _resolve_due(*, reference: datetime, limit: int) -> object:
            raise AssertionError("resolusi tidak boleh jalan di background=False")

        signals.resolve_due = _resolve_due  # type: ignore[attr-defined]
        app = _wired_app(
            _Ingest(_Ingestor(Market.CRYPTO)),
            signals,
            tick_sec=0.01,
            candle_settle_sec=0.0,
        )

        await app.startup(background=False)
        try:
            await asyncio.sleep(0.1)
            assert app.upkeep is not None, "objeknya tetap dibangun untuk `aruna upkeep`"
            assert not app.upkeep.running
            assert signals.calls == []
        finally:
            await app.shutdown()

    def test_locker_dioper_di_startup(self) -> None:
        """Penjaga terhadap cara paling sunyi kabel ini bisa putus: argumen
        ``locker=`` hilang dari konstruktor, loop tetap jalan sempurna, dan
        tidak ada prediksi yang pernah dibuat lagi."""
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "locker=" in source, "UpkeepLoop dibangun tanpa locker"


class TestKonfigurasiDitolakLebihAwal:
    """Horizon salah ketik akan mengecilkan set penguncian diam-diam, dan
    gejalanya - lebih sedikit prediksi daripada yang diharapkan - baru
    ketahuan saat sampelnya dibutuhkan."""

    def test_horizon_tidak_dikenal_ditolak(self) -> None:
        with pytest.raises(ValueError, match="unknown horizon"):
            UpkeepSettings(lock_horizons="15m,7h")

    def test_market_tidak_dikenal_ditolak(self) -> None:
        with pytest.raises(ValueError, match="unknown market"):
            UpkeepSettings(lock_markets="CRYPTO,FOREX")

    def test_default_adalah_spec_10(self) -> None:
        settings = UpkeepSettings()
        assert [h.value for h in settings.lock_horizon_set] == ["15m", "1h", "1d"]
        assert [m.value for m in settings.lock_market_set] == ["CRYPTO"]
