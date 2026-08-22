"""Apa yang ditanyakan pada simulasi (bagian 16.4).

Bagian 16.4 mencontohkan perbedaannya dengan dua kalimat berdampingan. Yang
dilarang: *"Apakah BTC akan naik?"* Yang diminta: *"Simulasikan beberapa
kemungkinan perkembangan berdasarkan kondisi saat ini."*

Bedanya bukan gaya bahasa. Pertanyaan ya/tidak **memaksa simulasi berpihak
sebelum ia mensimulasikan apa pun**: yang ditanya sudah menyebut satu arah, dan
setiap skenario yang lahir sesudahnya adalah pembelaan atau bantahan terhadap
arah itu. Bagian 16.5 menuntut minimal tiga skenario yang berdiri sendiri -
bullish continuation, bearish reversal, false breakout - dan tiga hal itu tidak
bisa lahir dari satu pertanyaan yang jawabannya ya atau tidak.

Ada akibat kedua yang lebih halus. Bagian 16.18 menyatakan Phase 16 tidak
menghasilkan FINAL LONG atau FINAL SHORT. Pertanyaan "apakah akan naik" yang
dijawab "ya" **adalah** FINAL LONG dalam bentuk kalimat - keputusan yang lolos
lewat pintu pertanyaan, bukan lewat pintu keluaran yang dijaga.
"""

from __future__ import annotations

import re

from aruna.scenario.pemicu import Peristiwa

__all__ = [
    "MINIMUM_KONDISI",
    "POLA_YA_TIDAK",
    "PertanyaanDitolak",
    "susun_pertanyaan",
]


#: Bentuk yang menuntut jawaban ya atau tidak, atau menuntut satu arah.
#:
#: Ditulis sebagai pola, bukan daftar kalimat: yang dilarang bentuknya, dan
#: bentuk yang sama bisa ditulis dengan aset apa pun.
POLA_YA_TIDAK = (
    re.compile(r"\bapakah\b", re.IGNORECASE),
    re.compile(r"\bakan\s+(naik|turun|pump|dump|rally|jatuh)\b", re.IGNORECASE),
    re.compile(r"\b(will|should)\s+\w+\s+(go|rise|fall|pump|dump)\b", re.IGNORECASE),
    re.compile(r"\b(buy|sell|long|short)\s+or\s+\w+\b", re.IGNORECASE),
    re.compile(r"\b(beli|jual)\s+atau\s+\w+\b", re.IGNORECASE),
    re.compile(r"^\s*(ya|tidak|yes|no)\b", re.IGNORECASE),
)

#: Berapa kondisi konkret minimal yang pertanyaannya harus sebut.
#:
#: **Satu, dan itu bukan angka yang dipilih melainkan yang tersisa**: nol
#: berarti pertanyaan tanpa kondisi sama sekali - "simulasikan pasar" - yang
#: tidak bisa dijawab dan tidak bisa dibantah. Contoh bagian 16.4 menyebut tiga
#: (breakout, volume tinggi, open interest naik), tapi menuntut tiga akan
#: menolak peristiwa tunggal yang sah seperti perubahan regime sendirian.
MINIMUM_KONDISI = 1


class PertanyaanDitolak(ValueError):
    """Pertanyaan yang akan merusak simulasinya, berikut alasannya."""


def susun_pertanyaan(
    *, aset: str, pemicu: frozenset[Peristiwa], kondisi: tuple[str, ...]
) -> str:
    """Pertanyaan yang menyebut kondisinya, bukan menuntut satu arah.

    Bentuknya mengikuti contoh bagian 16.4: kondisi konkret lebih dulu,
    permintaan simulasi di belakang, dan tidak ada kata kerja berarah di
    antaranya.
    """
    if not pemicu:
        raise PertanyaanDitolak(
            "pertanyaan tanpa pemicu (bagian 16.2): simulasi yang dibangunkan "
            "tanpa peristiwa adalah simulasi di tiap scan, yang justru dilarang"
        )
    if len(kondisi) < MINIMUM_KONDISI:
        raise PertanyaanDitolak(
            f"pertanyaan butuh minimal {MINIMUM_KONDISI} kondisi konkret "
            f"(bagian 16.4): tanpa kondisi, jawabannya tidak bisa dibantah"
        )

    daftar = ", ".join(kondisi)
    sebab = ", ".join(sorted(p.value for p in pemicu))
    pertanyaan = (
        f"Pada {aset} terjadi {sebab} dengan kondisi: {daftar}. "
        f"Simulasikan beberapa kemungkinan perkembangan market pada beberapa "
        f"periode berikutnya, berikut syarat yang membatalkan masing-masing."
    )

    periksa_pertanyaan(pertanyaan)
    return pertanyaan


def periksa_pertanyaan(pertanyaan: str) -> None:
    """Penjaga bentuk ya/tidak (bagian 16.4).

    Dipisah dari :func:`susun_pertanyaan` dan dijalankan **olehnya**: penjaga
    yang hanya berlaku pada jalur yang menyusun sendiri tidak menjaga apa-apa
    saat seseorang kelak menyusun pertanyaannya di tempat lain.
    """
    for pola in POLA_YA_TIDAK:
        if pola.search(pertanyaan):
            raise PertanyaanDitolak(
                f"pertanyaan berbentuk ya/tidak atau berarah (bagian 16.4): "
                f"pola {pola.pattern!r} cocok. Pertanyaan semacam ini memaksa "
                f"simulasi berpihak sebelum ia mensimulasikan apa pun, dan "
                f"jawabannya adalah keputusan arah yang lolos lewat pintu "
                f"pertanyaan (bagian 16.18)"
            )
