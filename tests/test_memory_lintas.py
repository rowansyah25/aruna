"""PASAL 15.18: aset lain sebagai konteks - dan batas yang jujur tentangnya.

Pasalnya mencontohkan DXY dan emas: "broad risk-on environment". **Keduanya
tidak ada di universe ARUNA** - terukur 2026-08-21, `assets` berisi 31 baris,
seluruhnya pasangan USDT kripto dan saham IDX. Tidak ada indeks dolar, tidak
ada logam.

Yang bisa dijawab jujur karena itu lebih sempit, dan namanya harus menyebut
kesempitannya: berapa banyak aset **kripto** yang sedang berada di rezim yang
sama. Menyebutnya "risk-on environment" akan mengklaim pengamatan lintas kelas
aset yang tidak pernah dilakukan.

Dan satu hal lagi yang pasalnya eja sendiri: cross-asset context **tidak boleh
menjadi keputusan tunggal**. Modul ini karena itu tidak memulangkan arah.
"""

from __future__ import annotations

import pytest

from aruna.memory.lintas import (
    AMBANG_SEJALAN,
    LintasAset,
    baca_lintas,
)


def _baris(
    n: int, rezim: str, arah: str = "BUY", *, mulai: int = 0
) -> list[dict[str, str]]:
    """``mulai`` supaya dua kelompok tidak memakai nama simbol yang sama.

    Versi pertama helper ini menomori dari nol pada tiap kelompok, jadi
    delapan TRENDING dan dua RANGING menghasilkan **delapan** simbol - dedup
    per simbol memakan yang kedua. Palsu yang bentuknya salah membuat test
    menuduh kode yang benar.
    """
    return [
        {"symbol": f"AST{i}/USDT", "regime": rezim, "arah": arah}
        for i in range(mulai, mulai + n)
    ]


class TestPembacaannya:
    def test_menghitung_yang_serezim(self) -> None:
        lintas = baca_lintas(_baris(8, "TRENDING") + _baris(2, "RANGING", mulai=100),
                             rezim_sekarang="TRENDING")

        assert lintas.sejalan == 8
        assert lintas.total == 10

    def test_rezim_yang_tidak_diketahui_tidak_dihitung_sejalan(self) -> None:
        """UNKNOWN bukan kecocokan - aturan yang sama dengan sidik jarinya."""
        lintas = baca_lintas(_baris(5, "UNKNOWN"), rezim_sekarang="UNKNOWN")

        assert lintas.sejalan == 0

    def test_kosong_bukan_nol_persen(self) -> None:
        lintas = baca_lintas([], rezim_sekarang="TRENDING")

        assert lintas.total == 0
        assert lintas.pct is None

    def test_satu_aset_tidak_bisa_disebut_lintas_aset(self) -> None:
        """Konteks lintas aset yang dihitung dari satu aset adalah konteks aset
        itu sendiri dengan nama yang lebih meyakinkan."""
        lintas = baca_lintas(_baris(1, "TRENDING"), rezim_sekarang="TRENDING")

        assert not lintas.luas

    def test_pctnya_terbaca(self) -> None:
        lintas = baca_lintas(_baris(7, "TRENDING") + _baris(3, "RANGING", mulai=100),
                             rezim_sekarang="TRENDING")

        assert lintas.pct == 70


class TestTidakMemutuskan:
    def test_tidak_ada_bidang_arah(self) -> None:
        """PASAL 15.18: cross-asset context tidak boleh menjadi keputusan
        tunggal. Sebuah bidang ``arah`` di sini akan dibaca sebagai satu."""
        lintas = baca_lintas(_baris(8, "TRENDING"), rezim_sekarang="TRENDING")

        assert not hasattr(lintas, "arah")
        assert not hasattr(lintas, "keputusan")

    def test_bekunya_dijaga(self) -> None:
        from dataclasses import FrozenInstanceError

        lintas = baca_lintas(_baris(8, "TRENDING"), rezim_sekarang="TRENDING")

        with pytest.raises(FrozenInstanceError):
            lintas.sejalan = 0  # type: ignore[misc]


class TestKalimatnya:
    def test_menyebut_kripto_bukan_pasar_secara_umum(self) -> None:
        """Terukur: tidak ada DXY dan tidak ada emas di universe. Menyebut
        "pasar" atau "risk-on" akan mengklaim pengamatan lintas kelas aset yang
        tidak pernah dilakukan."""
        kalimat = baca_lintas(
            _baris(8, "TRENDING"), rezim_sekarang="TRENDING"
        ).ringkas()

        assert "kripto" in kalimat.lower()

    def test_tidak_pernah_mengucapkan_risk_on(self) -> None:
        for rezim in ("TRENDING", "RANGING", "BREAKOUT"):
            kalimat = baca_lintas(
                _baris(9, rezim), rezim_sekarang=rezim
            ).ringkas().lower()
            for terlarang in ("risk-on", "risk on", "bullish market",
                              "pasti", "peluang"):
                assert terlarang not in kalimat

    def test_yang_sempit_tidak_dicetak_sebagai_konteks_luas(self) -> None:
        assert baca_lintas(_baris(1, "TRENDING"),
                           rezim_sekarang="TRENDING").ringkas() == ""


class TestAmbangnya:
    def test_ambang_sejalan_masuk_akal(self) -> None:
        """Di bawah setengah bukan "sejalan" - itu terbelah."""
        assert 50 < AMBANG_SEJALAN <= 100

    def test_bentuknya_lintasaset(self) -> None:
        assert isinstance(
            baca_lintas(_baris(3, "TRENDING"), rezim_sekarang="TRENDING"),
            LintasAset,
        )
