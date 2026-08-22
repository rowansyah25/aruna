"""Pembelajaran berjalan sendiri, dan Telegram hanya boleh membacanya.

**Kejadian yang memicu berkas ini.** Sesudah seluruh Phase 12 selesai dibangun,
diuji, dan di-restart ke produksi, ``AdaptiveLearningService`` ternyata hanya
dipanggil dari satu tempat: ``cli.py``. Loop upkeep tidak menyentuhnya. Jadi
ARUNA belajar tepat ketika seseorang mengetik ``aruna learn``, dan
``Strategist`` yang membaca hasilnya membaca angka dari entah kapan.

Kode yang benar, diuji, diekspor, dan tidak pernah dilewati - keluarga cacat
yang paling sering terulang di sistem ini, dan yang paling sulit dilihat karena
setiap bagiannya tampak beres.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aruna.upkeep.loop import LEARNING_INTERVAL

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)


class _Learning:
    def __init__(self, *, meledak: bool = False) -> None:
        self.dipanggil = 0
        self._meledak = meledak

    async def run(self, *, now):
        self.dipanggil += 1
        if self._meledak:
            raise RuntimeError("tabel belum ada")
        from types import SimpleNamespace as N

        return N(observations=100, stored_patterns=50)


def _loop(learning):
    from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

    loop = UpkeepLoop.__new__(UpkeepLoop)
    loop._learning = learning
    loop._stats = UpkeepStats(started_at=NOW)
    return loop


class TestPembelajaranBerjalanSendiri:
    @pytest.mark.asyncio
    async def test_putaran_pertama_langsung_jalan(self) -> None:
        belajar = _Learning()
        loop = _loop(belajar)

        await loop._run_learning(NOW)

        assert belajar.dipanggil == 1
        assert loop._stats.learning_runs == 1

    @pytest.mark.asyncio
    async def test_tidak_diulang_di_hari_yang_sama(self) -> None:
        """Sejarahnya bertambah beberapa prediksi per jam; satu putaran membaca
        seluruhnya dan menulis ratusan baris."""
        belajar = _Learning()
        loop = _loop(belajar)

        await loop._run_learning(NOW)
        await loop._run_learning(NOW + timedelta(hours=6))
        await loop._run_learning(NOW + timedelta(hours=23))

        assert belajar.dipanggil == 1

    @pytest.mark.asyncio
    async def test_jalan_lagi_sesudah_jendelanya_lewat(self) -> None:
        belajar = _Learning()
        loop = _loop(belajar)

        await loop._run_learning(NOW)
        await loop._run_learning(NOW + LEARNING_INTERVAL)

        assert belajar.dipanggil == 2

    @pytest.mark.asyncio
    async def test_kegagalannya_tidak_menghentikan_siklus(self) -> None:
        """Pembelajaran yang gagal berarti angka membeku satu hari; siklus yang
        ikut mati berarti candle tidak disegarkan dan sinyal tidak dinilai."""
        belajar = _Learning(meledak=True)
        loop = _loop(belajar)

        await loop._run_learning(NOW)

        assert loop._stats.learning_failures == 1
        assert loop._stats.learning_runs == 0

    @pytest.mark.asyncio
    async def test_kegagalan_tidak_menandai_sudah_jalan(self) -> None:
        """Kalau gagal dianggap sudah jalan, ARUNA menunggu sehari penuh untuk
        mencoba lagi sesuatu yang mungkin pulih semenit kemudian."""
        belajar = _Learning(meledak=True)
        loop = _loop(belajar)

        await loop._run_learning(NOW)
        assert loop._stats.last_learning_at is None

    def test_jendelanya_sehari(self) -> None:
        assert timedelta(days=1) == LEARNING_INTERVAL


class TestDirangkaiKeAplikasi:
    def test_loop_upkeep_memanggil_pembelajaran(self) -> None:
        import inspect

        from aruna.upkeep.loop import UpkeepLoop

        sumber = inspect.getsource(UpkeepLoop)
        assert "self._learning is not None" in sumber
        assert "_run_learning" in sumber

    def test_aplikasi_menyerahkan_servicenya_ke_loop(self) -> None:
        """Tanpa baris ini seluruh Phase 12 diam di produksi."""
        import inspect

        from aruna.app import ArunaApplication

        sumber = inspect.getsource(ArunaApplication)
        assert "learning=AdaptiveLearningService(" in sumber


class TestTelegramHanyaMembaca:
    """PASAL 12.25. Perintah yang memicu putaran pembelajaran akan menulis
    ratusan baris tiap kali diketik."""

    def test_tidak_ada_perintah_yang_memicu_pembelajaran(self) -> None:
        from aruna.notify.telegram.registry import build_registry

        nama = set(build_registry())
        assert "learn" not in nama, "perintah pemicu tidak boleh ada"

    def test_dua_perintah_baca_saja_terdaftar(self) -> None:
        from aruna.notify.telegram.registry import build_registry

        nama = set(build_registry())
        assert {"strategies", "learning"} <= nama

    def test_perintahnya_terikat_saat_penyimpanan_ada(self) -> None:
        import inspect

        from aruna.notify.telegram.bot import TelegramBot

        sumber = inspect.getsource(TelegramBot._bind_phase1_handlers)
        assert '"strategies": self._cmd_strategies' in sumber
        assert '"learning": self._cmd_learning' in sumber
        assert "self._deps.adaptive is not None" in sumber

    def test_bot_tidak_punya_jalur_tulis_ke_pembelajaran(self) -> None:
        """Bot boleh membaca hasil; ia tidak boleh mengubah satu pun status
        strategi atau menjalankan putaran."""
        import inspect

        from aruna.notify.telegram import bot as modul

        for terlarang in (
            "set_strategy_status",
            "save_patterns",
            "AdaptiveLearningService",
            "record_event",
        ):
            assert terlarang not in inspect.getsource(modul), terlarang

    @pytest.mark.asyncio
    async def test_katalog_kosong_dikatakan_bukan_didiamkan(self) -> None:
        from aruna.notify.telegram.bot import BotDeps, TelegramBot

        class _Store:
            async def catalog_with_performance(self):
                return []

            async def overall_win_rate(self):
                return None

        from aruna.core.config import get_settings

        bot = TelegramBot.__new__(TelegramBot)
        bot._deps = BotDeps(
            settings=get_settings(),
            state=None,
            phase=12,
            latest_health=lambda: None,
            refresh_health=None,
            adaptive=_Store(),
        )
        keluar: list[str] = []

        async def _balas(_update, teks):
            keluar.append(teks)

        bot._reply = _balas
        await bot._cmd_strategies(None, None)

        assert keluar and "kosong" in keluar[0]

    @pytest.mark.asyncio
    async def test_belum_ada_hasil_dikatakan(self) -> None:
        from aruna.core.config import get_settings
        from aruna.notify.telegram.bot import BotDeps, TelegramBot

        class _Store:
            async def notable_patterns(self, **kwargs):
                return []

            async def recent_events(self, **kwargs):
                return []

        bot = TelegramBot.__new__(TelegramBot)
        bot._deps = BotDeps(
            settings=get_settings(),
            state=None,
            phase=12,
            latest_health=lambda: None,
            refresh_health=None,
            adaptive=_Store(),
        )
        keluar: list[str] = []

        async def _balas(_update, teks):
            keluar.append(teks)

        bot._reply = _balas
        await bot._cmd_learning(None, None)

        assert keluar and "Belum ada" in keluar[0]
