"""Kapan ARUNA boleh berbicara lagi (PASAL 14.35, 14.36, 14.37).

Bentuk kegagalannya ditulis apa adanya di PASAL 14.37: LONG LONG LONG setiap
beberapa detik. Yang rusak bukan hanya ketenangan operator - notifikasi yang
berbunyi terus berhenti dibaca, dan yang hilang pertama justru signal yang
berbeda dari sebelumnya.
"""

from __future__ import annotations

import pytest

from aruna.decision import Arah, State
from aruna.decision.consistency import (
    ConsistencyError,
    Pembalikan,
    Pemicu,
    Terakhir,
    Tindakan,
    evaluate,
)
from aruna.decision.explanation import Alasan, ExplanationError, Sumber

AKTIF = Terakhir(
    decision=Arah.LONG,
    horizon="15m",
    strategy="trend-continuation",
    state=State.ACTIVE,
)

BUKTI = (Alasan(Sumber.STRUKTUR, "struktur 15m patah di 63.780"),)

BALIK = Pembalikan(
    previous=Arah.LONG,
    new=Arah.SHORT,
    reason="struktur bullish 15m dibatalkan",
    new_evidence=BUKTI,
)


def nilai(candidate: Arah = Arah.LONG, **kw):
    kw.setdefault("horizon", AKTIF.horizon)
    kw.setdefault("strategy", AKTIF.strategy)
    kw.setdefault("previous", AKTIF)
    return evaluate(candidate, **kw)


class TestDuplikat:
    def test_arah_sama_tanpa_sebab_tidak_dikirim(self) -> None:
        """PASAL 14.37: jangan kirim LONG LONG LONG setiap beberapa detik."""
        p = nilai()

        assert p.action is Tindakan.DIAM
        assert not p.sends
        assert "14.37" in p.reason

    def test_setup_baru_membenarkan_pengiriman(self) -> None:
        p = nilai(triggers=[Pemicu.SETUP_BARU])

        assert p.action is Tindakan.KIRIM
        assert Pemicu.SETUP_BARU in p.triggers

    def test_horizon_berbeda_terdeteksi_tanpa_diklaim(self) -> None:
        """Sebab yang bisa dibuktikan tidak perlu dipercaya dari pemanggil."""
        p = nilai(horizon="1h")

        assert p.action is Tindakan.KIRIM
        assert p.triggers == (Pemicu.HORIZON_BARU,)

    def test_strategi_berbeda_terdeteksi_tanpa_diklaim(self) -> None:
        p = nilai(strategy="mean-reversion")

        assert p.triggers == (Pemicu.STRATEGI_BARU,)

    def test_klaim_horizon_baru_yang_tidak_benar_ditolak(self) -> None:
        """Perlindungan duplikat yang bisa dilewati dengan menyebut alasan yang
        tidak benar bukan perlindungan; ia formulir."""
        with pytest.raises(ConsistencyError, match="horizon baru"):
            nilai(triggers=[Pemicu.HORIZON_BARU])

    def test_klaim_strategi_baru_yang_tidak_benar_ditolak(self) -> None:
        with pytest.raises(ConsistencyError, match="strategi baru"):
            nilai(triggers=[Pemicu.STRATEGI_BARU])

    def test_klaim_pembalikan_tanpa_perubahan_arah_ditolak(self) -> None:
        with pytest.raises(ConsistencyError, match="pembalikan arah"):
            nilai(triggers=[Pemicu.BALIK_ARAH])

    def test_sebab_yang_tidak_bisa_diperiksa_dipercaya(self) -> None:
        """Modul ini tidak berpura-pura bisa memeriksa "perubahan pasar besar"."""
        assert nilai(triggers=[Pemicu.PERUBAHAN_BESAR]).action is Tindakan.KIRIM

    def test_enam_sebab_persis_seperti_pasalnya(self) -> None:
        assert {p.value for p in Pemicu} == {
            "setup baru",
            "perubahan pasar besar",
            "syarat pembatalan terpenuhi",
            "pembalikan arah",
            "horizon baru",
            "strategi baru",
        }


class TestSignalYangSudahBerakhir:
    @pytest.mark.parametrize(
        "akhir", [State.HIT, State.INVALIDATED, State.EXPIRED]
    )
    def test_signal_berakhir_tidak_membungkam_yang_berikutnya(self, akhir) -> None:
        """Pendapat berikutnya bukan pengulangan, ia pendapat berikutnya."""
        lama = Terakhir(Arah.LONG, "15m", "trend-continuation", akhir)

        assert nilai(previous=lama).action is Tindakan.KIRIM

    def test_tanpa_signal_sebelumnya_langsung_kirim(self) -> None:
        assert nilai(previous=None).action is Tindakan.KIRIM

    def test_pembalikan_tanpa_arah_lama_ditolak(self) -> None:
        with pytest.raises(ConsistencyError, match="pembalikan arah"):
            nilai(previous=None, triggers=[Pemicu.BALIK_ARAH])

    def test_signal_published_masih_membungkam(self) -> None:
        lama = Terakhir(Arah.LONG, "15m", "trend-continuation", State.PUBLISHED)

        assert nilai(previous=lama).action is Tindakan.DIAM


