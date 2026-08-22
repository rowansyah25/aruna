"""Kosakata publik dan hasil pemilihan (PASAL 1, 3, 15).

Yang diuji di sini bukan format teks, tapi dua hal yang bisa menyesatkan
operator: kata yang berbunyi seperti janji, dan perpecahan yang tidak pernah
terjadi.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aruna.core.enums import AgentRole, Decision
from aruna.notify.verdict import (
    LONG,
    MARK,
    NO_SIGNAL,
    PUBLIC_DECISION,
    SHORT,
    InternalVocabularyLeak,
    VoteSplit,
    guard_public,
    public_decision,
    render_votes,
    vote_split,
)


def _op(role, decision, *, abstained=False):
    return SimpleNamespace(role=role, decision=decision, abstained=abstained)


class TestKosakataPublik:
    def test_arah_diterjemahkan(self) -> None:
        assert public_decision(Decision.BUY) == LONG
        assert public_decision(Decision.SELL) == SHORT

    def test_wait_jadi_no_signal(self) -> None:
        """WAIT terbaca seperti "sebentar lagi ada". Tidak ada yang akan
        datang - dan pembaca yang menunggu akan menunggui apa pun berikutnya
        yang muncul di layar."""
        assert public_decision(Decision.WAIT) == NO_SIGNAL

    def test_setiap_decision_punya_terjemahan(self) -> None:
        """Anggota enum baru harus gagal keras di sini, bukan diam-diam jadi
        NO SIGNAL di ponsel operator."""
        for decision in Decision:
            assert decision in PUBLIC_DECISION, decision

    def test_hanya_tiga_kata_yang_keluar(self) -> None:
        assert set(PUBLIC_DECISION.values()) == {LONG, SHORT, NO_SIGNAL}

    def test_tiap_kata_punya_warna(self) -> None:
        assert set(MARK) == {LONG, SHORT, NO_SIGNAL}

    def test_warna_sesuai_permintaan_operator(self) -> None:
        """Hijau naik, merah turun, kuning tidak ada signal. Warna dibaca lebih
        dulu daripada kata di bawahnya, jadi warna yang salah adalah
        kesalahpahaman sebelum kalimat pertama sempat dibaca."""
        assert MARK[LONG] == "🟢"
        assert MARK[SHORT] == "🔴"
        assert MARK[NO_SIGNAL] == "🟡"

    def test_string_diterima_juga(self) -> None:
        assert public_decision("BUY") == LONG

    def test_string_asing_ditolak(self) -> None:
        with pytest.raises(InternalVocabularyLeak):
            public_decision("MAYBE")


class TestPenjagaKosakata:
    def test_wait_ditolak(self) -> None:
        with pytest.raises(InternalVocabularyLeak, match="WAIT"):
            guard_public("FINAL DECISION:\nWAIT")

    @pytest.mark.parametrize("kata", ["BUY", "SELL", "NO_SIGNAL"])
    def test_kosakata_internal_lain_ditolak(self, kata: str) -> None:
        with pytest.raises(InternalVocabularyLeak):
            guard_public(f"FINAL DECISION:\n{kata}")

    def test_pesan_bersih_lolos(self) -> None:
        teks = "🟢 ARUNA ANALYSIS\n\nFINAL DECISION:\nLONG\n\nNO SIGNAL bukan WAIT"
        with pytest.raises(InternalVocabularyLeak):
            guard_public(teks)  # kalimat terakhir sengaja masih memuat WAIT

        bersih = "🟢 ARUNA ANALYSIS\n\nFINAL DECISION:\nLONG\n\nTOTAL:\n3 VS 2"
        assert guard_public(bersih) == bersih

    def test_no_signal_dengan_spasi_lolos(self) -> None:
        """Kata publiknya "NO SIGNAL"; yang dilarang adalah "NO_SIGNAL"."""
        assert guard_public("FINAL DECISION:\nNO SIGNAL") is not None

    def test_kata_yang_kebetulan_memuatnya_lolos(self) -> None:
        """Penjaga yang menolak "SELLING" atau "BUYER" akan memblokir kalimat
        yang benar, dan penjaga yang memblokir kalimat benar akan dimatikan."""
        assert guard_public("tekanan SELLING mereda, BUYER kembali masuk")

    def test_tidak_memperbaiki_diam_diam(self) -> None:
        """Kalau "WAIT" cuma diganti, kalimat di sekitarnya - "menunggu
        konfirmasi" - tetap lolos dan tetap salah."""
        with pytest.raises(InternalVocabularyLeak):
            guard_public("Sedang WAIT, menunggu konfirmasi berikutnya.")


class TestPembagianSuara:
    def test_siapa_di_sisi_mana(self) -> None:
        opinions = [
            _op(AgentRole.TECHNICAL, Decision.BUY),
            _op(AgentRole.RISK, Decision.SELL),
            _op(AgentRole.NEWS, Decision.BUY),
        ]
        split = vote_split(opinions, Decision.BUY)

        assert split.setuju == (AgentRole.TECHNICAL.value, AgentRole.NEWS.value)
        assert split.kontra == (AgentRole.RISK.value,)
        assert split.total == "2 VS 1"

    def test_abstain_bukan_kontra(self) -> None:
        """Agent tanpa bukti tidak sedang menolak apa pun. Memasukkannya ke
        KONTRA membuat feed yang mati terbaca sebagai council yang terbelah."""
        opinions = [
            _op(AgentRole.TECHNICAL, Decision.BUY),
            _op(AgentRole.NEWS, Decision.WAIT, abstained=True),
        ]
        split = vote_split(opinions, Decision.BUY)

        assert split.kontra == ()
        assert split.abstain == (AgentRole.NEWS.value,)
        assert split.total == "1 VS 0"

    def test_wait_dan_no_signal_dihitung_sepakat(self) -> None:
        """Keduanya sampai ke operator sebagai kalimat yang sama: tidak ada
        posisi. Mencatatnya sebagai perpecahan menampilkan perbedaan yang
        tidak pernah terjadi."""
        opinions = [
            _op(AgentRole.TECHNICAL, Decision.WAIT),
            _op(AgentRole.RISK, Decision.NO_SIGNAL),
        ]
        split = vote_split(opinions, Decision.WAIT)

        assert split.kontra == ()
        assert len(split.setuju) == 2

    def test_council_bulat(self) -> None:
        opinions = [_op(AgentRole.TECHNICAL, Decision.SELL)]
        split = vote_split(opinions, Decision.SELL)
        assert split.total == "1 VS 0"


