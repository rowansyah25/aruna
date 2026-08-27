"""Dimensi ketiga: apa yang operator dapat kalau mengikuti ARUNA.

Operator memutuskan: menyuruh tutup saat untung terhitung menang. Berkas ini
menjaga keputusan itu dari berubah jadi angka yang tidak berarti.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.core.enums import Decision
from aruna.xau.resolve import (
    MIN_R_UNTUK_WIN,
    HasilAkhir,
    LevelTersentuh,
    nilai_hasil_akhir,
    r_multiple,
)


class TestRMultiple:
    def test_untung_diukur_terhadap_jarak_stop(self) -> None:
        """R adalah persis yang dipertaruhkan kalau bacaannya salah."""
        # entry 1000, stop 990 -> risiko 10. Harga 1005 -> +0,5 R.
        r = r_multiple(Decimal("1000"), Decimal("990"), Decimal("1005"), Decision.BUY)
        assert r == Decimal("0.5")

    def test_sell_berlawanan_arah(self) -> None:
        r = r_multiple(Decimal("1000"), Decimal("1010"), Decimal("995"), Decision.SELL)
        assert r == Decimal("0.5")

    def test_rugi_bertanda_negatif(self) -> None:
        r = r_multiple(Decimal("1000"), Decimal("990"), Decimal("996"), Decision.BUY)
        assert r == Decimal("-0.4")

    def test_risiko_nol_tidak_terukur(self) -> None:
        """Membagi dengan nol akan mengarang angka tak terhingga."""
        assert r_multiple(
            Decimal("1000"), Decimal("1000"), Decimal("1005"), Decision.BUY
        ) is None


class TestLevelTersentuhMenentukan:
    def test_target_menang(self) -> None:
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TARGET, disuruh_tutup=None, r=Decimal("3")
        )
        assert hasil is HasilAkhir.TARGET
        assert menang is True

    def test_stop_kalah(self) -> None:
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.STOP, disuruh_tutup=None, r=Decimal("-1")
        )
        assert hasil is HasilAkhir.STOP
        assert menang is False


class TestTutupSaatUntung:
    """Keputusan operator: peringatan yang nyata pada harga nyata = hasil nyata."""

    def test_tutup_di_atas_ambang_menang(self) -> None:
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=True,
            r=MIN_R_UNTUK_WIN,
        )
        assert hasil is HasilAkhir.TUTUP_UNTUNG
        assert menang is True

    def test_tutup_di_bawah_ambang_BUKAN_menang(self) -> None:
        """Ini penjaganya. Untung sepeser pun bukan kemenangan - harga yang
        bergerak +0,01% adalah derau satu bar, dan menghitungnya menang
        membuat win rate naik tanpa satu keputusan pun membaik."""
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=True,
            r=Decimal("0.05"),
        )
        assert hasil is HasilAkhir.TUTUP_RUGI
        assert menang is False

    def test_tutup_saat_rugi_kalah(self) -> None:
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=True,
            r=Decimal("-0.3"),
        )
        assert hasil is HasilAkhir.TUTUP_RUGI
        assert menang is False

    def test_r_tak_terukur_tidak_dihitung_menang(self) -> None:
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN, disuruh_tutup=True, r=None
        )
        assert menang is False


class TestTahanBelumDinilai:
    def test_disuruh_tahan_belum_ada_hasil(self) -> None:
        """Menghitungnya kalah akan menghukum kesabaran yang ARUNA sendiri
        sarankan; menghitungnya menang akan mengarang hasil yang belum ada."""
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=False,
            r=Decimal("0.9"),
        )
        assert hasil is HasilAkhir.TAHAN
        assert menang is None

    def test_belum_ada_putusan_belum_dinilai(self) -> None:
        hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN, disuruh_tutup=None, r=Decimal("2")
        )
        assert hasil is HasilAkhir.TAHAN
        assert menang is None

    def test_tahan_dengan_untung_besar_tetap_belum_dinilai(self) -> None:
        """Posisinya belum ditutup, jadi untungnya belum jadi milik siapa pun."""
        _hasil, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=False,
            r=Decimal("5"),
        )
        assert menang is None


class TestAmbangnyaBerarti:
    def test_ambang_bukan_nol(self) -> None:
        """Ambang nol berarti tiap gerak positif jadi kemenangan, dan angka
        win rate berhenti berarti."""
        assert MIN_R_UNTUK_WIN > 0

    def test_tepat_di_ambang_menang(self) -> None:
        _h, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=True,
            r=MIN_R_UNTUK_WIN,
        )
        assert menang is True

    def test_sedikit_di_bawah_ambang_kalah(self) -> None:
        _h, menang = nilai_hasil_akhir(
            level=LevelTersentuh.TIDAK_SATU_PUN,
            disuruh_tutup=True,
            r=MIN_R_UNTUK_WIN - Decimal("0.01"),
        )
        assert menang is False


class TestTigaDimensiTerpisah:
    def test_menang_tidak_menggantikan_arah_benar(self) -> None:
        """Tiga dimensi, dan tak satu pun boleh menggantikan yang lain.

        Sebuah TUTUP_UNTUNG bisa terjadi pada arah yang benar maupun - lewat
        gerak yang berbalik di akhir - pada bacaan yang meleset. Menyatukannya
        menghapus tepat perbedaan yang menentukan apa yang harus diperbaiki.
        """
        import inspect

        from aruna.xau import resolve

        sumber = inspect.getsource(resolve.nilai_hasil_akhir)
        assert "arah_benar" not in sumber, (
            "hasil_akhir tidak boleh dihitung dari arah_benar - keduanya "
            "menjawab pertanyaan yang berbeda"
        )

    def test_no_signal_tidak_pernah_sampai_sini(self) -> None:
        from aruna.xau.resolve import nilai_hasil

        with pytest.raises(ValueError, match="berarah"):
            nilai_hasil(1, None, Decision.NO_SIGNAL, [])
