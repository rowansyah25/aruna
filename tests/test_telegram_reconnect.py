"""ARUNA menyusul sendiri saat Telegram pulih.

**Kejadian yang memicunya, dengan angkanya.** Pada 2026-08-19,
``api.telegram.org`` menolak seluruh koneksi dari mesin operator: ReadTimeout
tiga dari tiga percobaan HTTP mentah, dua puluh detik masing-masing, sementara
``api.binance.com`` menjawab 200 pada saat yang sama. Internetnya hidup; hanya
Telegram yang tersumbat.

Yang terjadi kemudian bukan pesan yang tertunda. ``_start_telegram`` memanggil
``bot.start()`` tepat sekali; kegagalannya dicatat, health-nya jadi DOWN, dan
tidak ada apa pun yang mencoba lagi. Ketika sumbatannya lepas - dan ia lepas -
ARUNA tetap diam sampai prosesnya di-restart manual. Operator sedang di luar
dan kehilangan seluruh signal, hasil, dan alert kesehatan tanpa satu pun tanda
bahwa ada yang perlu diperbaiki.

Sebuah sistem yang diam karena kesalahan sesaat, dan tetap diam sesudah
kesalahan itu berlalu, lebih buruk daripada yang tidak pernah menyala: yang
kedua terlihat rusak, yang pertama terlihat sepi.
"""

from __future__ import annotations

import asyncio

import pytest

from aruna.app import (
    TELEGRAM_RETRY_MAX_SEC,
    TELEGRAM_RETRY_MIN_SEC,
    ArunaApplication,
)
from aruna.core.errors import TelegramError


class _Bot:
    """Bot yang gagal sekian kali lalu berhasil."""

    def __init__(self, gagal: int = 2, *, permanent: bool = False) -> None:
        self._sisa = gagal
        self._permanent = permanent
        self.started = False
        self.percobaan = 0

    async def start(self) -> None:
        self.percobaan += 1
        if self._sisa > 0:
            self._sisa -= 1
            raise TelegramError("Timed out", permanent=self._permanent)
        self.started = True


def _app(bot: _Bot) -> ArunaApplication:
    app = ArunaApplication.__new__(ArunaApplication)
    app.bot = bot
    app._stop_requested = asyncio.Event()
    app._telegram_retry = None

    async def _diam(**kwargs: object) -> None:
        return None

    app._record_event = _diam
    return app


@pytest.fixture
def cepat(monkeypatch):
    """Percepat jedanya supaya test tidak menunggu tiga puluh detik."""
    import aruna.app as modul

    monkeypatch.setattr(modul, "TELEGRAM_RETRY_MIN_SEC", 0.01)
    monkeypatch.setattr(modul, "TELEGRAM_RETRY_MAX_SEC", 0.05)


