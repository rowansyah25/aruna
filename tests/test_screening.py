"""Screening pra-pembukaan IDX.

Operator: "jalan lagi tiga puluh menit sebelum market buka, dan fast screening
untuk tahu apa yang akan terjadi di market."

Setengah permintaan itu tidak bisa dipenuhi siapa pun: tidak ada yang tahu arah
pembukaan, dan PASAL 51 melarang mengklaimnya. Yang dikirim karena itu adalah
apa yang **sudah** terjadi selagi bursa tutup - dan pesannya mengatakan
perbedaan itu, bukan membiarkannya disimpulkan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from aruna.notify.screening import (
    MAX_SIMBOL,
    SCREENING_SENT_KEY,
    PreOpenScreening,
    render_screening,
)

WIB = ZoneInfo("Asia/Jakarta")


def _wib(jam: int, menit: int = 0, *, hari: int = 18) -> datetime:
    return datetime(2026, 8, hari, jam, menit, tzinfo=WIB)


def _event(kind: str = "VOLUME_SPIKE", severity: float = 2.4, detail: str = "x"):
    return SimpleNamespace(
        kind=SimpleNamespace(value=kind), severity=severity, detail=detail
    )


def _hasil(symbol: str, *events, scanned: bool = True, reason: str = ""):
    return SimpleNamespace(
        symbol=symbol, events=tuple(events), scanned=scanned, reason=reason
    )


class _Sender:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


class _Scanner:
    def __init__(self, hasil=()) -> None:
        self.hasil = list(hasil)
        self.dipanggil = 0

    async def scan(self, moment=None):
        self.dipanggil += 1
        return self.hasil


class _State:
    def __init__(self, stored=None) -> None:
        self.stored = stored

    async def get(self, key: str):
        return self.stored

    async def set(self, key: str, value, *, actor: str) -> None:
        self.stored = value


def _screening(*, hasil=(), sender=None, state=None, news=None):
    return PreOpenScreening(
        scanner=_Scanner(hasil),
        sender=sender or _Sender(),
        state=state,
        news=news,
    )


class TestBukanRamalan:
    """Sebuah daftar berjudul "apa yang akan terjadi" akan dibaca sebagai
    prediksi bahkan ketika isinya identik."""

    def test_menyangkal_meramal(self) -> None:
        # Baris dibungkus, jadi kalimatnya diperiksa setelah baris digabung -
        # bukan sebagai potongan yang kebetulan tidak terpotong.
        teks = " ".join(render_screening([_hasil("BBCA", _event())]).split())
        assert "BUKAN ramalan" in teks
        assert "tidak tahu arah pembukaan" in teks

    def test_judulnya_soal_yang_sudah_terjadi(self) -> None:
        teks = render_screening([_hasil("BBCA", _event())])
        assert "SEJAK PENUTUPAN KEMARIN" in teks

    def test_tidak_memakai_kata_terlarang(self) -> None:
        """PASAL 51: tidak boleh ada 'pasti naik', 'pasti turun', dan
        seterusnya."""
        from aruna.futures.plan import FORBIDDEN_CLAIMS

        teks = render_screening(
            [_hasil("BBCA", _event()), _hasil("GOTO")]
        ).lower()
        for klaim in FORBIDDEN_CLAIMS:
            assert klaim not in teks, klaim


class TestIsiPesan:
    def test_yang_bergerak_disebut_dengan_angkanya(self) -> None:
        teks = render_screening(
            [_hasil("BBCA", _event("BREAKDOWN", 1.3, "tutup 1,8 ATR di bawah support"))]
        )
        assert "BBCA" in teks
        assert "BREAKDOWN" in teks
        assert "1.30x ambang" in teks
        assert "tutup 1,8 ATR di bawah support" in teks

    def test_yang_diam_ikut_disebut_namanya(self) -> None:
        """Daftar yang hanya memuat yang bergerak tidak bisa dibedakan dari
        pemindaian yang setengah gagal - dan "tidak ada yang bergerak" adalah
        kabar, bukan ketiadaan kabar."""
        teks = render_screening([_hasil("BBCA", _event()), _hasil("BBRI")])
        assert "TIDAK BERGERAK: BBRI" in teks

    def test_yang_gagal_dibaca_dipisah_dari_yang_diam(self) -> None:
        """Menggabungkannya membuat pemindaian yang separuh rusak terbaca
        seperti pasar yang tenang."""
        teks = render_screening([
            _hasil("BBRI"),
            _hasil("GOTO", scanned=False, reason="bar tidak terbaca"),
        ])
        assert "TIDAK BISA DIBACA:" in teks
        assert "GOTO: bar tidak terbaca" in teks
        assert "TIDAK BERGERAK: BBRI" in teks

    def test_pasar_yang_sepi_dikatakan_bukan_dikosongkan(self) -> None:
        teks = render_screening([_hasil("BBCA"), _hasil("BBRI")])
        assert "TIDAK ADA YANG BERGERAK" in teks

    def test_yang_paling_jauh_melewati_ambang_lebih_dulu(self) -> None:
        teks = render_screening([
            _hasil("KECIL", _event(severity=1.1)),
            _hasil("BESAR", _event(severity=4.8)),
        ])
        assert teks.index("BESAR") < teks.index("KECIL")

    def test_daftar_panjang_dipotong_dan_sisanya_disebut(self) -> None:
        banyak = [
            _hasil(f"S{i}", _event(severity=1.0 + i))
            for i in range(MAX_SIMBOL + 3)
        ]
        teks = render_screening(banyak)
        assert "3 simbol lagi" in teks


class TestBerita:
    def test_barisnya_dict_bukan_objek(self) -> None:
        """``NewsRepository.recent`` mengembalikan dict. Versi pertama blok ini
        memakai ``getattr``, yang pada dict selalu gagal dan mencetak judul
        kosong tanpa satu pun error."""
        teks = render_screening(
            [_hasil("BBCA")],
            [{"title": "ANTM bagi dividen", "importance": "HIGH"}],
        )
        assert "ANTM bagi dividen" in teks
        assert "[HIGH]" in teks

    def test_tanpa_judul_dikatakan(self) -> None:
        teks = render_screening([_hasil("BBCA")], [{"importance": "LOW"}])
        assert "(tanpa judul)" in teks


class TestKapanBerjalan:
    @pytest.mark.asyncio
    async def test_jalan_di_jendela_pemanasan(self) -> None:
        sender = _Sender()
        s = _screening(hasil=[_hasil("BBCA", _event())], sender=sender)

        assert await s.run(_wib(8, 35)) is True
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_tidak_jalan_sebelum_jendela(self) -> None:
        sender = _Sender()
        assert await _screening(sender=sender).run(_wib(8, 20)) is False
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_tidak_jalan_saat_bursa_sudah_buka(self) -> None:
        """Namanya pra-pembukaan. Dikirim jam sepuluh pagi ia bukan lagi
        screening pra-pembukaan, hanya pemindaian yang terlambat."""
        sender = _Sender()
        assert await _screening(sender=sender).run(_wib(10, 0)) is False

    @pytest.mark.asyncio
    async def test_tidak_jalan_di_akhir_pekan(self) -> None:
        # 23 Agustus 2026 jatuh hari Minggu.
        sender = _Sender()
        assert await _screening(sender=sender).run(_wib(8, 35, hari=23)) is False

    @pytest.mark.asyncio
    async def test_tidak_memindai_kalau_belum_waktunya(self) -> None:
        """Pemindaian membaca bar seluruh universe. Menjalankannya lalu
        membuang hasilnya adalah kueri yang tidak punya pembaca."""
        s = _screening(hasil=[_hasil("BBCA")])
        await s.run(_wib(8, 20))
        assert s.scanner.dipanggil == 0

    @pytest.mark.asyncio
    async def test_sekali_sehari(self) -> None:
        sender = _Sender()
        s = _screening(hasil=[_hasil("BBCA", _event())], sender=sender)

        await s.run(_wib(8, 35))
        assert await s.run(_wib(8, 50)) is False
        assert len(sender.sent) == 1

    @pytest.mark.asyncio
    async def test_hari_berikutnya_jalan_lagi(self) -> None:
        sender = _Sender()
        s = _screening(hasil=[_hasil("BBCA", _event())], sender=sender)

        await s.run(_wib(8, 35, hari=18))
        assert await s.run(_wib(8, 35, hari=19)) is True

    @pytest.mark.asyncio
    async def test_restart_tidak_mengirim_ulang(self) -> None:
        state = _State()
        lama = _screening(hasil=[_hasil("BBCA", _event())], state=state)
        await lama.run(_wib(8, 35))
        assert state.stored == {"date": "2026-08-18"}

        sender = _Sender()
        baru = _screening(
            hasil=[_hasil("BBCA", _event())], sender=sender, state=state
        )
        assert await baru.run(_wib(8, 50)) is False
        assert sender.sent == []

    @pytest.mark.asyncio
    async def test_gagal_kirim_tidak_distempel(self) -> None:
        state = _State()
        s = _screening(
            hasil=[_hasil("BBCA", _event())], sender=_Sender(ok=False), state=state
        )

        assert await s.run(_wib(8, 35)) is False
        assert state.stored is None
        # Penanda di memori juga tidak boleh terpasang: kalau ia terpasang,
        # percobaan berikutnya di jendela yang sama akan dianggap sudah
        # terkirim walau tidak ada pesan yang pernah sampai.
        assert s._last_date is None

    @pytest.mark.asyncio
    async def test_pasar_sepi_tetap_dikirim(self) -> None:
        """"Tidak ada yang bergerak" adalah kabar. Diam di pagi hari tidak bisa
        dibedakan dari ARUNA yang mati kalau tidak dikatakan."""
        sender = _Sender()
        s = _screening(hasil=[_hasil("BBCA"), _hasil("BBRI")], sender=sender)

        assert await s.run(_wib(8, 35)) is True
        assert "TIDAK ADA YANG BERGERAK" in sender.sent[0]


class TestBeritaTidakMembatalkanPesan:
    @pytest.mark.asyncio
    async def test_kueri_berita_gagal_pesannya_tetap_terkirim(self) -> None:
        class _Meledak:
            async def recent(self, **kwargs):
                raise RuntimeError("database sedang tidak enak badan")

        sender = _Sender()
        s = _screening(
            hasil=[_hasil("BBCA", _event())], sender=sender, news=_Meledak()
        )

        assert await s.run(_wib(8, 35)) is True
        assert "BBCA" in sender.sent[0]

    @pytest.mark.asyncio
    async def test_berita_lama_tidak_ikut(self) -> None:
        lama = datetime(2026, 8, 1, tzinfo=UTC)
        baru = _wib(8, 35).astimezone(UTC) - timedelta(hours=6)

        class _Berita:
            async def recent(self, **kwargs):
                return [
                    {"title": "kabar lama", "published_at": lama},
                    {"title": "kabar semalam", "published_at": baru},
                ]

        sender = _Sender()
        s = _screening(hasil=[_hasil("BBCA")], sender=sender, news=_Berita())
        await s.run(_wib(8, 35))

        assert "kabar semalam" in sender.sent[0]
        assert "kabar lama" not in sender.sent[0]

    @pytest.mark.asyncio
    async def test_dipanggil_dengan_parameter_yang_memang_ada(self) -> None:
        """``recent`` menerima limit/market/min_importance - bukan ``since``.
        Versi pertama mengoper ``since=`` dan tertangkap except, jadi blok
        beritanya tidak akan pernah muncul dan tidak ada yang gagal berisik."""
        dicatat: dict = {}

        class _Berita:
            async def recent(self, **kwargs):
                dicatat.update(kwargs)
                return []

        s = _screening(hasil=[_hasil("BBCA")], news=_Berita())
        await s.run(_wib(8, 35))

        assert "since" not in dicatat
        assert set(dicatat) <= {"limit", "market", "min_importance"}


class TestSampaiKeLoopYangJalan:
    """Cacat berulang di repo ini: kode ditulis, diekspor, diuji, tidak pernah
    dicapai jalur yang benar-benar jalan."""

    @pytest.mark.asyncio
    async def test_siklus_upkeep_memanggilnya(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        dipanggil: list[datetime] = []

        class _Screening:
            async def run(self, moment):
                dipanggil.append(moment)
                return True

        saat = datetime(2026, 8, 18, 1, 35, tzinfo=UTC)  # 08:35 WIB
        loop = UpkeepLoop(
            refresher=None, resolver=None, locker=None, screening=_Screening(),
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=saat),
        )
        await loop.cycle(now=saat)

        assert dipanggil == [saat]
        assert loop.stats.screenings == 1

    @pytest.mark.asyncio
    async def test_kegagalannya_tidak_menjatuhkan_siklus(self) -> None:
        from aruna.core.config import UpkeepSettings
        from aruna.upkeep.loop import UpkeepLoop, UpkeepStats

        class _Meledak:
            async def run(self, moment):
                raise RuntimeError("universe tidak terbaca")

        saat = datetime(2026, 8, 18, 1, 35, tzinfo=UTC)
        loop = UpkeepLoop(
            refresher=None, resolver=None, locker=None, screening=_Meledak(),
            settings=UpkeepSettings(_env_file=None),
            stats=UpkeepStats(started_at=saat),
        )
        stats = await loop.cycle(now=saat)

        assert stats.screening_failures == 1
        assert stats.cycles == 1

    def test_app_merakitnya(self) -> None:
        import inspect

        from aruna import app as app_module

        sumber = inspect.getsource(app_module.ArunaApplication._start_upkeep)
        assert "screening=self._build_screening()" in sumber

    def test_pemindainya_memang_idx_dan_harian(self) -> None:
        """Pemindai IDX belum pernah ada: ``ScannerService`` selalu dibangun
        dengan market bawaannya, yaitu CRYPTO.

        Intervalnya 1d karena pada 08:30 WIB bar tertutup terbaru sebuah saham
        adalah bar harian kemarin - meminta 15m akan membandingkan satu bar
        terakhir sebelum bel penutup, bukan satu hari.
        """
        import inspect

        from aruna import app as app_module

        sumber = inspect.getsource(app_module.ArunaApplication._build_screening)
        assert "market=Market.IDX" in sumber
        assert "interval=Horizon.D1" in sumber

    def test_tanpa_idx_tidak_dirakit(self) -> None:
        from types import SimpleNamespace as NS

        from aruna.app import ArunaApplication
        from aruna.core.enums import Market

        app = ArunaApplication.__new__(ArunaApplication)
        app.settings = NS(app=NS(enabled_markets=(Market.CRYPTO,)))

        assert app._build_screening() is None


class TestKunciTerpisah:
    def test_tidak_berbagi_kunci_dengan_pengabar_lain(self) -> None:
        from aruna.notify.daily_service import DAILY_SENT_KEY
        from aruna.notify.research import RESEARCH_SENT_KEY

        assert len({SCREENING_SENT_KEY, DAILY_SENT_KEY, RESEARCH_SENT_KEY}) == 3
