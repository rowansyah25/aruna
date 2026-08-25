"""Ambang MOMENTUM duduk di ekor sebaran, bukan di tengahnya.

Diukur 2026-08-25 atas 22.540 jendela di dua puluh lima simbol 15m: sebaran
``momentum`` bermedian +0,06 dengan p90 +1,57. Ambang agen 1,5 karena itu
menyala pada 16,7% kasus dan seluruhnya di ekor - wilayah yang docstring modul
itu sendiri serahkan ke REVERSAL.

Berkas ini tidak menguji "ambangnya benar". Ia menjaga supaya angka itu tidak
berubah tanpa seseorang membaca kenapa ia ada, dan supaya keterangan yang
menjelaskannya tidak hilang saat kode dirapikan.
"""

from __future__ import annotations

import inspect

from aruna.agents.market import AMBANG_MOMENTUM, MomentumAgent


class TestAmbangnyaPunyaAsalUsul:
    def test_bukan_angka_telanjang_di_tengah_fungsi(self) -> None:
        """Angka ajaib di dalam `evaluate` tidak bisa dicari, tidak bisa
        dibantah, dan tidak punya tempat untuk menaruh alasannya."""
        sumber = inspect.getsource(MomentumAgent.evaluate)

        assert "AMBANG_MOMENTUM" in sumber
        assert "1.5" not in sumber.split("bull += ")[0]

    def test_alasannya_ikut_tersimpan(self) -> None:
        """Yang dijaga di sini keterangannya, bukan angkanya.

        Sebuah konstanta yang alasannya hilang akan diubah oleh orang
        berikutnya berdasarkan intuisi - dan intuisi tentang ambang momentum
        persis yang membuat angka ini mendarat di p90 tanpa ada yang sadar.
        """
        from aruna.agents import market

        sumber = inspect.getsource(market)
        potongan = sumber.split("AMBANG_MOMENTUM = ")[0]

        for kata in ("p90", "16,7%", "REVERSAL"):
            assert kata in potongan, kata

    def test_menyala_hanya_di_ekor(self) -> None:
        """p90 terukur +1,57. Ambang di bawahnya berarti agen ini berhenti
        menjadi pembaca ekor - dan itu perubahan perilaku yang harus disengaja,
        bukan efek samping merapikan angka."""
        assert AMBANG_MOMENTUM >= 1.0, (
            "ambang di bawah 1,0 menempatkan MOMENTUM di tengah sebaran, "
            "bukan di ekornya - perubahan perilaku yang butuh bukti, bukan "
            "kerapian"
        )


class TestTumpangTindihDenganReversal:
    def test_docstringnya_mengakui_tumpang_tindihnya(self) -> None:
        """Komentar lama berkata ekstrem diserahkan ke REVERSAL, sementara
        ambangnya justru menempatkan MOMENTUM di ekstrem. Dua agen dengan tesis
        berlawanan membaca wilayah yang sama, dan yang menang bukan yang benar
        melainkan yang berbobot lebih besar.
        """
        doc = MomentumAgent.__doc__ or ""

        assert "REVERSAL" in doc
        assert "AMBANG_MOMENTUM" in doc
