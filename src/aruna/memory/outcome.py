"""Apa yang terjadi pada kondisi-kondisi serupa (PASAL 15.9, 15.10, 15.37).

**Sampel kecil bukan bukti, dan modul ini menolak berpura-pura sebaliknya.**
Tiga kasus yang semuanya menang menghasilkan "win rate 100%" - angka yang
terdengar sama meyakinkannya dengan yang dari seribu kasus, dan tidak
menerangkan apa pun. :data:`SAMPEL_MINIMUM` yang menahannya, dan
``Ringkasan.cukup`` yang membuat penahanan itu bisa dibaca pemanggil.

**Arah tanpa kasus bukan nol persen.** ``win_rate["SHORT"] = 0`` dibaca sebagai
"SHORT selalu kalah"; yang benar adalah tidak ada satu pun kasus SHORT untuk
dinilai. Bedanya nyata bagi yang membaca, jadi yang kedua memulangkan ``None``.

**Yang tidak berarah tidak ikut dihitung.** ``WAIT`` adalah 59% dari sejarah
ARUNA (terukur 2026-08-21); menghitungnya sebagai kekalahan LONG akan
menenggelamkan win rate yang sesungguhnya di bawah keputusan yang memang tidak
mengambil posisi apa pun.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from aruna.memory.record import Hasil, Ingatan
from aruna.memory.similarity import Kemiripan

#: Jumlah kasus serupa minimum sebelum hasilnya boleh diringkas jadi angka.
#:
#: Dua puluh, dan bukan angka bulat yang lebih besar: korpus ARUNA baru
#: beberapa hari (terukur 2026-08-17 s/d 08-20), dan ambang yang terlalu tinggi
#: berarti ingatan tidak pernah bisa berbicara sama sekali. Yang di bawahnya
#: tetap dilaporkan - jumlahnya disebut - hanya tidak diringkas jadi persen.
SAMPEL_MINIMUM = 20

#: Dieja persis seperti pasalnya, supaya yang mencari kalimatnya di log dan di
#: pesan menemukan hal yang sama.
KALIMAT_TIDAK_CUKUP = "INSUFFICIENT HISTORICAL SAMPLE"
KALIMAT_TIDAK_ADA = "NO SIGNIFICANT HISTORICAL MATCH"

#: Ejaan spot -> ejaan PASAL 15.10. ``WAIT`` dan ``NO_SIGNAL`` sengaja tidak
#: ada di sini: keduanya keputusan untuk tidak mengambil posisi, dan posisi
#: yang tidak diambil tidak punya menang atau kalah.
_ARAH: dict[str, str] = {
    "BUY": "LONG",
    "LONG": "LONG",
    "SELL": "SHORT",
    "SHORT": "SHORT",
}


@dataclass(frozen=True, slots=True)
class Ringkasan:
    """Ringkasan kasus serupa. Angkanya menyebut dirinya sendiri."""

    total: int
    per_arah: dict[str, int]
    #: Persen kemenangan per arah, atau ``None`` kalau tidak ada kasusnya.
    win_rate: dict[str, int | None]
    rentang_similarity: tuple[int, int]
    rentang_waktu: tuple[datetime, datetime] | None

    @property
    def cukup(self) -> bool:
        return self.total >= SAMPEL_MINIMUM

    @property
    def kalimat(self) -> str | None:
        """Kalimat yang wajib menggantikan angka saat sampelnya tidak layak."""
        if not self.total:
            return KALIMAT_TIDAK_ADA
        if not self.cukup:
            return KALIMAT_TIDAK_CUKUP
        return None


def ringkas(cocok: Sequence[tuple[Ingatan, Kemiripan]]) -> Ringkasan:
    """Ringkas kasus serupa menjadi angka yang boleh dibaca (PASAL 15.10)."""
    per_arah: dict[str, int] = {"LONG": 0, "SHORT": 0}
    menang: dict[str, int] = {"LONG": 0, "SHORT": 0}
    dinilai: dict[str, int] = {"LONG": 0, "SHORT": 0}

    skor: list[int] = []
    waktu: list[datetime] = []

    for ingatan, mirip in cocok:
        skor.append(mirip.skor)
        waktu.append(ingatan.locked_at)

        arah = _ARAH.get(str(ingatan.arah).strip().upper())
        if arah is None:
            continue
        per_arah[arah] += 1
        # Hanya yang hasilnya benar-benar menang atau kalah yang masuk
        # penyebut. NEUTRAL dan UNKNOWN bukan kekalahan.
        if ingatan.hasil in (Hasil.WIN, Hasil.LOSS):
            dinilai[arah] += 1
            if ingatan.hasil is Hasil.WIN:
                menang[arah] += 1

    return Ringkasan(
        total=len(cocok),
        per_arah=per_arah,
        win_rate={
            arah: round(menang[arah] * 100 / dinilai[arah])
            if dinilai[arah]
            else None
            for arah in per_arah
        },
        rentang_similarity=(min(skor), max(skor)) if skor else (0, 0),
        rentang_waktu=(min(waktu), max(waktu)) if waktu else None,
    )


__all__ = [
    "KALIMAT_TIDAK_ADA",
    "KALIMAT_TIDAK_CUKUP",
    "SAMPEL_MINIMUM",
    "Ringkasan",
    "ringkas",
]
