"""Penjaga proses dan berita berkala (PASAL 11, 37).

Dua cacat dari keluarga yang sama, ditutup bersama.

Berita: ``NewsService`` dibangun di ``app.py``, ditutup saat shutdown, dan
tidak pernah dijalankan di antaranya - tidak ada ``start`` di kelasnya dan
tidak ada pemanggil ``ingest`` selain CLI. Terukur saat ditemukan: 280 item,
terakhir diambil enam puluh jam sebelumnya, dan ``NewsAgent`` terus membacanya
sebagai konteks sekarang. Bukti yang hilang kelihatan; bukti basi tidak.

Penjaga: loop di dalam ARUNA tahan terhadap tick yang gagal, tapi tidak
terhadap proses yang mati - dan dari dalam proses, kematian tidak bisa
dilaporkan.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from aruna.core.config import UpkeepSettings
from aruna.supervisor import (
    CRITICAL_RESTARTS,
    HEALTHY_UPTIME_SEC,
    RESTART_MIN_SEC,
    AlreadyRunning,
    ChildSpec,
    Supervisor,
    default_children,
    single_instance,
)
from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

MONDAY = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# PASAL 11 - berita berkala
# ---------------------------------------------------------------------------


class _News:
    def __init__(self, *, stored: int = 3, error: Exception | None = None) -> None:
        self.calls = 0
        self.stored = stored
        self.error = error

    async def ingest(self) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            fetched=self.stored, stored=self.stored, duplicates=0,
            linked=0, failures=[],
        )


class _Refresher:
    async def refresh(self, *, now: datetime) -> Any:
        # Melaporkan segar untuk semua pasangan: penguncian menunggu candle bar
        # itu benar-benar tiba (`UpkeepLoop._bukti_siap`), dan `refreshed=[]`
        # berarti "tidak ada yang diambil" - keadaan saat gerbangnya memang
        # harus menutup. Test ini menguji URUTAN fase, bukan kesegaran candle.
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
    async def resolve_due(self, *, reference: datetime, limit: int) -> Any:
        return SimpleNamespace(
            resolved=0, awaiting_candles=0, no_prices=0,
            unavailable_interval=0, failures=[],
        )


def _loop(news=None, **overrides) -> UpkeepLoop:
    return UpkeepLoop(
        refresher=_Refresher(),  # type: ignore[arg-type]
        resolver=_Resolver(),
        news=news,
        settings=UpkeepSettings(**overrides),  # type: ignore[arg-type]
        stats=UpkeepStats(started_at=MONDAY),
    )


class TestBeritaDitarikBerkala:
    async def test_berita_masuk_pada_siklus_pertama(self) -> None:
        news = _News()
        loop = _loop(news)
        await loop.cycle(now=MONDAY)

        assert news.calls == 1
        assert loop.stats.news_items == 3

    async def test_cadence_dihormati(self) -> None:
        """Tiap tick akan membanjiri feed RSS; ambangnya yang mengatur."""
        news = _News()
        loop = _loop(news, news_interval_sec=300.0)

        await loop.cycle(now=MONDAY)
        await loop.cycle(now=MONDAY + timedelta(seconds=60))
        await loop.cycle(now=MONDAY + timedelta(seconds=299))
        assert news.calls == 1

        await loop.cycle(now=MONDAY + timedelta(seconds=300))
        assert news.calls == 2

    async def test_feed_yang_gagal_tetap_pada_cadence_nya(self) -> None:
        """Stempel dipasang pada PERCOBAAN. Kalau dipasang pada keberhasilan,
        feed yang mati akan dicoba ulang tiap tick."""
        news = _News(error=ConnectionError("rss unreachable"))
        loop = _loop(news, news_interval_sec=300.0)

        await loop.cycle(now=MONDAY)
        await loop.cycle(now=MONDAY + timedelta(seconds=60))

        assert news.calls == 1
        assert loop.stats.news_failures == 1

    async def test_berita_gagal_tidak_mengakhiri_siklus(self) -> None:
        loop = _loop(_News(error=ConnectionError("rss unreachable")))
        stats = await loop.cycle(now=MONDAY)

        assert stats.cycles == 1
        assert stats.failed_cycles == 0
        assert any("news:" in e for e in stats.errors)

    async def test_ditarik_sebelum_prediksi_dikunci(self) -> None:
        """Council membaca berita sebagai bukti. Menyegarkannya sesudah putusan
        berarti setiap council menimbang dunia satu siklus lalu."""
        order: list[str] = []

        class _TrackedNews(_News):
            async def ingest(self) -> Any:
                order.append("news")
                return await super().ingest()

        class _Locker:
            async def lock_signals(self, market, horizons, **_: object) -> Any:
                order.append("lock")
                return SimpleNamespace(
                    locked=0, recorded_non_directional=0, skipped=0, failures=[]
                )

        loop = UpkeepLoop(
            refresher=_Refresher(),  # type: ignore[arg-type]
            resolver=_Resolver(),
            news=_TrackedNews(),
            locker=_Locker(),
            settings=UpkeepSettings(lock_horizons="1h"),
            stats=UpkeepStats(started_at=MONDAY),
        )
        await loop.cycle(now=MONDAY)

        assert order.index("news") < order.index("lock"), order

    async def test_dimatikan_dikatakan_bukan_dilaporkan_nol(self) -> None:
        """Nol berita karena dimatikan dan nol karena tidak ada yang terbit
        terlihat sama di layar (SPEC 49)."""
        loop = _loop(_News(), news_enabled=False)
        await loop.cycle(now=MONDAY)

        assert "news dimatikan lewat ARUNA_UPKEEP_NEWS_ENABLED=false" in (
            loop.stats.summary()
        )

    async def test_percobaan_kosong_tetap_dicatat(self, capsys) -> None:
        """Feed sepi tidak boleh terlihat sama dengan fase yang mati.

        Ini terukur di lapangan: fase berita berjalan tepat waktu tiap 300
        detik selama sepuluh menit dan tidak meninggalkan satu pun baris log,
        karena semua 280 item sudah ada di database dan ``stored`` nol. Dari
        luar, sistem yang bekerja dan sistem yang diam terlihat identik -
        cacat yang sama dengan yang dibereskan fase ini, cuma naik satu lapis.
        """
        loop = _loop(_News(stored=0))
        await loop.cycle(now=MONDAY)

        # structlog menulis ke stdout, bukan lewat ``logging`` stdlib, jadi
        # ``caplog`` tidak akan pernah melihatnya - dan test yang menyadap
        # saluran yang salah selalu hijau, apa pun kodenya.
        keluaran = capsys.readouterr().out
        assert "upkeep.news" in keluaran, (
            "percobaan berita tanpa item baru tidak meninggalkan jejak apa pun"
        )
        assert "stored=0" in keluaran

    async def test_tanpa_news_service_juga_dikatakan(self) -> None:
        loop = _loop(None)
        await loop.cycle(now=MONDAY)
        assert loop.stats.news_enabled is False
        assert "news dimatikan" in loop.stats.summary()


class TestKabelBeritaKeApp:
    def test_news_dioper_ke_upkeep(self) -> None:
        """Cacat aslinya persis ini: dibangun, ditutup, tidak pernah dijalankan."""
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "news=self.news" in source, "UpkeepLoop dibangun tanpa news"


# ---------------------------------------------------------------------------
# PASAL 37 - penjaga proses
# ---------------------------------------------------------------------------


class _Sleeps:
    def __init__(self) -> None:
        self.values: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.values.append(seconds)
        await asyncio.sleep(0)


def _supervisor(exits: list[int | None], *, name: str = "anak") -> Supervisor:
    """Penjaga yang anaknya diganti daftar exit code - tanpa proses nyata."""
    sisa = list(exits)
    sup = Supervisor(children=[ChildSpec(name=name, args=["-c", "pass"])])

    async def runner(spec: ChildSpec) -> int | None:
        if not sisa:
            sup._stopping.set()
            return 0
        return sisa.pop(0)

    sup.runner = runner
    return sup


class TestMenyalakanUlangYangMati:
    async def test_anak_yang_mati_dinyalakan_lagi(self) -> None:
        sup = _supervisor([1, 1])
        await sup.run(sleep=_Sleeps())

        assert sup.state["anak"].restarts >= 2

    async def test_jeda_melebar(self) -> None:
        """Proses yang mati dalam dua detik lalu dinyalakan seketika adalah hot
        loop yang membakar CPU sambil terlihat seperti sistem yang hidup."""
        sleeps = _Sleeps()
        sup = _supervisor([1, 1, 1])
        await sup.run(sleep=sleeps)

        assert sleeps.values[:3] == [
            RESTART_MIN_SEC,
            RESTART_MIN_SEC * 2,
            RESTART_MIN_SEC * 4,
        ], sleeps.values

    async def test_gagal_berulang_jadi_critical(self) -> None:
        sup = _supervisor([1] * (CRITICAL_RESTARTS + 1))
        await sup.run(sleep=_Sleeps())

        assert sup.state["anak"].critical is True
        assert sup.state["anak"].consecutive >= CRITICAL_RESTARTS

    async def test_sekali_mati_belum_critical(self) -> None:
        """Penjaga yang berteriak pada kematian pertama jadi kebisingan, dan
        kebisingan diabaikan tepat saat teriakan sungguhan datang."""
        sup = _supervisor([1])
        await sup.run(sleep=_Sleeps())
        assert sup.state["anak"].critical is False

    async def test_ctrl_c_bukan_crash(self) -> None:
        """Ctrl-C sampai juga ke anaknya, jadi anaknya mati - dan penjaga
        melihat exit code bukan-nol persis seperti saat crash.

        Bedanya cuma satu: operator yang meminta. Penjaga yang tidak
        membedakannya akan mencatat kematian itu sebagai kegagalan, menunggu
        jeda restart, dan - kalau operator menekan Ctrl-C lima kali - berteriak
        CRITICAL tentang kerusakan yang tidak pernah ada.
        """
        sleeps = _Sleeps()
        sup = Supervisor(children=[ChildSpec(name="anak", args=["-c", "pass"])])

        async def killed_by_ctrl_c(spec: ChildSpec) -> int | None:
            sup._stopping.set()  # sinyal tiba selagi anak masih hidup
            return 1  # lalu anaknya mati karena sinyal itu

        sup.runner = killed_by_ctrl_c
        await sup.run(sleep=sleeps)

        state = sup.state["anak"]
        assert state.restarts == 0, "kematian yang diminta operator dihitung crash"
        assert state.consecutive == 0
        assert state.critical is False
        assert sleeps.values == [], "menunggu jeda restart padahal sudah diminta stop"


class TestApaYangDijaga:
    def test_dua_proses_dan_keduanya_analisis(self) -> None:
        children = default_children("BTCUSDT,ETHUSDT", hours=24.0)
        names = {c.name for c in children}
        assert names == {"aruna-run", "futures-loop"}

    def test_tidak_ada_perintah_eksekusi(self) -> None:
        """PASAL 41. Penjaga menyalakan ulang apa pun yang disuruh, jadi daftar
        inilah yang menentukan apa yang dijaga hidup."""
        children = default_children("BTCUSDT", hours=24.0)
        semua = " ".join(" ".join(c.args) for c in children)
        for terlarang in ("order", "trade", "withdraw", "transfer", "leverage-set"):
            assert terlarang not in semua, terlarang

    def test_simbol_diteruskan(self) -> None:
        children = default_children("XRPUSDT", hours=12.0)
        futures = next(c for c in children if c.name == "futures-loop")
        assert "XRPUSDT" in futures.args
        assert "12.0" in futures.args


class TestSatuArunaSaja:
    def test_yang_kedua_ditolak(self, tmp_path) -> None:
        lock = tmp_path / "aruna.lock"
        with (
            single_instance(lock),  # ARUNA pertama memegang kunci
            pytest.raises(AlreadyRunning),
            single_instance(lock),  # yang kedua harus ditolak, bukan ikut jalan
        ):
            pass

    def test_giliran_berikutnya_dapat(self, tmp_path) -> None:
        """Kunci OS dilepas begitu prosesnya selesai. Berkas PID tidak - dan
        mekanisme yang dipasang untuk menjaga uptime jadi sebab downtime."""
        lock = tmp_path / "aruna.lock"
        with single_instance(lock):
            pass
        with single_instance(lock):  # tidak boleh melempar
            pass

    def test_crash_tidak_meninggalkan_kunci_basi(self, tmp_path) -> None:
        lock = tmp_path / "aruna.lock"
        with contextlib.suppress(RuntimeError), single_instance(lock):
            raise RuntimeError("boom")
        with single_instance(lock):
            pass

    def test_folder_kunci_dibuat_kalau_belum_ada(self, tmp_path) -> None:
        lock = tmp_path / "belum" / "ada" / "aruna.lock"
        with single_instance(lock):
            assert lock.exists()

    def test_dipasang_di_jalur_hidup(self) -> None:
        """Cacat yang paling sering di repo ini: ditulis, diekspor, diuji,
        tidak pernah dicapai jalur yang benar-benar jalan."""
        import inspect

        from aruna import cli as cli_module

        source = inspect.getsource(cli_module.cmd_supervise)
        assert "single_instance(" in source, "supervise jalan tanpa kunci"


class TestBerkasSatuKlik:
    def test_bat_memanggil_supervise(self) -> None:
        from pathlib import Path

        isi = Path("ARUNA.bat").read_text(encoding="utf-8")
        assert "aruna.cli supervise" in isi
        assert ".venv\\Scripts\\python.exe" in isi

    def test_bat_menyatakan_bukan_eksekutor(self) -> None:
        """Berkas yang diklik operator adalah tempat paling mungkin dibaca,
        jadi batasannya dinyatakan di situ."""
        from pathlib import Path

        isi = Path("ARUNA.bat").read_text(encoding="utf-8").lower()
        assert "menganalisis saja" in isi
        assert "tidak ada order" in isi


def test_ambang_sehat_lebih_lama_dari_jeda_maksimum() -> None:
    """Kalau ambang "sehat" lebih pendek daripada jeda restart, proses yang
    mati seketika akan tetap dianggap sehat begitu jedanya cukup melebar - dan
    penjaga berhenti melambat tepat saat seharusnya paling melambat."""
    from aruna.supervisor import RESTART_MAX_SEC

    assert HEALTHY_UPTIME_SEC > RESTART_MAX_SEC
