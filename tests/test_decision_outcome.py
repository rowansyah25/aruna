"""Nasib satu signal, dan apa yang dipelajari darinya (PASAL 14.31, 14.34).

Yang dijaga di sini: LOSS tidak dilunakkan, FALSE SIGNAL tidak disamakan dengan
LOSS, dan salah tanda tidak lolos ke Phase 12 sebagai kebalikan dari kenyataan.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.decision import Arah, State
from aruna.decision.outcome import (
    KEADAAN,
    Catatan,
    Hasil,
    OutcomeError,
    Sebab,
    require_analysed,
)
from aruna.decision.silence import GERAK_BERARTI_PCT


def catat(**kw) -> Catatan:
    kw.setdefault("symbol", "BTC/USDT")
    kw.setdefault("decision", Arah.LONG)
    kw.setdefault("outcome", Hasil.LOSS)
    return Catatan(**kw)


class TestEmpatAkhir:
    def test_empat_hasil_persis_seperti_pasalnya(self) -> None:
        assert {h.value for h in Hasil} == {
            "WIN", "LOSS", "INVALIDATED", "EXPIRED"
        }

    def test_tiap_hasil_punya_keadaan_akhirnya(self) -> None:
        assert set(KEADAAN) == set(Hasil)
        assert KEADAAN[Hasil.WIN] is State.HIT
        assert KEADAAN[Hasil.LOSS] is State.HIT
        assert KEADAAN[Hasil.INVALIDATED] is State.INVALIDATED
        assert KEADAAN[Hasil.EXPIRED] is State.EXPIRED

    def test_keadaannya_ikut_dilaporkan(self) -> None:
        assert catat(outcome=Hasil.EXPIRED).state is State.EXPIRED

    def test_no_signal_tidak_punya_hasil(self) -> None:
        """Yang menilai diamnya ARUNA adalah modul yang lain."""
        with pytest.raises(OutcomeError, match="silence"):
            catat(decision=Arah.NO_SIGNAL)


class TestOrientasiArah:
    def test_short_yang_harganya_turun_itu_benar(self) -> None:
        """Yang membalik tanda untuk SHORT adalah modul ini, bukan pemanggil."""
        c = catat(
            decision=Arah.SHORT, outcome=Hasil.WIN, move_pct=Decimal("-3.5")
        )

        assert c.searah_pct == Decimal("3.5")
        assert c.false_signal is False

    def test_long_yang_harganya_turun_itu_salah(self) -> None:
        c = catat(outcome=Hasil.LOSS, move_pct=Decimal("-3.5"))

        assert c.searah_pct == Decimal("-3.5")
        assert c.false_signal is True

    def test_short_yang_harganya_naik_itu_salah(self) -> None:
        c = catat(
            decision=Arah.SHORT, outcome=Hasil.LOSS, move_pct=Decimal("3.5")
        )

        assert c.false_signal is True


class TestGabunganMustahil:
    def test_win_yang_pasarnya_melawan_ditolak(self) -> None:
        """Salah tanda yang lolos akan mengajari Phase 12 kebalikan dari
        kenyataan."""
        with pytest.raises(OutcomeError, match="disebut WIN"):
            catat(outcome=Hasil.WIN, move_pct=Decimal("-1.0"))

    def test_loss_yang_pasarnya_searah_ditolak(self) -> None:
        with pytest.raises(OutcomeError, match="disebut LOSS"):
            catat(outcome=Hasil.LOSS, move_pct=Decimal("1.0"))

    def test_invalidated_boleh_bergerak_ke_mana_saja(self) -> None:
        """Signal yang dibatalkan bisa berakhir di sisi mana pun; tidak ada
        yang mustahil tentangnya."""
        assert catat(outcome=Hasil.INVALIDATED, move_pct=Decimal("5")).state
        assert catat(outcome=Hasil.EXPIRED, move_pct=Decimal("-5")).state

    def test_tanpa_pengukuran_tidak_ada_yang_ditolak(self) -> None:
        assert catat(outcome=Hasil.WIN).searah_pct is None


class TestFalseSignalBukanLoss:
    def test_loss_kecil_bukan_false_signal(self) -> None:
        """Stop kena setelah harga bergerak sedikit adalah biaya normal dari
        bertaruh, bukan kesalahan analisis."""
        c = catat(outcome=Hasil.LOSS, move_pct=Decimal("-0.8"))

        assert c.outcome is Hasil.LOSS
        assert c.false_signal is False

    def test_tepat_di_ambang_sudah_false_signal(self) -> None:
        c = catat(outcome=Hasil.LOSS, move_pct=-GERAK_BERARTI_PCT)

        assert c.false_signal is True

    def test_ambangnya_sama_dengan_ambang_kesempatan_terlewat(self) -> None:
        """Gerakan sebesar yang membuat diamnya ARUNA disebut kehilangan adalah
        gerakan sebesar yang membuat pendapatnya disebut salah."""
        tepat_di_bawah = -(GERAK_BERARTI_PCT - Decimal("0.01"))

        assert catat(outcome=Hasil.LOSS, move_pct=tepat_di_bawah).false_signal is False

    def test_belum_terukur_bukan_berarti_benar(self) -> None:
        """Mengembalikan False akan membuat setiap signal yang belum diukur
        masuk ke Phase 12 sebagai signal yang sehat."""
        c = catat(outcome=Hasil.LOSS)

        assert c.false_signal is None
        assert c.false_signal is not False

    def test_expired_pun_bisa_jadi_false_signal(self) -> None:
        """FALSE SIGNAL diukur dari gerak pasar, bukan dari cara signalnya
        berakhir."""
        c = catat(outcome=Hasil.EXPIRED, move_pct=Decimal("-4"))

        assert c.false_signal is True


class TestSebab:
    def test_tujuh_sebab_persis_seperti_pasalnya(self) -> None:
        assert {s.value for s in Sebab} == {
            "agent salah baca",
            "rezim pasar salah diklasifikasi",
            "waktu masuk buruk",
            "stop loss buruk",
            "kejutan berita",
            "strategi gagal",
            "masalah data",
        }

    def test_false_signal_tanpa_sebab_tidak_dikirim_ke_learning(self) -> None:
        """Mengirimnya ke learning tidak mengajarkan apa pun."""
        c = catat(outcome=Hasil.LOSS, move_pct=Decimal("-4"))

        assert c.needs_analysis
        with pytest.raises(OutcomeError, match=r"PASAL 14\.34"):
            require_analysed(c)

    def test_false_signal_dengan_sebab_lolos(self) -> None:
        c = catat(
            outcome=Hasil.LOSS,
            move_pct=Decimal("-4"),
            causes=(Sebab.REZIM_SALAH,),
        )

        assert not c.needs_analysis
        assert require_analysed(c) is c

    def test_loss_biasa_tidak_wajib_punya_sebab(self) -> None:
        """Menuntut penjelasan untuk setiap kekalahan akan menghasilkan
        penjelasan yang dikarang."""
        c = catat(outcome=Hasil.LOSS, move_pct=Decimal("-0.5"))

        assert not c.needs_analysis
        assert require_analysed(c) is c

    def test_yang_belum_terukur_tidak_dituduh_butuh_analisis(self) -> None:
        assert not catat(outcome=Hasil.LOSS).needs_analysis


class TestLossTidakDilunakkan:
    def test_hasilnya_dibawa_apa_adanya_ke_learning(self) -> None:
        """PASAL 11.21: dilarang menghapus, menyembunyikan, atau mengubah
        LOSS."""
        c = catat(
            outcome=Hasil.LOSS,
            move_pct=Decimal("-4"),
            causes=(Sebab.AGENT_SALAH,),
            note="pembalikan jangka pendek",
        )
        muatan = c.learning_payload()

        assert muatan["outcome"] == "LOSS"
        assert muatan["false_signal"] is True
        assert muatan["move_pct"] == Decimal("-4")
        assert muatan["causes"] == ["agent salah baca"]

    def test_kalimatnya_menyebut_loss_dengan_namanya(self) -> None:
        teks = "\n".join(catat(outcome=Hasil.LOSS, move_pct=Decimal("-4")).report())

        assert "LOSS" in teks
        assert "FALSE SIGNAL" in teks

    def test_win_juga_dikirim(self) -> None:
        """Sistem yang hanya mempelajari kemenangannya akan yakin pada dirinya
        sendiri dengan kecepatan yang mengkhawatirkan - dan sebaliknya."""
        muatan = catat(outcome=Hasil.WIN, move_pct=Decimal("2")).learning_payload()

        assert muatan["outcome"] == "WIN"


class TestLaporan:
    def test_false_signal_ditandai_sebelum_penjelasan(self) -> None:
        """Label yang muncul sesudah paragraf pembenaran sudah kehilangan
        gunanya."""
        baris = catat(
            outcome=Hasil.LOSS,
            move_pct=Decimal("-4"),
            note="pembalikan jangka pendek",
        ).report()
        teks = [x for x in baris if "FALSE SIGNAL" in x or "Akar masalah" in x]

        assert "FALSE SIGNAL" in teks[0]

    def test_sebab_belum_ketemu_dinyatakan(self) -> None:
        teks = "\n".join(catat(outcome=Hasil.LOSS, move_pct=Decimal("-4")).report())

        assert "BELUM ditemukan" in teks

    def test_yang_belum_terukur_dikatakan_begitu(self) -> None:
        teks = "\n".join(catat(outcome=Hasil.LOSS).report())

        assert "belum terukur" in teks
        assert "FALSE SIGNAL" not in teks

    def test_tujuannya_disebut(self) -> None:
        teks = "\n".join(catat(outcome=Hasil.WIN, move_pct=Decimal("3")).report())

        assert "Phase 12" in teks
        assert "Phase 11" in teks
