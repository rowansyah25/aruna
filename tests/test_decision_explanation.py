"""Kenapa keputusannya begitu (PASAL 14.29).

Kalimat generik bukan sekadar tidak berguna - ia terbaca seperti alasan, jadi
pembacanya merasa sudah diberi tahu sesuatu dan berhenti bertanya. Keputusan
tanpa alasan sama sekali setidaknya jujur tentang apa yang tidak diketahuinya.
"""

from __future__ import annotations

import pytest

from aruna.decision import Arah
from aruna.decision.explanation import (
    MIN_SUMBER,
    Alasan,
    ExplanationError,
    Penjelasan,
    Sumber,
)

#: Contoh PASAL 14.29, dipecah menurut sumbernya: struktur, volume, strategi.
CONTOH = Penjelasan(
    decision=Arah.LONG,
    reasons=(
        Alasan(Sumber.STRUKTUR, "struktur bullish 15m masih utuh"),
        Alasan(Sumber.VOLUME, "volume mengonfirmasi breakout"),
        Alasan(
            Sumber.STRATEGI,
            "strategi trend-continuation kuat pada rezim pasar saat ini",
        ),
    ),
    against=(Alasan(Sumber.MOMENTUM, "momentum 10m melemah"),),
)


class TestKalimatKosongDitolak:
    def test_contoh_terlarang_pasal_1429_ditolak(self) -> None:
        """"DILARANG: 'Market terlihat bagus.'" """
        with pytest.raises(ExplanationError, match="kalimat kosong"):
            Alasan(Sumber.STRUKTUR, "Market terlihat bagus.")

    def test_alasan_tanpa_isi_ditolak(self) -> None:
        with pytest.raises(ExplanationError, match="tanpa isi"):
            Alasan(Sumber.VOLUME, "   ")

    def test_klaim_terlarang_pasal_51_ditolak_bukan_disensor(self) -> None:
        """Ini teks tulisan ARUNA sendiri. Menyensornya akan menyembunyikan bug
        di lapisan yang menyusun kalimatnya."""
        with pytest.raises(ExplanationError, match="PASAL 51"):
            Alasan(Sumber.STRATEGI, "strategi ini pasti profit")

    def test_kalimat_berisi_lolos(self) -> None:
        assert Alasan(Sumber.VOLUME, "volume 2.4x rata-rata 20 bar").line()


class TestSumberWajibBerbeda:
    def test_contoh_pasal_1429_lolos(self) -> None:
        assert CONTOH.sources == (Sumber.STRUKTUR, Sumber.VOLUME, Sumber.STRATEGI)

    def test_satu_sumber_saja_ditolak(self) -> None:
        """Keputusan yang berdiri di atas satu klaim runtuh bersama klaim itu."""
        with pytest.raises(ExplanationError, match="sumber berbeda"):
            Penjelasan(
                decision=Arah.LONG,
                reasons=(Alasan(Sumber.STRUKTUR, "struktur bullish"),),
            )

    def test_dua_kalimat_satu_sumber_tetap_satu_alasan(self) -> None:
        """"Struktur 15m bullish" dan "struktur masih utuh" terdengar seperti
        dua bukti; keduanya satu pengamatan yang ditulis dua kali."""
        with pytest.raises(ExplanationError, match="diulang"):
            Penjelasan(
                decision=Arah.LONG,
                reasons=(
                    Alasan(Sumber.STRUKTUR, "struktur 15m bullish"),
                    Alasan(Sumber.STRUKTUR, "struktur masih utuh"),
                    Alasan(Sumber.STRUKTUR, "higher low terbentuk"),
                ),
            )

    def test_dua_sumber_berbeda_sudah_cukup(self) -> None:
        """Ambang tiga akan menolak kasus dua-sumber yang jujur, dan yang
        terjadi berikutnya adalah alasan ketiga yang ditulis untuk memenuhi
        hitungan."""
        p = Penjelasan(
            decision=Arah.SHORT,
            reasons=(
                Alasan(Sumber.STRUKTUR, "lower high terbentuk di 1h"),
                Alasan(Sumber.VOLUME, "volume jual naik 3x"),
            ),
        )

        assert len(p.sources) == MIN_SUMBER

    def test_bukti_yang_melawan_tidak_ikut_menghitung(self) -> None:
        """Bukti penentang tidak boleh menambal kekurangan bukti pendukung."""
        with pytest.raises(ExplanationError):
            Penjelasan(
                decision=Arah.LONG,
                reasons=(Alasan(Sumber.STRUKTUR, "struktur bullish"),),
                against=(
                    Alasan(Sumber.VOLUME, "volume tipis"),
                    Alasan(Sumber.RISIKO, "spread lebar"),
                ),
            )


class TestJudul:
    def test_judulnya_ikut_arahnya(self) -> None:
        assert CONTOH.title() == "KENAPA LONG"

    def test_no_signal_juga_wajib_dijelaskan(self) -> None:
        """PASAL 14.29 menyebut WHY NO SIGNAL sejajar dengan WHY LONG."""
        p = Penjelasan(
            decision=Arah.NO_SIGNAL,
            reasons=(
                Alasan(Sumber.TIMEFRAME, "1h dan 15m berlawanan arah"),
                Alasan(Sumber.RISIKO, "imbalan 0.6x dari risikonya"),
            ),
        )

        assert p.title() == "KENAPA NO SIGNAL"


class TestLaporan:
    def test_tiap_alasan_menyebut_sumbernya(self) -> None:
        teks = "\n".join(CONTOH.report())

        assert "(struktur pasar)" in teks
        assert "(volume)" in teks
        assert "(strategi historis)" in teks

    def test_yang_melawan_ikut_dicetak(self) -> None:
        """Penjelasan yang hanya memuat hal yang mendukung bukan penjelasan -
        ia pembelaan."""
        teks = "\n".join(CONTOH.report())

        assert "Yang melawan:" in teks
        assert "momentum 10m melemah" in teks

    def test_tanpa_penentang_tidak_mencetak_blok_kosong(self) -> None:
        p = Penjelasan(
            decision=Arah.LONG,
            reasons=(
                Alasan(Sumber.STRUKTUR, "higher low 1h"),
                Alasan(Sumber.VOLUME, "volume naik"),
            ),
        )

        assert "Yang melawan:" not in "\n".join(p.report())
