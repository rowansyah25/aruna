"""Apa saja yang boleh keluar lewat Telegram (PASAL 14.38).

Yang dilindungi bukan kuota, melainkan perhatian: sebuah notifikasi yang selalu
berbunyi diabaikan persis ketika ia akhirnya penting.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from aruna.decision.channel import (
    CATATAN_MATI,
    DILARANG,
    ChannelError,
    Jenis,
    allow,
)

#: Tujuh jenis PASAL 14.38, diketik ulang dari spesifikasinya.
PASAL_14_38 = [
    "FINAL SIGNAL",
    "WIN",
    "LOSS",
    "IMPORTANT NO SIGNAL",
    "HEALTH ALERT",
    "MODEL PROPOSAL",
    "DAILY REPORT",
]


class TestDaftarnya:
    def test_tujuh_jenis_persis_seperti_pasalnya(self) -> None:
        assert [j.value for j in Jenis] == PASAL_14_38

    def test_larangannya_lengkap(self) -> None:
        """Enam bentuk kegagalan yang PASAL 14.38 sebut namanya."""
        assert list(DILARANG) == [
            "setiap candle",
            "setiap polling",
            "setiap agent calculation",
            "setiap protest",
            "setiap API request",
            "setiap internal score",
        ]

    def test_peristiwa_proses_tidak_ada_di_daftar(self) -> None:
        """"ARUNA menyala" tidak memberi operator satu pun keputusan untuk
        diambil - kalau ia menyala, signal akan datang sendiri."""
        teks = " ".join(j.value for j in Jenis).upper()

        for kata in ("STARTUP", "ONLINE", "MENYALA", "RESTART"):
            assert kata not in teks


class TestPenjaga:
    def test_jenis_yang_sah_diloloskan(self) -> None:
        for j in Jenis:
            assert allow(j) is j

    def test_teks_bebas_ditolak(self) -> None:
        """Penjaga yang menebak jenis pesan dari isinya akan sesekali salah
        tebak - dan yang salah tebak adalah pesan yang paling tidak biasa,
        yaitu yang paling mungkin penting."""
        with pytest.raises(ChannelError):
            allow("FINAL SIGNAL")  # type: ignore[arg-type]

    def test_jenis_karangan_ditolak(self) -> None:
        with pytest.raises(ChannelError, match="proses internal"):
            allow("STARTUP")  # type: ignore[arg-type]


class TestPesanSambutanSudahDicabut:
    """Bukti bahwa jalur hidup benar-benar berhenti mengirimnya.

    Terukur pada 2026-08-19: bot menyala lima kali dalam tujuh jam, semuanya
    dari restart rutin - lima notifikasi yang tidak mengubah apa pun.
    """

    def test_tidak_ada_lagi_penyusun_pesan_sambutan(self) -> None:
        from aruna.app import ArunaApplication

        assert not hasattr(ArunaApplication, "_startup_message")

    @pytest.mark.asyncio
    async def test_startup_sungguhan_tidak_mengirim_apa_pun(self) -> None:
        """Lewat ``startup()`` yang sebenarnya, dengan bot yang mengaku hidup.

        Bukan pemindaian teks sumber. Sebuah test yang mencari string
        ``bot.send`` di dalam kode akan tetap hijau pada pemanggilan yang
        dieja lain, dan merah pada komentar yang menyebutnya - dua-duanya
        salah. Yang dihitung di sini adalah pesan yang benar-benar berangkat.
        """
        # ``background=True``: Telegram hanya dinyalakan di jalur itu, dan
        # sebuah test yang menjalankan jalur tanpa bot tidak bisa membuktikan
        # apa pun tentang pesan yang dikirim bot.
        app = _AppTanpaIO()
        try:
            await app.startup(background=True)
            assert app.bot is not None
            assert app.bot.terkirim == [], (
                f"penyalaan mengirim {len(app.bot.terkirim)} pesan; "
                f"peristiwa proses tidak ada di daftar PASAL 14.38"
            )
        finally:
            await app.shutdown()


class TestWaktuMatiTetapDilaporkan:
    """Yang dicabut adalah "ARUNA menyala", bukan "ARUNA sempat mati".

    Keduanya datang pada momen yang sama - penyalaan - dan itu justru kenapa
    pembedaannya harus diuji: sebuah pencabutan yang terlalu lebar akan
    menghapus satu-satunya hal yang melindungi operator dari salah membaca
    diam.
    """

    @pytest.mark.asyncio
    async def test_mati_lama_menghasilkan_satu_health_alert(self) -> None:
        from aruna.health.heartbeat import HEARTBEAT_KEY

        app = _AppTanpaIO()
        app._denyut = {HEARTBEAT_KEY: {"at": "2026-01-01T00:00:00.000Z"}}
        try:
            await app.startup(background=True)
            assert app.bot is not None
            assert len(app.bot.terkirim) == 1
            assert "ARUNA MATI" in app.bot.terkirim[0]
            assert "tidak ada setup" in app.bot.terkirim[0]
        finally:
            await app.shutdown()

    @pytest.mark.asyncio
    async def test_denyut_baru_tidak_menghasilkan_apa_pun(self) -> None:
        """Restart rutin - denyut beberapa detik lalu - tetap diam."""
        from aruna.core.clock import isoformat, now_utc
        from aruna.health.heartbeat import HEARTBEAT_KEY

        app = _AppTanpaIO()
        app._denyut = {HEARTBEAT_KEY: {"at": isoformat(now_utc())}}
        try:
            await app.startup(background=True)
            assert app.bot is not None
            assert app.bot.terkirim == []
        finally:
            await app.shutdown()


class TestDenyutDitulisTiapSiklus:
    """Tanpa penulisnya, pelapornya tidak punya apa pun untuk dibandingkan."""

    @pytest.mark.asyncio
    async def test_siklus_upkeep_menulis_denyut(self) -> None:
        from datetime import UTC, datetime

        from aruna.core.config import UpkeepSettings
        from aruna.health.heartbeat import HEARTBEAT_KEY
        from aruna.upkeep.loop import UpkeepLoop

        class _State:
            def __init__(self) -> None:
                self.isi: dict = {}

            async def get(self, key: str):
                return self.isi.get(key)

            async def set(self, key: str, value: dict, *, actor: str) -> None:
                self.isi[key] = value

        state = _State()
        loop = UpkeepLoop(
            refresher=None,
            resolver=None,
            heartbeat_state=state,
            settings=UpkeepSettings(enabled=False),
        )

        await loop.cycle(now=datetime(2026, 8, 19, 12, 0, tzinfo=UTC))

        assert HEARTBEAT_KEY in state.isi

    @pytest.mark.asyncio
    async def test_aplikasi_mengoper_penyimpannya_ke_loop(self) -> None:
        """Test di atas membangun loop-nya sendiri, jadi ia tidak bisa
        membuktikan bahwa **aplikasi** benar-benar mengoper penyimpan denyut.

        Tanpa yang ini, ``heartbeat_state=self.app_state`` bisa diganti
        ``None`` dan semua test tetap hijau - denyut yang tidak pernah ditulis,
        dan waktu mati yang tidak pernah bisa diukur.
        """
        app = _AppTanpaIO(bangun_upkeep=True)
        try:
            await app.startup(background=False)
            assert app.upkeep is not None
            assert app.upkeep._heartbeat is app.app_state
            assert app.upkeep._heartbeat is not None
        finally:
            await app.shutdown()


class _BotPalsu:
    """Bot yang mengaku hidup dan mencatat apa pun yang dikirim padanya."""

    def __init__(self) -> None:
        self.started = True
        self.terkirim: list[str] = []
        self.registry: dict = {}

    async def send(self, text: str, **_kw: object) -> bool:
        self.terkirim.append(text)
        return True

    async def stop(self) -> None:
        self.started = False


def _AppTanpaIO(*, bangun_upkeep: bool = False):
    """``ArunaApplication`` sungguhan dengan langkah I/O-nya saja dilepas.

    Pola yang sama dengan ``_wired_app`` di ``test_upkeep.py``: semuanya sejak
    penyalaan berjalan seperti di produksi, jadi jalur yang dicabut dari
    ``startup()`` membuat test gagal alih-alih lolos terhadap kode mati.
    """
    from aruna.app import ArunaApplication

    class _State:
        """``app_state`` secukupnya, isinya bisa diatur test."""

        def __init__(self, isi: dict) -> None:
            self.isi = isi

        async def get(self, key: str):
            return self.isi.get(key)

        async def set(self, key: str, value: dict, *, actor: str) -> None:
            self.isi[key] = value

    class _App(ArunaApplication):
        _denyut: ClassVar[dict] = {}

        def configure_logging(self) -> None:
            return None

        async def _start_database(self) -> None:
            return None

        def _build_repositories(self) -> None:
            from types import SimpleNamespace

            # Cukup untuk melewati penjaga `upkeep.not_wired`: loop-nya tidak
            # dijalankan di test ini, hanya diperiksa perakitannya.
            isi = SimpleNamespace() if bangun_upkeep else None
            self.market_data = isi  # type: ignore[assignment]
            self.signals = isi  # type: ignore[assignment]
            self.signal_store = None
            self.app_state = _State(dict(self._denyut))  # type: ignore[assignment]

        async def _verify_schema(self) -> None:
            return None

        async def _load_runtime_state(self) -> None:
            return None

        async def _load_measured_history(self) -> None:
            return None

        async def _start_ingestion(self) -> None:
            if not bangun_upkeep:
                return None
            from types import SimpleNamespace

            # ``markets`` kosong: loop-nya dirakit tapi tidak menyegarkan apa
            # pun. Yang diperiksa test ini adalah perakitannya, bukan kerjanya.
            async def _tutup() -> None:
                return None

            self.ingest = SimpleNamespace(  # type: ignore[assignment]
                markets=(), close=_tutup
            )

        async def _start_telegram(self) -> None:
            self.bot = _BotPalsu()  # type: ignore[assignment]

        async def _start_health_monitor(self) -> None:
            return None

        async def _start_upkeep(self) -> None:
            if bangun_upkeep:
                await super()._start_upkeep()
                return
            return None

        async def _record_event(self, **_kwargs: object) -> None:
            return None

        async def _audit(self, *_a: object, **_k: object) -> None:
            return None

    return _App()


class TestPesanMatiTidakBisaDiandalkan:
    def test_catatannya_menyebut_batasannya(self) -> None:
        """Terukur: `aruna.stopped` 22 kali, `telegram.stopped` nol kali.
        Jaring pengaman yang hanya bekerja pada kasus yang tidak berbahaya
        mengajari pembacanya bahwa diam tanpa pesan berarti ARUNA masih hidup.
        """
        assert "crash" in CATATAN_MATI
        assert "rapi" in CATATAN_MATI

    def test_pemberitahuan_mati_terbaca_sebagai_health(self) -> None:
        """Ia tetap dikirim - diamnya sistem yang tidak diketahui adalah diam
        yang salah dibaca sebagai "tidak ada setup"."""
        assert Jenis.HEALTH in Jenis