class TestPembalikan:
    def test_arah_berubah_tanpa_alasan_ditolak(self) -> None:
        """PASAL 14.35: tidak boleh LONG lalu beberapa detik kemudian SHORT
        tanpa perubahan pasar yang berarti."""
        with pytest.raises(ConsistencyError, match=r"PASAL 14\.35"):
            nilai(Arah.SHORT)

    def test_arah_berubah_dengan_alasan_dikirim(self) -> None:
        p = nilai(Arah.SHORT, reversal=BALIK)

        assert p.action is Tindakan.BALIK
        assert p.reversal is BALIK
        assert Pemicu.BALIK_ARAH in p.triggers

    def test_catatan_pembalikan_harus_cocok_dengan_kejadiannya(self) -> None:
        salah = Pembalikan(
            previous=Arah.SHORT, new=Arah.LONG,
            reason="struktur bearish dibatalkan", new_evidence=BUKTI,
        )

        with pytest.raises(ConsistencyError, match="tapi yang terjadi"):
            nilai(Arah.SHORT, reversal=salah)

    def test_pembalikan_tanpa_bukti_baru_ditolak(self) -> None:
        """Perubahan arah harus dijelaskan oleh data yang belum ada sebelumnya."""
        with pytest.raises(ConsistencyError, match="bukti baru"):
            Pembalikan(
                previous=Arah.LONG, new=Arah.SHORT,
                reason="struktur patah", new_evidence=(),
            )

    def test_alasan_pembalikan_tidak_boleh_kosong(self) -> None:
        """Alasan yang boleh berbunyi "market terlihat bagus" membuat seluruh
        syarat pembalikan menjadi formalitas."""
        with pytest.raises(ExplanationError):
            Pembalikan(
                previous=Arah.LONG, new=Arah.SHORT,
                reason="market terlihat bagus", new_evidence=BUKTI,
            )
        with pytest.raises(ExplanationError, match="tanpa isi"):
            Pembalikan(
                previous=Arah.LONG, new=Arah.SHORT,
                reason="  ", new_evidence=BUKTI,
            )

    def test_no_signal_bukan_arah_yang_bisa_dibalik(self) -> None:
        with pytest.raises(ConsistencyError, match="bukan arah"):
            Pembalikan(
                previous=Arah.LONG, new=Arah.NO_SIGNAL,
                reason="struktur patah", new_evidence=BUKTI,
            )

    def test_arah_yang_sama_bukan_pembalikan(self) -> None:
        with pytest.raises(ConsistencyError, match="bukan pembalikan"):
            Pembalikan(
                previous=Arah.LONG, new=Arah.LONG,
                reason="struktur patah", new_evidence=BUKTI,
            )


class TestSignalLamaTidakDisunting:
    def test_signal_lama_menjadi_invalidated(self) -> None:
        """PASAL 14.36: Old Signal INVALIDATED, New Signal CREATED."""
        assert BALIK.retire(State.ACTIVE) is State.INVALIDATED
        assert BALIK.retire(State.PUBLISHED) is State.INVALIDATED

    def test_signal_yang_sudah_berakhir_menolak_dibatalkan(self) -> None:
        """Tidak ada lagi yang bisa dibatalkan darinya, dan menandainya ulang
        akan menulis ulang sejarah."""
        from aruna.decision import TransitionError

        for akhir in (State.HIT, State.EXPIRED, State.INVALIDATED):
            with pytest.raises(TransitionError):
                BALIK.retire(akhir)

    def test_laporannya_menyatakan_yang_lama_tidak_disunting(self) -> None:
        teks = "\n".join(BALIK.report())

        assert "tidak disunting" in teks
        assert "INVALIDATED" in teks
        assert "DIBUAT" in teks

    def test_laporannya_menyebut_kedua_arah_dan_buktinya(self) -> None:
        teks = "\n".join(BALIK.report())

        assert "LONG" in teks
        assert "SHORT" in teks
        assert "struktur bullish 15m dibatalkan" in teks
        assert "63.780" in teks


class TestKandidatTanpaArah:
    def test_no_signal_tidak_diumumkan_sebagai_signal(self) -> None:
        p = nilai(Arah.NO_SIGNAL)

        assert p.action is Tindakan.DIAM
        assert "tidak ada arah baru" in p.reason

    def test_no_signal_tidak_membatalkan_signal_lama(self) -> None:
        """Yang membatalkan setup lama adalah syarat pembatalannya sendiri
        (PASAL 14.21), bukan kehadiran kandidat tanpa arah."""
        p = nilai(Arah.NO_SIGNAL)

        assert p.reversal is None
        assert p.triggers == ()


class TestKalimat:
    def test_barisnya_menyebut_sebabnya(self) -> None:
        assert "horizon baru" in nilai(horizon="1h").line()

    def test_diam_menyebut_kenapa_diam(self) -> None:
        assert "TIDAK DIKIRIM" in nilai().line()
