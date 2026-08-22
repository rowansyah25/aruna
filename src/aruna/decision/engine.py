"""Alur lengkap satu keputusan (PASAL 14.42).

PASAL 14.3 menyebut empat belas tahap; PASAL 14.42 menyebut alur penuh dari
data mentah sampai pembelajaran. Keduanya urutan yang sama dengan kekasaran
berbeda, dan modul ini memuat yang lebih halus.

**Modul ini tidak menjalankan apa pun.** Ia daftar berurut, dan gunanya adalah
menjadikan "langkah ini dilewati" sebagai pertanyaan yang bisa dijawab -
:mod:`aruna.decision.observe` yang menjawabnya dari keputusan yang sudah jadi.

Sebuah mesin yang benar-benar menjalankan urutan ini akan menjadi gerbang
ketiga di jalur yang sudah punya dua, dan dua-duanya terukur akan membungkam
ARUNA hampir sepenuhnya kalau dipasang apa adanya: gerbang risiko Phase 13 dan
daftar periksa empat belas butir. Memasang yang ketiga sebelum yang pertama
diukur menghasilkan sistem yang diam karena tiga sebab sekaligus, dan tidak
satu pun bisa dibedakan dari pasar yang sepi.
"""

from __future__ import annotations

from enum import StrEnum


class Langkah(StrEnum):
    """Nilainya data - jangan diterjemahkan.

    **Urutan deklarasinya adalah urutan alurnya**, dan itu satu-satunya sumber
    urutan di modul ini. Menyisipkan anggota baru di tempat yang salah akan
    tertangkap ``test_urutannya_tidak_bertentangan``, yang membandingkannya
    dengan PASAL 14.3.
    """

    MARKET_DATA = "MARKET DATA"
    DATA_VALIDATION = "DATA VALIDATION"
    DATA_FRESHNESS = "DATA FRESHNESS"
    MARKET_REGIME = "MARKET REGIME"
    MULTI_TIMEFRAME = "MULTI-TIMEFRAME"
    STRATEGY_MATCH = "STRATEGY MATCH"
    AGENT_ANALYSIS = "AGENT ANALYSIS"
    PROTEST = "PROTEST"
    COUNTER_ARGUMENT = "COUNTER ARGUMENT"
    VETO_CHECK = "VETO CHECK"
    COUNCIL = "COUNCIL"
    SIGNAL_QUALITY = "SIGNAL QUALITY"
    HISTORICAL_PERFORMANCE = "HISTORICAL PERFORMANCE"
    RISK_ANALYSIS = "RISK ANALYSIS"
    RR = "R/R"
    SL_TP_VALIDATION = "SL / TP VALIDATION"
    INVALIDATION = "INVALIDATION"
    EXPIRATION = "EXPIRATION"
    DECISION_HORIZON = "DECISION HORIZON"
    FINAL_QUALITY_GATE = "FINAL QUALITY GATE"
    FINAL_DECISION = "FINAL DECISION"
    TELEGRAM = "TELEGRAM"
    OUTCOME = "OUTCOME"
    PHASE_11 = "PHASE 11"
    PHASE_12 = "PHASE 12"
    PHASE_13 = "PHASE 13"


ALUR: tuple[Langkah, ...] = tuple(Langkah)

_INDEKS: dict[Langkah, int] = {langkah: i for i, langkah in enumerate(ALUR)}

#: Langkah yang terjadi **sesudah** signal terbit.
#:
#: PASAL 14.24 dan §12.1: apa pun yang dihitung di sini tidak boleh mengubah
#: direction, entry, SL, TP, atau confidence yang sudah dikirim. Batas ini
#: dieja supaya pembaca berikutnya tidak perlu menyimpulkannya dari urutan -
#: dan supaya sebuah langkah yang kelak disisipkan sesudah TELEGRAM tidak
#: diam-diam berada di luar batas immutability.
SESUDAH_TERBIT: frozenset[Langkah] = frozenset({
    Langkah.TELEGRAM,
    Langkah.OUTCOME,
    Langkah.PHASE_11,
    Langkah.PHASE_12,
    Langkah.PHASE_13,
})


def posisi(langkah: Langkah) -> int:
    return _INDEKS[langkah]


def sebelum(a: Langkah, b: Langkah) -> bool:
    return _INDEKS[a] < _INDEKS[b]


__all__ = ["ALUR", "SESUDAH_TERBIT", "Langkah", "posisi", "sebelum"]
