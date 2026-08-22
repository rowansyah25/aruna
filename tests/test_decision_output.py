"""Bentuk akhir yang sampai ke operator (PASAL 14.26, 14.27, 14.28).

Yang dijaga: pesan berarah yang tidak bisa ditindaklanjuti tidak dikirim,
kosakata internal dan klaim terlarang tidak lolos lewat kalimat sisipan, dan
kaki ANALYST ONLY tidak pernah hilang.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aruna.decision import Arah, State
from aruna.decision.explanation import Alasan, Penjelasan, Sumber
from aruna.decision.invalidation import Ambang, Invalidasi, Sisi
from aruna.decision.output import KAKI, Berkas, OutputError
from aruna.decision.score import score
from aruna.decision.timeframes import Bacaan, Lintas
from aruna.decision.timing import Rencana, Syarat, Timing
from aruna.notify.verdict import InternalVocabularyLeak

INVAL = Invalidasi(
    decision=Arah.LONG,
    levels=(Ambang("15m", Sisi.BELOW, Decimal("63780")),),
)


def berkas(**kw) -> Berkas:
    dasar = {
        "symbol": "BTC/USDT",
        "market": "FUTURES / PERPETUAL",
        "decision": Arah.LONG,
        "horizon": "15m",
        "entry": Decimal("64120"),
        "stop": Decimal("63780"),
        "target": Decimal("64950"),
        "invalidation": INVAL,
    }
    return Berkas(**(dasar | kw))


class TestSignalWajibLengkap:
    @pytest.mark.parametrize(
        "hilang", ["entry", "stop", "target", "invalidation"]
    )
    def test_bagian_yang_hilang_membatalkan_pengiriman(self, hilang) -> None:
        """Operator: "hanya sinyal valid yang di kirim". Entry tanpa stop tidak
        bisa diukur risikonya."""
        with pytest.raises(OutputError, match="tidak bisa ditindaklanjuti"):
            berkas(**{hilang: None}).render()

    def test_horizon_wajib(self) -> None:
        """Arah tanpa horizon tidak bisa dijawab dengan timeframe mana pun."""
        with pytest.raises(OutputError, match="horizon wajib"):
            berkas(horizon="  ").render()

    def test_lengkap_terkirim(self) -> None:
        teks = berkas().render()

        assert "BTC/USDT" in teks
        assert "64120" in teks
        assert "63780" in teks
        assert "64950" in teks
        assert "15m" in teks

    def test_horizonnya_dicetak_bukan_hanya_disimpan(self) -> None:
        """Operator: "Timeframenya jangan di ilangin ya"."""
        assert "HORIZON:" in berkas().render()


class TestNoSignal:
    def test_no_signal_wajib_menyebut_sebabnya(self) -> None:
        with pytest.raises(OutputError, match=r"PASAL 14\.27"):
            berkas(decision=Arah.NO_SIGNAL).render()

    def test_no_signal_tidak_butuh_harga(self) -> None:
        teks = berkas(
            decision=Arah.NO_SIGNAL,
            entry=None, stop=None, target=None, invalidation=None,
            reason="konfirmasi belum cukup",
        ).render()

        assert "NO SIGNAL" in teks
        assert "konfirmasi belum cukup" in teks

    def test_no_signal_tidak_mencetak_harga_walau_diberikan(self) -> None:
        """Harga di pesan tanpa arah mengundang pembacaan yang tidak pernah
        diputuskan siapa pun."""
        teks = berkas(
            decision=Arah.NO_SIGNAL, reason="konfirmasi belum cukup"
        ).render()

        assert "64120" not in teks
        assert "STOP LOSS" not in teks


class TestPenjagaKosakata:
    def test_kata_internal_dari_kalimat_sisipan_ditolak(self) -> None:
        """PASAL 14.27: "Jangan mengirim WAIT sebagai final decision." Yang
        diperiksa adalah teks jadi, jadi kata itu tidak bisa masuk lewat
        kalimat sebab yang disusun lapisan lain."""
        with pytest.raises(InternalVocabularyLeak):
            berkas(
                decision=Arah.NO_SIGNAL, reason="WAIT for confirmation"
            ).render()

    def test_klaim_terlarang_dari_kalimat_sisipan_ditolak(self) -> None:
        with pytest.raises(OutputError, match="PASAL 51"):
            berkas(rr="1 : 2.44 (risk free)").render()

    def test_pesan_wajar_lolos_keduanya(self) -> None:
        assert berkas(rr="1 : 2.44").render()


class TestKaki:
    def test_kaki_analyst_only_selalu_ada(self) -> None:
        """Pesan berisi entry, stop, dan leverage yang sampai tanpa kalimat itu
        terbaca sebagai perintah (PASAL 14.26, 14.44)."""
        for teks in (
            berkas().render(),
            berkas(
                decision=Arah.NO_SIGNAL, reason="konfirmasi belum cukup"
            ).render(),
        ):
            for baris in KAKI:
                assert baris in teks

    def test_kakinya_di_akhir(self) -> None:
        assert berkas().render().rstrip().endswith(KAKI[-1])


class TestBlokAnalisis:
    def test_blok_kosong_tidak_dicetak(self) -> None:
        assert "ANALISIS" not in berkas().render()

    def test_decision_score_tidak_pernah_tanpa_keterangannya(self) -> None:
        """PASAL 14.16: skor bukan probabilitas profit."""
        s = score({
            "trend": 1.0, "structure": 1.0, "momentum": 1.0,
            "volume": 1.0, "agreement": 1.0, "history": 1.0,
            "risk": 1.0, "news": 1.0,
        })
        teks = berkas(score=s).render()

        assert "+69" in teks
        assert "bukan peluang profit" in teks

    def test_penjelasan_ikut_dicetak(self) -> None:
        p = Penjelasan(
            decision=Arah.LONG,
            reasons=(
                Alasan(Sumber.STRUKTUR, "struktur 15m utuh"),
                Alasan(Sumber.VOLUME, "volume mengonfirmasi breakout"),
            ),
        )
        teks = berkas(explanation=p).render()

        assert "KENAPA LONG" in teks
        assert "struktur 15m utuh" in teks

    def test_mutu_dan_rezim_dicetak(self) -> None:
        teks = berkas(quality=91, confidence=0.87, regime="TRENDING UP").render()

        assert "91/100" in teks
        assert "87%" in teks
        assert "TRENDING UP" in teks


class TestKonflikTimeframe:
    def test_lintas_timeframe_ikut_dicetak(self) -> None:
        """PASAL 14.28."""
        peta = Lintas(
            horizon="15m",
            readings=(
                Bacaan("5m", Arah.LONG),
                Bacaan("10m", Arah.SHORT),
                Bacaan("15m", Arah.LONG),
            ),
            regime="TRENDING UP",
        )
        teks = berkas(lintas=peta).render()

        assert "MULTI-TIMEFRAME" in teks
        assert "DOMINAN: 15m" in teks

    def test_oposisi_dan_sanggahan_dicetak(self) -> None:
        teks = berkas(
            oposisi=(("Agent 3", "momentum bearish"),),
            sanggahan=(("Agent 1", "struktur timeframe tinggi masih bullish"),),
        ).render()

        assert "OPOSISI: Agent 3" in teks
        assert "SANGGAHAN: Agent 1" in teks

    def test_suara_council_dicetak(self) -> None:
        teks = berkas(
            setuju=("Agent 1", "Agent 2"), kontra=("Agent 3",)
        ).render()

        assert "SETUJU:" in teks
        assert "KONTRA:" in teks
        assert "Agent 3" in teks


class TestWaktuMasuk:
    def test_waktu_masuk_tertunda_dicetak_dekat_harganya(self) -> None:
        """Catatan waktu masuk yang terpisah dari angkanya terbaca sesudah
        operator selesai membaca angka - yaitu sesudah ia memutuskan."""
        r = Rencana(
            Arah.LONG,
            Timing.PULLBACK,
            Syarat(
                zone_low=Decimal("64000"),
                zone_high=Decimal("64100"),
                confirmation="muncul konfirmasi bullish",
            ),
        )
        # Blok analisis dan risiko sengaja diisi. Tanpa keduanya, memindahkan
        # catatan waktu masuk ke bawah blok yang kosong tidak menggeser apa pun,
        # dan test ini akan lulus pada tata letak yang justru salah.
        baris = berkas(
            timing=r, regime="TRENDING UP", risk_line="🟢 32/100 LOW"
        ).render().splitlines()
        i_target = next(i for i, x in enumerate(baris) if "TAKE PROFIT" in x)
        i_tunggu = next(i for i, x in enumerate(baris) if "TUNGGU PULLBACK" in x)
        i_risiko = next(i for i, x in enumerate(baris) if "RISIKO" in x)

        assert i_target < i_tunggu < i_risiko
        assert i_tunggu - i_target < 4

    def test_syaratnya_ikut_dicetak(self) -> None:
        r = Rencana(
            Arah.LONG,
            Timing.PULLBACK,
            Syarat(zone_low=Decimal("64000"), zone_high=Decimal("64100")),
        )
        teks = berkas(timing=r).render()

        assert "64,000 - 64,100" in teks
        assert "kedaluwarsa" in teks

    def test_masuk_sekarang_tidak_menambah_blok(self) -> None:
        """Sebuah baris "WAKTU MASUK: MASUK SEKARANG" tidak menambah apa pun -
        entry sudah tertulis di atasnya."""
        teks = berkas(timing=Rencana(Arah.LONG, Timing.NOW)).render()

        assert "WAKTU MASUK" not in teks


class TestKeadaanTidakDipakai:
    def test_state_tidak_bocor_ke_pesan(self) -> None:
        """Keadaan internal seperti CANDIDATE atau DEBATING bukan urusan
        pembaca, dan kosakata internal memang dijaga terpisah."""
        assert State.DEBATING.value not in berkas().render()
