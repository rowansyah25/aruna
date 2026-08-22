"""Apa yang ditanyakan pada simulasi (bagian 16.4).

Contoh terlarang bagian 16.4 sendiri - *"Apakah BTC akan naik?"* - dipakai
langsung sebagai kasus uji, supaya test ini menolak persis kalimat yang spec-nya
tolak, bukan tafsiranku tentangnya.
"""

from __future__ import annotations

import pytest

from aruna.scenario.pemicu import Peristiwa
from aruna.scenario.pertanyaan import (
    MINIMUM_KONDISI,
    PertanyaanDitolak,
    periksa_pertanyaan,
    susun_pertanyaan,
)

PEMICU = frozenset({Peristiwa.BREAKOUT_BESAR, Peristiwa.VOLUME_EKSTREM})
KONDISI = ("harga > resistance 24 jam", "volume 4,2x rata-rata", "OI naik 3,1%")


def _pertanyaan(**kw) -> str:
    return susun_pertanyaan(
        **{"aset": "BTC/USDT", "pemicu": PEMICU, "kondisi": KONDISI} | kw
    )


class TestMenyebutKondisiKonkret:
    def test_kondisinya_muncul_di_pertanyaan(self) -> None:
        p = _pertanyaan()

        for k in KONDISI:
            assert k in p

    def test_asetnya_disebut(self) -> None:
        assert "BTC/USDT" in _pertanyaan()

    def test_pemicunya_disebut(self) -> None:
        """Pertanyaan yang tidak menyebut apa yang membangunkannya tidak bisa
        diperiksa ulang: pembacanya tak punya cara tahu apakah simulasinya
        pantas dijalankan sama sekali."""
        p = _pertanyaan()

        assert Peristiwa.BREAKOUT_BESAR.value in p
        assert Peristiwa.VOLUME_EKSTREM.value in p

    def test_meminta_beberapa_kemungkinan(self) -> None:
        """Bentuk yang bagian 16.4 minta, bukan sekadar bukan-ya/tidak."""
        p = _pertanyaan().lower()

        assert "simulasikan" in p
        assert "kemungkinan" in p

    def test_meminta_syarat_pembatal(self) -> None:
        """Bagian 16.11: tiap skenario butuh invalidation condition, dan
        pertanyaan yang tidak memintanya akan dijawab tanpanya."""
        assert "membatalkan" in _pertanyaan().lower()

    def test_tanpa_kondisi_ditolak(self) -> None:
        with pytest.raises(PertanyaanDitolak, match="kondisi"):
            _pertanyaan(kondisi=())

    def test_satu_kondisi_cukup(self) -> None:
        """Perubahan regime sendirian adalah peristiwa yang sah; menuntut tiga
        kondisi seperti contoh spec akan menolaknya."""
        assert MINIMUM_KONDISI == 1
        assert _pertanyaan(kondisi=("regime berpindah ke TRENDING_BEARISH",))

    def test_tanpa_pemicu_ditolak(self) -> None:
        """Bagian 16.2: simulasi yang dibangunkan tanpa peristiwa adalah
        simulasi di tiap scan."""
        with pytest.raises(PertanyaanDitolak, match="pemicu"):
            _pertanyaan(pemicu=frozenset())


class TestBentukYaTidakDitolak:
    """Bagian 16.4."""

    @pytest.mark.parametrize(
        "buruk",
        [
            "Apakah BTC akan naik?",
            "apakah btc/usdt akan turun dalam 4 jam?",
            "BTC akan naik atau tidak?",
            "Will BTC go up next hour?",
            "Should ETH rise after the breakout?",
            "buy or sell BTC now",
            "beli atau jual sekarang",
            "Ya, simulasikan kemungkinan perkembangan.",
        ],
    )
    def test_ditolak(self, buruk) -> None:
        with pytest.raises(PertanyaanDitolak, match=r"16\.4"):
            periksa_pertanyaan(buruk)

    def test_contoh_spec_yang_dilarang_persis(self) -> None:
        """Kalimat yang bagian 16.4 kutip sebagai contoh terlarang."""
        with pytest.raises(PertanyaanDitolak):
            periksa_pertanyaan("Apakah BTC akan naik?")

    def test_contoh_spec_yang_diminta_lolos(self) -> None:
        """Kalimat yang bagian 16.4 kutip sebagai contoh yang benar."""
        periksa_pertanyaan(
            "Simulasikan beberapa kemungkinan perkembangan berdasarkan "
            "kondisi saat ini."
        )

    def test_pertanyaan_susunan_sendiri_lolos_penjaganya(self) -> None:
        """Penjaganya dijalankan oleh penyusunnya, bukan hanya tersedia di
        sebelahnya - kalau tidak, penyusun yang kelak berubah bisa
        menghasilkan bentuk terlarang tanpa satu pun test merah."""
        periksa_pertanyaan(_pertanyaan())

    def test_penjaganya_benar_benar_dipanggil_penyusun(self) -> None:
        """Cabut-uji dalam bentuk test: kalau `susun_pertanyaan` berhenti
        memanggil penjaganya, kalimat berarah yang diselipkan lewat `kondisi`
        akan lolos."""
        with pytest.raises(PertanyaanDitolak):
            _pertanyaan(kondisi=("apakah harga akan naik",))
