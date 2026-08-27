"""Koreksi diri XAU: yang dikoreksi, dan yang sengaja TIDAK dikoreksi."""

from __future__ import annotations

from aruna.core.enums import AgentRole
from aruna.xau.koreksi import KOREKSI_TIAP, hitung_koreksi, perlu_koreksi


def _baris(agen: str, benar: int, salah: int) -> list[dict]:
    """Suara satu agen dengan campuran arah SEIMBANG dan akurasi tertentu.

    Arahnya diselang-seling dengan sengaja. Seorang agen yang selalu bilang
    BUY tidak bisa dinilai: di sampel yang BUY-nya menang 87%, garis dasarnya
    juga 87%, jadi edge-nya nol dan multipliernya 1,0 - mesinnya bekerja benar,
    dan data uji yang selalu searah tidak menguji apa pun.
    """
    keluar = []
    for i in range(benar + salah):
        arah = "BUY" if i % 2 == 0 else "SELL"
        keluar.append(
            {
                "agent": agen,
                "agent_decision": arah,
                "council_decision": arah,
                "direction_correct": i < benar,
            }
        )
    return keluar


class TestPemicu:
    def test_belum_cukup_belum_memicu(self) -> None:
        assert perlu_koreksi(KOREKSI_TIAP - 1, 0) is False

    def test_tepat_di_ambang_memicu(self) -> None:
        assert perlu_koreksi(KOREKSI_TIAP, 0) is True

    def test_tidak_memicu_ulang_untuk_hitungan_yang_sama(self) -> None:
        """Sisa bagi akan memicu berkali-kali selama hitungannya tidak
        bertambah - dan hitungan memang tidak bertambah di antara dua hasil."""
        assert perlu_koreksi(KOREKSI_TIAP, KOREKSI_TIAP) is False

    def test_kelipatan_berikutnya_memicu_lagi(self) -> None:
        assert perlu_koreksi(KOREKSI_TIAP * 2, KOREKSI_TIAP) is True

    def test_lompat_dua_kelipatan_tetap_sekali(self) -> None:
        """Beberapa hasil terselesaikan bersamaan tidak boleh jadi dua putaran."""
        assert perlu_koreksi(KOREKSI_TIAP * 3, KOREKSI_TIAP) is True


class TestSampelKurang:
    def test_kosong_dicatat_tidak_diterapkan(self) -> None:
        """Tanpa barisnya, 'belum cukup bahan' dan 'tidak pernah dijalankan'
        terlihat sama persis - dan yang kedua adalah kerusakan."""
        h = hitung_koreksi([], putaran=1, dipicu_oleh=10)
        assert h.diterapkan is False
        assert h.bobot == {}
        assert "tidak ada agen yang cukup sampelnya" in h.alasan

    def test_sedikit_suara_belum_cukup(self) -> None:
        h = hitung_koreksi(_baris("TECHNICAL", 3, 1), putaran=1, dipicu_oleh=10)
        assert h.diterapkan is False
        assert h.sampel == 4

    def test_yang_gagal_tetap_menyimpan_bahannya(self) -> None:
        h = hitung_koreksi(_baris("TECHNICAL", 3, 1), putaran=1, dipicu_oleh=10)
        assert h.sampel == 4
        assert h.garis_dasar is not None


class TestBobotTerukur:
    def test_agen_yang_sering_benar_naik(self) -> None:
        baris = _baris("TECHNICAL", 28, 4)
        h = hitung_koreksi(baris, putaran=1, dipicu_oleh=40)
        assert h.diterapkan is True
        assert h.bobot["TECHNICAL"] > 1.0

    def test_agen_yang_sering_salah_turun(self) -> None:
        baris = _baris("REVERSAL", 6, 26)
        h = hitung_koreksi(baris, putaran=1, dipicu_oleh=40)
        assert h.bobot["REVERSAL"] < 1.0

    def test_garis_dasar_diukur_dari_baris_yang_sama(self) -> None:
        """Bukan konstanta. Agen yang selalu BUY di pasar yang naik 60% waktu
        tidak punya keahlian - ia punya keberuntungan yang bisa dihitung."""
        h = hitung_koreksi(_baris("TECHNICAL", 28, 4), putaran=1, dipicu_oleh=40)
        assert h.garis_dasar is not None
        assert 0.0 <= h.garis_dasar <= 1.0

    def test_agen_tanpa_sampel_cukup_tidak_muncul_di_bobot(self) -> None:
        """Ketiadaan berarti tidak diukur. Menuliskannya 1,0 membuatnya tak
        bisa dibedakan dari yang diukur lalu ternyata netral."""
        baris = _baris("TECHNICAL", 28, 4) + _baris("NEWS", 2, 1)
        h = hitung_koreksi(baris, putaran=1, dipicu_oleh=40)
        assert "TECHNICAL" in h.bobot
        assert "NEWS" not in h.bobot


