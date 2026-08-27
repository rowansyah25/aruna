"""Terjemahan suara, dan satu-satunya tempat WAIT boleh disebut."""

from __future__ import annotations

import pytest

from aruna.agents.base import AgentOpinion
from aruna.core.enums import AgentRole, Decision
from aruna.xau.suara import RekapSuara, Suara, ke_keputusan_xau, suara_terhadap


def _opini(
    decision: Decision,
    *,
    abstained: bool = False,
    role: AgentRole = AgentRole.TECHNICAL,
) -> AgentOpinion:
    return AgentOpinion(
        role=role,
        decision=decision,
        confidence=0.0 if decision is Decision.WAIT else 0.6,
        reasoning=() if abstained else ("alasan uji",),
        abstained=abstained,
    )


class TestSuaraTerhadap:
    def test_arah_sama_adalah_setuju(self) -> None:
        assert suara_terhadap(_opini(Decision.BUY), Decision.BUY) is Suara.AGREE

    def test_arah_berlawanan_adalah_menentang(self) -> None:
        assert suara_terhadap(_opini(Decision.SELL), Decision.BUY) is Suara.DISAGREE

    def test_setuju_terhadap_sell_juga_bekerja(self) -> None:
        assert suara_terhadap(_opini(Decision.SELL), Decision.SELL) is Suara.AGREE

    def test_abstain_adalah_netral(self) -> None:
        opini = _opini(Decision.WAIT, abstained=True)
        assert suara_terhadap(opini, Decision.BUY) is Suara.NEUTRAL

    def test_wait_tanpa_abstain_juga_netral(self) -> None:
        """Menahan diri bukan menentang - dan bukan mendukung."""
        assert suara_terhadap(_opini(Decision.WAIT), Decision.BUY) is Suara.NEUTRAL

    def test_arah_bukan_arah_ditolak(self) -> None:
        """Merekap terhadap NO_SIGNAL tidak punya arti; itu bug pemanggil."""
        with pytest.raises(ValueError, match="arah"):
            suara_terhadap(_opini(Decision.BUY), Decision.NO_SIGNAL)


class TestKontradiksi:
    def test_bulat_setuju_nol_kontradiksi(self) -> None:
        assert RekapSuara(setuju=8, menentang=0, netral=2, rincian=()).kontradiksi == 0.0

    def test_terbelah_rata_kontradiksi_penuh(self) -> None:
        assert RekapSuara(setuju=4, menentang=4, netral=2, rincian=()).kontradiksi == 1.0

    def test_netral_tidak_menghitung_sebagai_kontradiksi(self) -> None:
        """Sepuluh agen diam bukan sepuluh agen bertengkar.

        Kalau netral masuk penyebut, setiap kondisi sepi terlihat seperti
        perselisihan - dan gerbangnya menolak justru saat pasar paling tenang.
        """
        assert RekapSuara(setuju=2, menentang=0, netral=8, rincian=()).kontradiksi == 0.0

    def test_semua_netral_tidak_terukur(self) -> None:
        """Nol suara berarti TIDAK DIUKUR, bukan nol kontradiksi.

        Menyamakan keduanya akan meloloskan sinyal yang tidak seorang pun
        mendukungnya, karena 'kontradiksi 0' terbaca sebagai kesepakatan bulat.
        """
        assert RekapSuara(setuju=0, menentang=0, netral=10, rincian=()).kontradiksi is None

    def test_minoritas_satu_dari_sembilan(self) -> None:
        rekap = RekapSuara(setuju=8, menentang=1, netral=1, rincian=())
        assert abs(rekap.kontradiksi - 2 / 9) < 1e-9

    def test_bersuara_tidak_menghitung_netral(self) -> None:
        assert RekapSuara(setuju=3, menentang=2, netral=5, rincian=()).bersuara == 5


class TestKosakata:
    def test_wait_jadi_no_signal(self) -> None:
        assert ke_keputusan_xau(Decision.WAIT) is Decision.NO_SIGNAL

    def test_arah_diteruskan(self) -> None:
        assert ke_keputusan_xau(Decision.BUY) is Decision.BUY
        assert ke_keputusan_xau(Decision.SELL) is Decision.SELL

    def test_no_signal_tetap(self) -> None:
        assert ke_keputusan_xau(Decision.NO_SIGNAL) is Decision.NO_SIGNAL
