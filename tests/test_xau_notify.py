"""Pesan sinyal XAU: apa yang wajib ada, dan apa yang wajib tidak ada."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from aruna.core.enums import AgentRole, Decision
from aruna.xau.geometri import Geometri
from aruna.xau.keputusan import SinyalXau
from aruna.xau.notify import kirim_sinyal, susun_pesan
from aruna.xau.suara import RekapSuara, Suara, SuaraAgen

SAAT = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _sinyal(**kw) -> SinyalXau:
    geo = Geometri(
        entry=Decimal("4605.32"),
        stop=Decimal("4598.10"),
        target=Decimal("4620.00"),
        atr=Decimal("4.81"),
        sentuhan_target=5,
    )
    rekap = RekapSuara(
        setuju=4, menentang=1, netral=4,
        rincian=(
            SuaraAgen(AgentRole.TECHNICAL, Suara.AGREE, Decision.BUY, 0.9, False),
        ),
    )
    bawaan = dict(
        keputusan=Decision.BUY,
        setup_id="XAU/USD:BUY:4620.00",
        alasan=None,
        rekap=rekap,
        geometri=geo,
        confidence=0.71,
        spread_diukur=False,
    )
    return SinyalXau(**{**bawaan, **kw})


class TestIsiPesan:
    def test_arah_dan_harga_ada(self) -> None:
        pesan = susun_pesan(_sinyal(), as_of=SAAT)
        assert "BUY" in pesan
        assert "4,605.32" in pesan
        assert "4,620.00" in pesan

    def test_rr_dan_jarak_atr_ada(self) -> None:
        """Angka yang membuat gerbangnya bisa dibantah operator."""
        pesan = susun_pesan(_sinyal(), as_of=SAAT)
        assert "RR" in pesan
        assert "ATR" in pesan

    def test_suara_dan_kontradiksi_ada(self) -> None:
        pesan = susun_pesan(_sinyal(), as_of=SAAT)
        assert "4 setuju" in pesan
        assert "kontra" in pesan


class TestMenyebutYangTidakDiukur:
    def test_gerbang_spread_dinyatakan_tidak_aktif(self) -> None:
        """Pesan yang diam soal ini membiarkan operator mengira seluruh
        gerbang lulus. Laporan yang menyembunyikan lubangnya lebih berbahaya
        daripada lubang itu sendiri."""
        pesan = susun_pesan(_sinyal(), as_of=SAAT)
        assert "TIDAK AKTIF" in pesan
        assert "bid/ask" in pesan

    def test_kalender_tak_terbaca_dinyatakan(self) -> None:
        pesan = susun_pesan(_sinyal(), as_of=SAAT, berita=None)
        assert "kalender tidak terbaca" in pesan

    def test_sesi_tak_diukur_dinyatakan_bukan_dikosongkan(self) -> None:
        pesan = susun_pesan(_sinyal(), as_of=SAAT, sesi=None)
        assert "tidak diukur" in pesan


class TestAnalisSaja:
    def test_menyatakan_bukan_instruksi_eksekusi(self) -> None:
        pesan = susun_pesan(_sinyal(), as_of=SAAT)
        assert "menganalisa saja" in pesan.lower()
        assert "bukan instruksi eksekusi" in pesan.lower()

    def test_tidak_pernah_menyebut_ukuran_posisi(self) -> None:
        """ARUNA analis; angka ukuran posisi akan membuatnya terbaca sebagai
        perintah dagang."""
        pesan = susun_pesan(_sinyal(), as_of=SAAT).lower()
        for terlarang in ("leverage", "margin", "lot", "quantity", "notional"):
            assert f" {terlarang} " not in f" {pesan} " or "tidak ada" in pesan

    def test_kosakata_futures_tidak_muncul(self) -> None:
        pesan = susun_pesan(_sinyal(), as_of=SAAT)
        assert "LONG" not in pesan
        assert "SHORT" not in pesan
        assert "WAIT" not in pesan


class TestPengiriman:
    class PengirimPalsu:
        def __init__(self, configured=True, berhasil=True):
            self.configured = configured
            self._berhasil = berhasil
            self.terkirim: list[str] = []

        async def send(self, text: str) -> bool:
            self.terkirim.append(text)
            return self._berhasil

    async def test_terkirim_saat_terkonfigurasi(self) -> None:
        p = self.PengirimPalsu()
        assert await kirim_sinyal(p, "halo") is True
        assert p.terkirim == ["halo"]

    async def test_tanpa_pengirim_tidak_menjatuhkan(self) -> None:
        """Barisnya sudah tersimpan; kehilangan pesan lebih kecil daripada
        kehilangan loop."""
        assert await kirim_sinyal(None, "halo") is False

    async def test_pengirim_tak_terkonfigurasi_dilewati(self) -> None:
        p = self.PengirimPalsu(configured=False)
        assert await kirim_sinyal(p, "halo") is False
        assert p.terkirim == []

    async def test_gagal_kirim_dilaporkan_bukan_dilempar(self) -> None:
        p = self.PengirimPalsu(berhasil=False)
        assert await kirim_sinyal(p, "halo") is False