class TestAngkaAcuan:
    """Operator minta entry, take profit dan leverage ikut dikirim - sebagai
    acuan, bukan perintah."""

    def _blok(self, **kwargs) -> str:
        from aruna.notify.verdict import render_analysis

        base = {
            "symbol": "BTC/USDT",
            "decision": Decision.BUY,
            "split": VoteSplit(setuju=("TECHNICAL",), kontra=()),
            "entry": "63000",
            "stop": "61500",
            "target": "66000",
        }
        base.update(kwargs)
        return render_analysis(**base)

    def test_entry_stop_target_ikut(self) -> None:
        teks = self._blok()
        assert "ENTRY:\n63000" in teks
        assert "STOP LOSS:\n61500" in teks
        assert "TAKE PROFIT:\n66000" in teks

    def test_disebut_acuan_saja(self) -> None:
        assert "CATATAN:\nENTRY / SL / TP / LEVERAGE = ACUAN SAJA" in self._blok()

    def test_format_tanpa_indentasi(self) -> None:
        """Template operator menulis nilai rata kiri di baris sendiri. Versi
        sebelumnya menambah dua spasi "supaya rapi" - dan pesannya berhenti
        cocok dengan acuan yang dipakai pembacanya. Bentuk bukan selera."""
        teks = self._blok(leverage=10, liquidation="57200", confidence=0.87)
        for baris in teks.splitlines():
            assert baris == baris.lstrip(), repr(baris)

    def test_leverage_ikut_kalau_ada(self) -> None:
        teks = self._blok(leverage=10, liquidation="57200")
        assert "LEVERAGE:\n10x" in teks

    def test_leverage_tidak_pernah_sendirian(self) -> None:
        """10x tanpa harga likuidasi terbaca "modal dikali sepuluh", dan
        menyembunyikan bahwa gerak 10% melawan posisi sudah menghabiskannya."""
        teks = self._blok(leverage=10, liquidation="57200")
        assert "HARGA LIKUIDASI:\n57200" in teks

    def test_likuidasi_tak_terhitung_dikatakan(self) -> None:
        """Bukan tanda hubung yang mudah dilewati mata, dan bukan alasan
        menyembunyikan leverage-nya."""
        teks = self._blok(leverage=10, liquidation=None)
        assert "LEVERAGE:\n10x" in teks
        assert "HARGA LIKUIDASI:\nTIDAK BISA DIHITUNG" in teks
        assert "ditutup paksa bursa" in teks

    def test_tanpa_leverage_tidak_ada_bagian_itu(self) -> None:
        """Spot tidak punya leverage. Baris "LEVERAGE: -" akan mengajari
        pembaca melewati bagian itu, termasuk saat isinya ada."""
        teks = self._blok()
        assert "LEVERAGE:" not in teks
        assert "HARGA LIKUIDASI:" not in teks

    def test_no_signal_tidak_membawa_angka(self) -> None:
        """Level entry pada blok yang bilang tidak ada signal adalah undangan
        untuk masuk pada analisis yang justru menolak masuk."""
        teks = self._blok(decision=Decision.WAIT, leverage=10, liquidation="1")
        assert "ENTRY:" not in teks
        assert "LEVERAGE:" not in teks

    def test_angka_tidak_lengkap_dikatakan(self) -> None:
        teks = self._blok(stop=None)
        assert "TIDAK TERSEDIA" in teks
        assert "ENTRY:\n63000" not in teks

    def test_blok_berangka_lolos_penjaga_kosakata(self) -> None:
        assert self._blok(leverage=5, liquidation="50000")


class TestBlokPemilihan:
    def test_memuat_nama_bukan_cuma_angka(self) -> None:
        split = VoteSplit(setuju=("TECHNICAL", "NEWS"), kontra=("RISK",))
        teks = "\n".join(render_votes(split))

        assert "TECHNICAL" in teks
        assert "RISK" in teks
        assert "2 VS 1" in teks

    def test_sisi_kosong_dikatakan(self) -> None:
        """Judul "KONTRA:" tanpa apa-apa di bawahnya terbaca seperti daftar
        yang terpotong."""
        teks = "\n".join(render_votes(VoteSplit(setuju=("TECHNICAL",), kontra=())))
        assert "(tidak ada)" in teks

    def test_abstain_disembunyikan_kalau_kosong(self) -> None:
        """Baris kosong di tiap pesan mengajari pembaca melewatinya."""
        teks = "\n".join(render_votes(VoteSplit(setuju=("A",), kontra=("B",))))
        assert "ABSTAIN" not in teks

    def test_abstain_ditampilkan_kalau_ada(self) -> None:
        split = VoteSplit(setuju=("A",), kontra=("B",), abstain=("C",))
        teks = "\n".join(render_votes(split))
        assert "ABSTAIN:\nC" in teks

    def test_blok_pemilihan_lolos_penjaga(self) -> None:
        split = VoteSplit(setuju=("TECHNICAL",), kontra=("RISK",))
        assert guard_public("\n".join(render_votes(split)))
