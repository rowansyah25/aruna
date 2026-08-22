"""Pemindai di dalam loop upkeep, dan kabelnya ke app (PASAL 14, 15, 38).

Keputusan yang dikunci di sini bukan "pemindai bekerja" - itu diuji di
``test_scanner.py`` - melainkan **pemindai tidak menggerakkan council**.

Itu pilihan, dan pilihan yang bisa dibatalkan seseorang tanpa sadar sedang
membatalkan apa. Kalau peristiwa mulai memicu penguncian, prediksi hanya lahir
saat pasar bergerak: sampelnya condong ke periode ramai, dan catatan
menang-kalah berhenti sebanding lintas waktu - padahal mengukur itulah guna
seluruh sistem ini. Test di kelas terakhir yang menjaganya.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from aruna.core.config import UpkeepSettings
from aruna.scanner import AnalysisQueue, EventKind, ScanResult, SignificantEvent
from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

MONDAY = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


def _event(symbol="BTC/USDT", kind=EventKind.BREAKOUT, severity=2.0, offset=0):
    return SignificantEvent(
        symbol=symbol,
        kind=kind,
        severity=severity,
        detail="test",
        at=MONDAY + timedelta(seconds=offset),
    )


class _Scanner:
    def __init__(self, results=None, *, error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.calls: list[datetime] = []

    async def scan(self, moment: datetime) -> list[ScanResult]:
        self.calls.append(moment)
        if self.error is not None:
            raise self.error
        return list(self.results)


class _Refresher:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log

    async def refresh(self, *, now: datetime) -> Any:
        if self.log is not None:
            self.log.append("refresh")
        # Segar untuk semua pasangan - lihat `UpkeepLoop._bukti_siap`. Test ini
        # menguji bahwa pemindai TIDAK menggerakkan council, bukan kesegaran
        # candle; `refreshed=[]` akan menutup gerbang penguncian dan membuat
        # testnya lulus karena alasan yang salah.
        from aruna.core.enums import Horizon, Market

        return SimpleNamespace(
            candles=0, requests=0,
            refreshed=[(m, h) for m in Market for h in Horizon],
            deferred=[], failures=[],
            skipped_closed=[], skipped_no_intervals=[],
        )

    def state(self) -> dict:
        return {}


class _Resolver:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log

    async def resolve_due(self, *, reference: datetime, limit: int) -> Any:
        if self.log is not None:
            self.log.append("resolve")
        return SimpleNamespace(
            resolved=0, awaiting_candles=0, no_prices=0,
            unavailable_interval=0, failures=[],
        )


class _Locker:
    def __init__(self, log: list[str] | None = None) -> None:
        self.calls: list[tuple] = []
        self.log = log

    async def lock_signals(self, market, horizons, **_: object) -> Any:
        if self.log is not None:
            self.log.append("lock")
        self.calls.append((market, tuple(horizons)))
        return SimpleNamespace(
            locked=0, recorded_non_directional=0, skipped=0, failures=[]
        )


def _loop(scanner=None, *, queue=None, locker=None, log=None, **overrides):
    return UpkeepLoop(
        refresher=_Refresher(log),  # type: ignore[arg-type]
        resolver=_Resolver(log),
        locker=locker,
        scanner=scanner,
        queue=queue,
        settings=UpkeepSettings(**overrides),  # type: ignore[arg-type]
        stats=UpkeepStats(started_at=MONDAY),
    )


class TestPemindaiBerjalanDalamSiklus:
    async def test_peristiwa_masuk_antrean(self) -> None:
        queue = AnalysisQueue()
        scanner = _Scanner([
            ScanResult("BTC/USDT", (_event(),), usable_bars=59, scanned=True)
        ])
        loop = _loop(scanner, queue=queue)

        await loop.cycle(now=MONDAY)

        assert len(queue) == 1
        assert loop.stats.events == 1
        assert loop.stats.scanned == 1

    async def test_dipindai_dan_tidak_bisa_dipindai_dihitung_terpisah(self) -> None:
        """Keduanya menghasilkan nol peristiwa. Menyatukannya membuat riwayat
        bar yang terlalu pendek terbaca sebagai pasar yang tenang (SPEC 4)."""
        scanner = _Scanner([
            ScanResult("BTC/USDT", (), usable_bars=59, scanned=True),
            ScanResult("ETH/USDT", (), usable_bars=3, scanned=False, reason="kurang"),
        ])
        loop = _loop(scanner)

        await loop.cycle(now=MONDAY)

        assert loop.stats.scanned == 1
        assert loop.stats.unscannable == 1
        assert "1 dipindai" in loop.stats.summary()
        assert "1 bar belum cukup" in loop.stats.summary()

    async def test_memindai_setelah_refresh(self) -> None:
        """Bar yang baru disimpan siklus ini harus terbaca siklus ini juga -
        memindai lebih dulu berarti selalu membaca keadaan satu siklus lalu."""
        order: list[str] = []
        loop = _loop(_Scanner(), log=order, locker=_Locker(order))
        loop._scanner.scan = _tracked(loop._scanner, order)  # type: ignore[method-assign]

        await loop.cycle(now=MONDAY)

        assert order.index("refresh") < order.index("scan")

    async def test_pemindaian_gagal_tidak_mengakhiri_siklus(self) -> None:
        loop = _loop(_Scanner(error=ConnectionError("db down")))
        stats = await loop.cycle(now=MONDAY)

        assert stats.cycles == 1
        assert stats.failed_cycles == 0
        assert stats.scan_failures == 1
        assert any("scan:" in e for e in stats.errors)

    async def test_tanpa_pemindai_siklus_tetap_jalan(self) -> None:
        loop = _loop(None)
        stats = await loop.cycle(now=MONDAY)
        assert stats.cycles == 1
        assert stats.scanned == 0


class TestPemindaiTidakMenggerakkanCouncil:
    """Keputusan desain, dikunci supaya tidak bisa dibatalkan tanpa sadar.

    Kalau ini merah karena seseorang menyambungkan peristiwa ke penguncian,
    yang berubah bukan sekadar kabel: prediksi berhenti lahir pada cadence bar
    yang tetap, dan sampel yang selama ini independen jadi condong ke periode
    ramai. Keputusan itu boleh diambil - tapi harus diambil dengan sadar, dan
    ini yang memaksanya sadar.
    """

    async def test_peristiwa_tidak_menambah_penguncian(self) -> None:
        locker = _Locker()
        ramai = _Scanner([
            ScanResult("BTC/USDT", tuple(
                _event(kind=k, severity=99.0) for k in
                (EventKind.BREAKOUT, EventKind.VOLUME_SPIKE, EventKind.PRICE_MOVE)
            ), usable_bars=59, scanned=True)
        ])
        loop = _loop(ramai, locker=locker, lock_horizons="1h")

        await loop.cycle(now=MONDAY)
        sesudah_ramai = len(locker.calls)

        sepi = _Scanner([ScanResult("BTC/USDT", (), usable_bars=59, scanned=True)])
        loop2 = _loop(sepi, locker=_Locker(), lock_horizons="1h")
        await loop2.cycle(now=MONDAY)

        # Pasar ramai dan pasar sepi menghasilkan jumlah penguncian yang sama:
        # cadence bar yang memutuskan, bukan peristiwa.
        assert sesudah_ramai == len(loop2._locker.calls) == 1

    async def test_antrean_tidak_dikuras_oleh_penguncian(self) -> None:
        """Peristiwa tetap menunggu di antrean. Kalau penguncian mengurasnya,
        ia sedang memakainya - dan itu pintu yang test ini jaga."""
        queue = AnalysisQueue()
        scanner = _Scanner([
            ScanResult("BTC/USDT", (_event(),), usable_bars=59, scanned=True)
        ])
        loop = _loop(scanner, queue=queue, locker=_Locker(), lock_horizons="1h")

        await loop.cycle(now=MONDAY)

        assert len(queue) == 1, "penguncian menguras antrean pemindai"


class TestKabelKeApp:
    def test_scanner_dioper_ke_upkeep(self) -> None:
        """Cacat tertua repo ini: komponen yang hanya hidup di test."""
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "scanner=" in source, "UpkeepLoop dibangun tanpa scanner"
        assert "ScannerService(" in source


def _tracked(scanner: _Scanner, order: list[str]):
    original = scanner.scan

    async def wrapped(moment: datetime):
        order.append("scan")
        return await original(moment)

    return wrapped
