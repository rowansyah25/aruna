"""PASAL 14.42: alur lengkap, dan bahwa ia sejalan dengan PASAL 14.3.

Dua pasal menyebut urutan yang sama dengan kekasaran berbeda - 14.3 empat belas
tahap, 14.42 dua puluh enam langkah. Kalau keduanya boleh berkembang sendiri,
suatu saat sistem ini akan punya dua urutan resmi yang berselisih, dan tidak
ada yang tahu mana yang dijalankan.
"""

from __future__ import annotations

from typing import ClassVar

from aruna.decision.engine import ALUR, SESUDAH_TERBIT, Langkah, posisi, sebelum
from aruna.decision.hierarchy import Tahap


class TestAlurnya:
    def test_setiap_langkah_muncul_sekali(self) -> None:
        assert len(ALUR) == len(set(ALUR)) == len(Langkah)

    def test_risk_sebelum_keputusan_final(self) -> None:
        """PASAL 14.3: tidak boleh melewati risk validation."""
        assert sebelum(Langkah.RISK_ANALYSIS, Langkah.FINAL_DECISION)

    def test_validasi_data_paling_awal(self) -> None:
        assert sebelum(Langkah.DATA_VALIDATION, Langkah.MARKET_REGIME)
        assert posisi(Langkah.MARKET_DATA) == 0

    def test_telegram_sesudah_keputusan_final(self) -> None:
        """Pesan yang disusun sebelum keputusannya selesai adalah pesan yang
        bisa mendahului perubahan keputusannya."""
        assert sebelum(Langkah.FINAL_DECISION, Langkah.TELEGRAM)

    def test_pembelajaran_paling_akhir(self) -> None:
        for fase in (Langkah.PHASE_11, Langkah.PHASE_12, Langkah.PHASE_13):
            assert sebelum(Langkah.OUTCOME, fase)

    def test_veto_sebelum_council(self) -> None:
        """PASAL 14.11: veto yang sah menghasilkan NO SIGNAL. Memeriksanya
        sesudah council berarti council memutuskan sesuatu yang sudah gugur."""
        assert sebelum(Langkah.VETO_CHECK, Langkah.COUNCIL)

    def test_protes_sebelum_bantahan(self) -> None:
        assert sebelum(Langkah.PROTEST, Langkah.COUNTER_ARGUMENT)


class TestSejalanDenganPasal143:
    """Penjaga antara dua pasal yang menyebut urutan yang sama."""

    #: Tahap PASAL 14.3 -> langkah PASAL 14.42 yang mewakilinya.
    PETA: ClassVar[dict[Tahap, Langkah]] = {
        Tahap.DATA_VALIDITY: Langkah.DATA_VALIDATION,
        Tahap.DATA_FRESHNESS: Langkah.DATA_FRESHNESS,
        Tahap.MARKET_REGIME: Langkah.MARKET_REGIME,
        Tahap.MTF: Langkah.MULTI_TIMEFRAME,
        Tahap.AGENTS: Langkah.AGENT_ANALYSIS,
        Tahap.PROTEST: Langkah.PROTEST,
        Tahap.COUNCIL: Langkah.COUNCIL,
        Tahap.QUALITY: Langkah.SIGNAL_QUALITY,
        Tahap.STRATEGY: Langkah.HISTORICAL_PERFORMANCE,
        Tahap.RISK: Langkah.RISK_ANALYSIS,
        Tahap.RR: Langkah.RR,
        Tahap.INVALIDATION: Langkah.INVALIDATION,
        Tahap.HORIZON: Langkah.DECISION_HORIZON,
        Tahap.FINAL: Langkah.FINAL_DECISION,
    }

    def test_setiap_tahap_punya_langkahnya(self) -> None:
        assert set(self.PETA) == set(Tahap)

    def test_tidak_ada_langkah_yang_dipakai_dua_tahap(self) -> None:
        assert len(set(self.PETA.values())) == len(self.PETA)

    def test_urutannya_tidak_bertentangan(self) -> None:
        """Kalau 14.3 bilang A sebelum B, 14.42 tidak boleh bilang sebaliknya."""
        tahap = list(Tahap)
        for i in range(len(tahap) - 1):
            a, b = self.PETA[tahap[i]], self.PETA[tahap[i + 1]]
            assert sebelum(a, b), f"{a} harus sebelum {b}"


class TestSesudahTerbit:
    def test_telegram_dan_sesudahnya(self) -> None:
        assert Langkah.TELEGRAM in SESUDAH_TERBIT
        assert Langkah.OUTCOME in SESUDAH_TERBIT

    def test_keputusan_finalnya_sendiri_bukan(self) -> None:
        """PASAL 14.24: yang tidak boleh diubah adalah signal yang SUDAH
        terbit. Memasukkan keputusan finalnya sendiri ke daftar ini akan
        membekukan angka sebelum ia selesai dihitung."""
        assert Langkah.FINAL_DECISION not in SESUDAH_TERBIT

    def test_tidak_semuanya_sesudah_terbit(self) -> None:
        assert len(SESUDAH_TERBIT) < len(Langkah)

    def test_daftarnya_bersambung_sampai_akhir(self) -> None:
        """"Sesudah terbit" adalah ekor alurnya, bukan pilihan yang tersebar.
        Sebuah langkah awal yang ikut tertandai akan membuat gerbang
        immutability menyala di tengah perhitungan."""
        mulai = min(posisi(x) for x in SESUDAH_TERBIT)

        assert set(ALUR[mulai:]) == SESUDAH_TERBIT
