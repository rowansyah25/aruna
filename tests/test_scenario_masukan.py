"""Apa yang boleh masuk ke simulasi (bagian 16.3, 16.14).

Kedua penjaganya gagal dengan cara yang sama kalau dicabut: simulasi tetap
berjalan, skenarionya tetap rapi, dan tidak ada satu pun tanda bahwa masukannya
cacat. Itu sebabnya keduanya diuji sebagai penolakan yang **melempar**, bukan
sebagai nilai balik yang bisa diabaikan pemanggil.
"""

from __future__ import annotations

import pytest

from aruna.core.enums import DataQuality
from aruna.scenario.masukan import (
    BATAS_BYTE,
    BIDANG_DIIZINKAN,
    Masukan,
    MasukanDitolak,
    susun_masukan,
)

BERSIH = {
    "market_summary": "BTC/USDT konsolidasi di bawah resistance",
    "recent_price_structure": ["higher low", "lower high"],
    "volume": {"ratio": 2.1},
    "market_regime": "TRENDING_BULLISH",
    "scenario_question": "Simulasikan perkembangan setelah tembusan.",
}


class TestHanyaBidangYangSpecSebut:
    def test_bidang_yang_diizinkan_lolos(self) -> None:
        hasil = susun_masukan(BERSIH)

        assert hasil.bidang == BERSIH

    def test_bidang_asing_dibuang(self) -> None:
        hasil = susun_masukan(BERSIH | {"raw_api_response": {"a": 1}})

        assert "raw_api_response" not in hasil.bidang

    def test_yang_dibuang_dilaporkan(self) -> None:
        """Pembuangan diam-diam membuat pemanggil yang mengira sesuatu ikut
        terkirim tidak punya cara tahu bahwa tidak."""
        hasil = susun_masukan(BERSIH | {"raw_api_response": {}, "debug": 1})

        assert hasil.dibuang == ("debug", "raw_api_response")

    def test_sebelas_bidang_bagian_16_3(self) -> None:
        """Bagian 16.3 menyebut sebelas. Lebih berarti ada yang dikarang."""
        assert len(BIDANG_DIIZINKAN) == 11

    @pytest.mark.parametrize("bidang", BIDANG_DIIZINKAN)
    def test_tiap_bidang_spec_benar_benar_lolos(self, bidang) -> None:
        hasil = susun_masukan({bidang: "isi"})

        assert bidang in hasil.bidang


class TestMutuDitolak:
    """Bagian 16.3: hanya data yang telah divalidasi."""

    def test_mutu_ok_diterima(self) -> None:
        assert susun_masukan(BERSIH, mutu=DataQuality.OK).bidang

    @pytest.mark.parametrize(
        "buruk",
        [
            DataQuality.STALE,
            DataQuality.MISSING,
            DataQuality.ABNORMAL_PRICE,
            DataQuality.PROVIDER_DISCONNECTED,
            DataQuality.UNAVAILABLE,
        ],
    )
    def test_mutu_buruk_ditolak(self, buruk) -> None:
        with pytest.raises(MasukanDitolak, match=buruk.value):
            susun_masukan(BERSIH, mutu=buruk)

    def test_ambangnya_dipinjam_dari_blocks_signal(self) -> None:
        """Bukan ambang kedua. Kalau sebuah bacaan tidak cukup baik untuk
        melahirkan sinyal, ia tidak cukup baik untuk melahirkan skenario -
        dan ambang yang lebih longgar di sini berarti ARUNA menolak bertindak
        atas data itu sambil bersedia bernalar panjang di atasnya.

        Ditulis sebagai perbandingan menyeluruh, jadi anggota `DataQuality`
        yang ditambahkan kelak ikut terjaring tanpa test ini disunting."""
        for mutu in DataQuality:
            ditolak = False
            try:
                susun_masukan(BERSIH, mutu=mutu)
            except MasukanDitolak:
                ditolak = True

            assert ditolak is mutu.blocks_signal, mutu

    def test_mutu_diperiksa_sebelum_ukuran(self) -> None:
        """Muatan yang basi DAN kebesaran harus mengeluh soal basinya: mutu
        yang diperiksa belakangan berarti kerja penyaringan dihabiskan pada
        muatan yang akan ditolak juga."""
        raksasa = {"market_summary": "x" * (BATAS_BYTE * 2)}

        with pytest.raises(MasukanDitolak, match="STALE"):
            susun_masukan(raksasa, mutu=DataQuality.STALE)


class TestUkuranBerbatas:
    """Bagian 16.14."""

    def test_muatan_wajar_lolos(self) -> None:
        assert susun_masukan(BERSIH).ukuran_byte < BATAS_BYTE

    def test_dump_mentah_ditolak(self) -> None:
        raksasa = {"market_summary": "x" * (BATAS_BYTE + 1)}

        with pytest.raises(MasukanDitolak):
            susun_masukan(raksasa)

    def test_pesannya_menyebut_ukurannya(self) -> None:
        """Yang membaca perlu tahu seberapa jauh melewati batas, bukan cuma
        bahwa ia melewatinya - selisihnya menentukan apakah ringkasannya perlu
        dipangkas sedikit atau memang dump yang menyamar."""
        raksasa = {"market_summary": "x" * (BATAS_BYTE * 3)}

        with pytest.raises(MasukanDitolak) as galat:
            susun_masukan(raksasa)

        pesan = str(galat.value)
        assert str(BATAS_BYTE) in pesan
        assert any(
            k.isdigit() and int(k) > BATAS_BYTE for k in pesan.replace(",", " ").split()
        ), pesan

    def test_ukuran_diukur_setelah_penyaringan(self) -> None:
        """Bidang asing dibuang lebih dulu, jadi dump besar di bidang yang
        memang tidak dipakai tidak menolak muatan yang sebenarnya kecil."""
        hasil = susun_masukan(BERSIH | {"raw_api_response": "x" * (BATAS_BYTE * 2)})

        assert hasil.ukuran_byte < BATAS_BYTE

    def test_ukuran_diukur_pada_byte_bukan_karakter(self) -> None:
        """Yang membebani API dan token adalah yang dikirim. Satu karakter
        non-ASCII bisa tiga byte, dan menghitung karakter membuat batasnya
        bocor sampai tiga kali lipat pada teks Indonesia atau Mandarin."""
        m = Masukan(bidang={"market_summary": "€"})

        assert len("€") == 1
        assert susun_masukan(m.bidang).ukuran_byte > len(m.ke_json())