class TestVersiDanFallback:
    def test_versi_menunjuk_pendahulunya(self) -> None:
        h = hitung_koreksi(
            _baris("TECHNICAL", 28, 4),
            putaran=2,
            dipicu_oleh=20,
            versi_sebelumnya="xau-m5-1",
        )
        assert h.versi == "xau-m5-2"
        assert h.versi_sebelumnya == "xau-m5-1"

    def test_putaran_pertama_tanpa_pendahulu(self) -> None:
        h = hitung_koreksi(_baris("TECHNICAL", 28, 4), putaran=1, dipicu_oleh=10)
        assert h.versi_sebelumnya is None


class TestBobotYangBerlaku:
    """Bobot dari putaran yang GAGAL tidak boleh berlaku - itu sebabnya
    barisnya tetap ditulis: supaya kegagalan terlihat tanpa ikut bekerja."""

    def test_belum_pernah_koreksi_kosong(self) -> None:
        from aruna.xau.koreksi import bobot_yang_berlaku

        assert bobot_yang_berlaku(None) == {}

    def test_putaran_gagal_tidak_berlaku(self) -> None:
        from aruna.xau.koreksi import bobot_yang_berlaku

        gagal = {"diterapkan": False, "bobot": {"TECHNICAL": 9.0}}
        assert bobot_yang_berlaku(gagal) == {}

    def test_putaran_berhasil_berlaku(self) -> None:
        from aruna.xau.koreksi import bobot_yang_berlaku

        ok = {"diterapkan": True, "bobot": {"TECHNICAL": 1.2}}
        assert bobot_yang_berlaku(ok) == {"TECHNICAL": 1.2}

    def test_json_dari_basis_data_terbaca(self) -> None:
        """MySQL memulangkan kolom JSON sebagai string."""
        from aruna.xau.koreksi import bobot_yang_berlaku

        ok = {"diterapkan": True, "bobot": '{"TECHNICAL": 1.2}'}
        assert bobot_yang_berlaku(ok) == {"TECHNICAL": 1.2}

    def test_json_rusak_tidak_menjatuhkan(self) -> None:
        from aruna.xau.koreksi import bobot_yang_berlaku

        assert bobot_yang_berlaku({"diterapkan": True, "bobot": "{rusak"}) == {}


class TestBobotMengubahKeputusan:
    """Bobot yang tidak mengubah apa pun lebih buruk daripada tak ada bobot -
    laporannya mengaku menyetel diri sementara tak satu keputusan pun bergeser.
    """

    def _rekap(self, bobot):
        from aruna.core.enums import Decision
        from aruna.xau.suara import RekapSuara, Suara, SuaraAgen

        rincian = (
            SuaraAgen(AgentRole.TECHNICAL, Suara.AGREE, Decision.BUY, 0.9, False),
            SuaraAgen(AgentRole.REVERSAL, Suara.DISAGREE, Decision.SELL, 0.9, False),
        )
        terbobot = {"AGREE": 0.0, "DISAGREE": 0.0}
        if bobot:
            for s in rincian:
                terbobot[s.suara.value] += s.confidence * bobot.get(s.role.value, 1.0)
        return RekapSuara(
            setuju=1, menentang=1, netral=0, rincian=rincian,
            bobot_setuju=terbobot["AGREE"], bobot_menentang=terbobot["DISAGREE"],
        )

    def test_tanpa_bobot_terbelah_rata(self) -> None:
        assert self._rekap({}).kontradiksi == 1.0

    def test_dengan_bobot_penentang_lemah_kontradiksi_turun(self) -> None:
        """REVERSAL yang terbukti sering meleset tidak boleh membelah dewan
        sekuat TECHNICAL yang terbukti benar."""
        rekap = self._rekap({"TECHNICAL": 1.3, "REVERSAL": 0.4})
        assert rekap.berbobot is True
        assert rekap.kontradiksi < 1.0

    def test_bobot_bisa_membalikkan_gerbang(self) -> None:
        """Inti seluruh koreksi diri: ia harus sanggup mengubah putusan."""
        from aruna.xau.keputusan import MAX_KONTRADIKSI

        assert self._rekap({}).kontradiksi > MAX_KONTRADIKSI
        assert self._rekap({"TECHNICAL": 1.5, "REVERSAL": 0.3}).kontradiksi <= (
            MAX_KONTRADIKSI
        )


class TestYangSengajaTidakDikoreksi:
    def test_ambang_gerbang_tidak_ikut_disetel(self) -> None:
        """Menyetel ambang terhadap hasilnya sendiri adalah cara tercepat
        menaikkan win rate di atas kertas tanpa satu keputusan pun membaik.
        Spec menyebutnya overfitting dan melarangnya.

        Dipindai, bukan diingat: yang berikutnya menambahkan 'sedikit
        penyetelan ambang' tidak akan membaca komentar.
        """
        from pathlib import Path

        sumber = (
            Path(__file__).resolve().parent.parent
            / "src" / "aruna" / "xau" / "koreksi.py"
        ).read_text(encoding="utf-8")
        for ambang in ("MIN_RR", "MAX_KONTRADIKSI", "MIN_TARGET_ATR", "STOP_ATR"):
            assert f"{ambang} =" not in sumber
            assert f"{ambang}=" not in sumber