class TestMenyambungUlang:
    @pytest.mark.asyncio
    async def test_mencoba_lagi_sampai_berhasil(self, cepat) -> None:
        bot = _Bot(gagal=2)
        await _app(bot)._retry_telegram()

        assert bot.started is True
        assert bot.percobaan == 3, bot.percobaan

    @pytest.mark.asyncio
    async def test_bot_yang_sudah_hidup_tidak_dinyalakan_lagi(self, cepat) -> None:
        """Penjaga untuk keadaan yang benar-benar bisa terjadi.

        Versi pertama test ini memakai bot yang berhasil pada percobaan
        pertama, dan itu tidak menguji apa pun: loop-nya `return` di akhir
        putaran apa pun isinya, jadi mencabut penjaganya tidak mengubah
        hitungan. Yang harus diuji adalah bot yang SUDAH hidup sebelum loop
        berjalan - misalnya karena jalur lain berhasil menyalakannya sementara
        loop ini tidur.
        """
        bot = _Bot(gagal=0)
        bot.started = True
        await _app(bot)._retry_telegram()

        assert bot.percobaan == 0, "start() dipanggil pada bot yang sudah hidup"

    @pytest.mark.asyncio
    async def test_token_tidak_sah_tidak_dicoba_ulang(self, cepat) -> None:
        """Menunggu tidak memperbaiki token yang salah.

        Mencoba ulang selamanya hanya membuat log tidak terbaca - dan log yang
        tidak terbaca adalah tempat kegagalan berikutnya bersembunyi.
        """
        bot = _Bot(gagal=99, permanent=True)
        await _app(bot)._retry_telegram()

        assert bot.percobaan == 1, bot.percobaan
        assert bot.started is False

    @pytest.mark.asyncio
    async def test_tidak_menyalakan_bot_saat_sedang_berhenti(self, cepat) -> None:
        """Sesudah berhenti diminta, tidak boleh ada ``start()`` lagi.

        Versi pertama test ini hanya memeriksa loop-nya berhenti - dan itu
        dijamin oleh syarat ``while`` di luar, jadi mencabut pemeriksaan di
        DALAM putaran tidak membuatnya merah. Yang dijaga pemeriksaan itu
        adalah satu panggilan ``start()`` tambahan tepat saat ARUNA sedang
        menutup dirinya.
        """
        bot = _Bot(gagal=999)
        app = _app(bot)

        # Keadaan yang benar-benar dijaga: berhenti diminta SEMENTARA loop
        # sedang tidur di `wait_for`. Menyalakan `_stop_requested` sebelum
        # loop dipanggil tidak menguji apa pun - syarat `while` di luar sudah
        # menangkapnya, dan pemeriksaan di dalam putaran tidak pernah
        # tercapai.
        tugas = asyncio.create_task(app._retry_telegram())
        await asyncio.sleep(0)
        app._stop_requested.set()
        await asyncio.wait_for(tugas, timeout=1.0)

        assert bot.percobaan == 0, "start() dipanggil sesudah berhenti diminta"

    @pytest.mark.asyncio
    async def test_percobaan_yang_sedang_tidur_dibatalkan(self, cepat) -> None:
        bot = _Bot(gagal=999)
        app = _app(bot)
        tugas = asyncio.create_task(app._retry_telegram())
        app._telegram_retry = tugas
        await asyncio.sleep(0.03)

        await asyncio.wait_for(app._stop_telegram_retry(), timeout=1.0)

        # Diperiksa pada TASK-nya, bukan hanya pada atribut yang menunjuknya.
        # Versi pertama test ini hanya memeriksa `_telegram_retry is None`, dan
        # baris itu tetap berjalan walau seluruh pembatalannya dicabut - jadi
        # ia hijau untuk task yang masih hidup dan tidak menunjuk ke mana pun,
        # yang justru kebocoran yang mau dicegah.
        assert tugas.done(), "task-nya masih hidup sesudah dihentikan"
        assert app._telegram_retry is None
        assert not bot.started

    @pytest.mark.asyncio
    async def test_bot_yang_hilang_tidak_meledak(self, cepat) -> None:
        app = _app(_Bot(gagal=1))
        app.bot = None

        await asyncio.wait_for(app._retry_telegram(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_jedanya_melebar_bukan_tetap(self, cepat, monkeypatch) -> None:
        """Sumbatan jaringan bisa berlangsung berjam-jam. Mencoba tiap tiga
        puluh detik selama itu memindahkan kebisingan ke log."""
        import aruna.app as modul

        jeda: list[float] = []
        asli = asyncio.wait_for

        async def catat(aw, timeout=None):
            jeda.append(timeout)
            return await asli(aw, timeout=timeout)

        monkeypatch.setattr(modul.asyncio, "wait_for", catat)
        bot = _Bot(gagal=3)
        await _app(bot)._retry_telegram()

        assert len(jeda) >= 3
        assert jeda[1] > jeda[0], jeda
        assert all(j <= modul.TELEGRAM_RETRY_MAX_SEC for j in jeda), jeda


class TestSebabDibedakan:
    """Token yang salah dan jaringan yang tersumbat terlihat sama di log dan
    menuntut jawaban yang berlawanan."""

    def test_bawaannya_sementara(self) -> None:
        """Yang tidak dinyatakan permanen harus boleh dicoba lagi. Bawaan yang
        sebaliknya akan membuat setiap kegagalan baru diam-diam menyerah."""
        assert TelegramError("apa saja").permanent is False

    def test_token_tidak_sah_ditandai_di_lapisan_bot(self) -> None:
        """Ditandai di satu-satunya tempat yang masih memegang tipe
        pengecualian aslinya; di atas sana hanya tersisa kalimatnya."""
        import inspect

        from aruna.notify.telegram import bot as modul

        sumber = inspect.getsource(modul.TelegramBot.start)
        assert "PTBInvalidToken" in sumber
        assert "permanent=" in sumber

    def test_jedanya_masuk_akal(self) -> None:
        assert 10.0 <= TELEGRAM_RETRY_MIN_SEC <= 60.0
        assert 300.0 <= TELEGRAM_RETRY_MAX_SEC <= 3600.0


class TestDirangkaiKeJalurHidup:
    def test_startup_melepas_percobaan_ulang(self) -> None:
        import inspect

        sumber = inspect.getsource(ArunaApplication._start_telegram)
        assert "_retry_telegram" in sumber
        assert "exc.permanent" in sumber

    def test_shutdown_memanggil_seam_penghentiannya(self) -> None:
        """Pemindaian teks, dan ia HANYA memeriksa panggilannya ada.

        Apa yang panggilan itu lakukan diuji perilakunya di
        ``test_percobaan_yang_sedang_tidur_dibatalkan``. Pembagian itu bukan
        kerapian: versi sebelumnya memindai ``shutdown`` mencari kata
        ``cancel()``, dan pemindaian itu tetap hijau ketika seluruh
        pembatalannya dimatikan - karena katanya masih tertulis di sana.
        """
        import inspect

        sumber = inspect.getsource(ArunaApplication.shutdown)
        assert "_stop_telegram_retry()" in sumber

    def test_perintah_sekali_jalan_tidak_melepas_task(self) -> None:
        """``background=False`` adalah bentuk untuk CLI sekali jalan; sebuah
        task latar di sana akan hidup lebih lama dari perintahnya."""
        import inspect

        sumber = inspect.getsource(ArunaApplication._start_telegram)
        assert "self._background" in sumber
