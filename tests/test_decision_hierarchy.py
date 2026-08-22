"""Urutan yang harus dilewati sebuah keputusan (PASAL 14.3).

Analisis multi-timeframe yang berjalan sebelum kesegaran datanya diperiksa
menghasilkan angka yang rapi tentang harga yang mungkin sudah basi. Keluarannya
terlihat lengkap - itulah kenapa urutannya harus dijaga, bukan cuma
kelengkapannya.
"""

from __future__ import annotations

import pytest

from aruna.decision.hierarchy import (
    URUTAN,
    WAJIB,
    HierarchyError,
    Jalur,
    Tahap,
)

#: Empat belas langkah PASAL 14.3, diketik ulang dari spesifikasinya.
PASAL_14_3 = [
    "keabsahan data",
    "kesegaran data",
    "rezim pasar",
    "analisis multi-timeframe",
    "analisis agent",
    "protes agent",
    "suara council",
    "signal quality",
    "performa strategi historis",
    "analisis risiko",
    "risk/reward",
    "syarat pembatalan",
    "horizon keputusan",
    "keputusan final",
]


def lewati(*tahap: Tahap) -> Jalur:
    j = Jalur()
    for t in tahap:
        j = j.advance(t)
    return j


class TestUrutannya:
    def test_empat_belas_langkah_persis_seperti_pasalnya(self) -> None:
        assert [t.value for t in URUTAN] == PASAL_14_3

    def test_jalur_lengkap_dari_awal_sampai_akhir(self) -> None:
        j = lewati(*URUTAN)

        assert len(j.done) == len(URUTAN)
        assert j.may_decide
        assert j.skipped == ()

    def test_langkah_terbalik_ditolak(self) -> None:
        """Council yang memutuskan sebelum rezimnya diklasifikasi berdebat
        tanpa tahu pasar macam apa yang sedang dibacanya."""
        j = lewati(Tahap.DATA_VALIDITY, Tahap.DATA_FRESHNESS, Tahap.COUNCIL)

        with pytest.raises(HierarchyError, match="mendahuluinya"):
            j.advance(Tahap.MARKET_REGIME)

    def test_langkah_ganda_ditolak(self) -> None:
        j = lewati(Tahap.DATA_VALIDITY)

        with pytest.raises(HierarchyError, match="dua kali"):
            j.advance(Tahap.DATA_VALIDITY)

    def test_jalurnya_tidak_bisa_disunting_di_tempat(self) -> None:
        """Jejak yang bisa berubah sesudah keputusannya dibaca bukan jejak."""
        awal = lewati(Tahap.DATA_VALIDITY)
        lanjut = awal.advance(Tahap.DATA_FRESHNESS)

        assert awal.done == (Tahap.DATA_VALIDITY,)
        assert lanjut is not awal


class TestLangkahBolehTidakAda:
    def test_melompati_langkah_tidak_wajib_diperbolehkan(self) -> None:
        """Strategi historis butuh sampel yang belum tentu ada."""
        j = lewati(
            Tahap.DATA_VALIDITY, Tahap.DATA_FRESHNESS, Tahap.MARKET_REGIME,
            Tahap.RISK,
        )

        assert j.may_decide

    def test_yang_dilompati_tetap_disebut(self) -> None:
        j = lewati(Tahap.DATA_VALIDITY, Tahap.DATA_FRESHNESS, Tahap.RISK)

        assert Tahap.MARKET_REGIME in j.skipped
        assert Tahap.COUNCIL in j.skipped

    def test_langkah_yang_belum_tiba_bukan_langkah_yang_dilewati(self) -> None:
        """Menghitungnya sebagai lewat membuat setiap jalur yang sedang
        berjalan terlihat penuh lubang."""
        j = lewati(Tahap.DATA_VALIDITY, Tahap.DATA_FRESHNESS)

        assert j.skipped == ()
        assert Tahap.FINAL not in j.skipped

    def test_jalur_kosong_tidak_melewati_apa_pun(self) -> None:
        assert Jalur().skipped == ()
        assert "belum satu langkah" in Jalur().line()


class TestYangTidakBolehDilewati:
    def test_tiga_langkah_wajib(self) -> None:
        assert set(WAJIB) == {
            Tahap.DATA_VALIDITY, Tahap.DATA_FRESHNESS, Tahap.RISK
        }

    @pytest.mark.parametrize("hilang", sorted(WAJIB, key=lambda t: t.value))
    def test_keputusan_final_tanpa_langkah_wajib_ditolak(self, hilang) -> None:
        """PASAL 14.3: "Tidak boleh melewati data validation atau risk
        validation"."""
        j = Jalur()
        for t in URUTAN[:-1]:
            if t is not hilang:
                j = j.advance(t)

        assert not j.may_decide
        with pytest.raises(HierarchyError, match=hilang.value):
            j.advance(Tahap.FINAL)

    def test_yang_hilang_disebut_namanya(self) -> None:
        j = lewati(Tahap.DATA_VALIDITY)

        assert j.missing_mandatory == (Tahap.DATA_FRESHNESS, Tahap.RISK)
        assert "kesegaran data" in j.line()

    def test_risk_reward_tidak_wajib(self) -> None:
        """Ia butuh entry, stop, dan target yang belum tentu ada pada NO
        SIGNAL, dan gerbang yang menuntutnya akan menolak justru jalur yang
        paling sering benar."""
        assert Tahap.RR not in WAJIB

        j = Jalur()
        for t in URUTAN[:-1]:
            if t is not Tahap.RR:
                j = j.advance(t)

        assert j.advance(Tahap.FINAL).may_decide

    def test_langkah_tidak_wajib_lain_boleh_hilang(self) -> None:
        for boleh in (Tahap.STRATEGY, Tahap.INVALIDATION, Tahap.PROTEST):
            j = Jalur()
            for t in URUTAN[:-1]:
                if t is not boleh:
                    j = j.advance(t)
            assert j.advance(Tahap.FINAL).may_decide, boleh


class TestLaporan:
    def test_wajib_yang_hilang_ditandai_berbeda(self) -> None:
        teks = "\n".join(lewati(Tahap.DATA_VALIDITY).report())

        assert "[✓] keabsahan data" in teks
        assert "[✗] kesegaran data" in teks
        assert "[✗] analisis risiko" in teks
        assert "[·] rezim pasar" in teks

    def test_menyebut_tidak_boleh_memutuskan(self) -> None:
        teks = "\n".join(lewati(Tahap.DATA_VALIDITY).report())

        assert "TIDAK BOLEH memutuskan" in teks

    def test_jalur_lengkap_tidak_berteriak(self) -> None:
        teks = "\n".join(lewati(*URUTAN).report())

        assert "TIDAK BOLEH" not in teks
        assert "semua yang wajib ada" in teks

    def test_setiap_langkah_tercetak(self) -> None:
        teks = "\n".join(Jalur().report())

        for t in URUTAN:
            assert t.value in teks


class TestHubungannyaDenganDaftarPeriksa:
    def test_dua_pertanyaan_yang_berbeda(self) -> None:
        """PASAL 14.3 menanyakan urutan; PASAL 14.25 menanyakan kelengkapan.
        Keduanya punya empat belas butir, dan kemiripan itu justru yang membuat
        pembedaannya mudah hilang."""
        from aruna.decision.audit import Butir

        assert len(Butir) == len(Tahap) == 14
        # Daftar periksa punya "masa berlaku"; urutan punya "keputusan final".
        assert "masa berlaku" in {b.value for b in Butir}
        assert "keputusan final" in {t.value for t in Tahap}
        assert {b.value for b in Butir} != {t.value for t in Tahap}
