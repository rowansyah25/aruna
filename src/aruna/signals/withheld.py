"""Kenapa sebuah analisis tidak menjadi signal (PASAL 11.12).

Hampir semua yang PASAL 11.12 minta sudah tersimpan sebelum modul ini ada -
bukti bullish dan bearish lewat ``council_votes``, confidence dan signal
quality lewat ``signal_snapshots``, ambang tiap faktor lewat
``quality_detail``. Yang hilang cuma satu hal, dan hal itu yang membuat
sisanya sulit dipakai: **alasannya berupa prosa bebas.**

    withheld_reason: "verdict is WAIT, not a position"

Kalimat itu benar dan tidak bisa dihitung. Pertanyaan yang sebenarnya ingin
dijawab operator - *kenapa NO SIGNAL sebanyak ini* - adalah pertanyaan
pengelompokan, dan mengelompokkan prosa berarti mencocokkan potongan teks yang
berubah setiap kali kalimatnya diperbaiki. Seratus penahanan karena confidence
di bawah lantai dan seratus karena data basi adalah dua masalah yang sangat
berbeda dengan dua perbaikan yang sangat berbeda, dan keduanya terbaca sama
selama alasannya hanya kalimat.

Kodenya **bukan kategori yang dikarang**. Masing-masing adalah cabang yang
memang ada di ``SignalService.lock_signals``, satu per satu - jadi daftar ini
tidak bisa lebih halus atau lebih kasar daripada keputusan yang sungguhan
diambil. Cabang baru harus menambah kode baru di sini, dan itu disengaja:
sebuah penahanan yang tidak punya nama akan menumpuk di ``UNKNOWN`` sampai
seseorang bertanya kenapa.

**Prosanya tidak dibuang.** Kode menjawab "kelompok apa", kalimat menjawab
"apa persisnya", dan keduanya disimpan. Mengganti kalimat dengan kode akan
menghapus satu-satunya tempat yang menyebut angka yang meleset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WithheldCode(StrEnum):
    """Nilainya data - jangan diterjemahkan."""

    #: Council sendiri tidak memilih arah. Ini BUKAN penolakan: analisisnya
    #: selesai dan kesimpulannya "tidak ada posisi". Dipisahkan dari yang lain
    #: karena menyatukannya membuat ARUNA yang memang sedang menunggu pasar
    #: terlihat seperti ARUNA yang rusak.
    NON_DIRECTIONAL = "NON_DIRECTIONAL"

    #: Berarah, tapi keyakinannya di bawah lantai publikasi.
    CONFIDENCE_FLOOR = "CONFIDENCE_FLOOR"

    #: Buktinya lebih tua daripada horizon yang diprediksinya.
    STALE_EVIDENCE = "STALE_EVIDENCE"

    #: Gagal gerbang kualitas (PASAL 11.13).
    QUALITY_GATE = "QUALITY_GATE"

    #: Mengulang prediksi yang masih berjalan (PASAL 11.6).
    DUPLICATE = "DUPLICATE"

    #: Masih dalam jeda sesudah kalah (PASAL 11.5).
    COOLDOWN = "COOLDOWN"

    #: Cabang yang belum punya nama. Kalau angka ini bertumbuh, ada keputusan
    #: yang diambil sistem dan tidak ada yang bisa menjelaskannya.
    UNKNOWN = "UNKNOWN"


#: Penahanan yang menunjuk **input**, bukan keputusan - ini yang pantas
#: membangunkan pembaca log.
#:
#: Sisanya adalah disiplin yang berjalan benar: masa tenang sesudah kalah,
#: duplikat dari prediksi yang masih terbuka, keyakinan di bawah lantai,
#: council yang memang tidak memilih arah. Semuanya tetap dicatat - hanya
#: sebagai keterangan, bukan peringatan.
#:
#: **Terukur:** dari 765 penahanan, 359 adalah masa tenang dan duplikat.
#: Peringatan yang isinya sistem bekerja benar melatih pembacanya melewati
#: baris WARNING, dan yang hilang berikutnya adalah 40 gerbang mutu yang
#: benar-benar menunjuk data bermasalah.
#:
#: ``UNKNOWN`` ikut di sini dengan sengaja: sebuah penahanan yang tidak bisa
#: dikelompokkan berarti ada keputusan yang diambil sistem dan tidak ada yang
#: bisa menjelaskannya. Itu justru yang paling perlu dilihat.
PERLU_PERHATIAN: frozenset[WithheldCode] = frozenset({
    WithheldCode.STALE_EVIDENCE,
    WithheldCode.QUALITY_GATE,
    WithheldCode.UNKNOWN,
})


@dataclass(frozen=True, slots=True)
class Withheld:
    """Satu penahanan: kelompoknya, kalimatnya, dan angka yang meleset."""

    code: WithheldCode
    reason: str
    #: Nilai yang diukur dan ambang yang seharusnya dilewati, kalau ada.
    #:
    #: Disimpan berpasangan. "Confidence 0,41" tidak berarti apa-apa tanpa
    #: lantainya, dan "lantai 0,55" tidak berarti apa-apa tanpa nilainya -
    #: yang bisa dipakai hanya keduanya bersama.
    measured: float | None = None
    threshold: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "reason": self.reason,
            "measured": self.measured,
            "threshold": self.threshold,
            **self.extra,
        }


#: Frasa yang dipakai ``should_lock`` hari ini, dipetakan ke kodenya.
#:
#: Pencocokan teks di sini adalah utang, dan disebut utang. Sumber yang benar
#: adalah ``should_lock`` yang mengembalikan kodenya sendiri; sampai itu terjadi
#: peta ini menerjemahkan kalimatnya - dan ``classify`` mengembalikan
#: ``UNKNOWN`` daripada menebak, supaya kalimat yang berubah muncul sebagai
#: angka yang bisa dilihat, bukan sebagai salah kelompok yang diam.
_PHRASES: tuple[tuple[str, WithheldCode], ...] = (
    ("not a position", WithheldCode.NON_DIRECTIONAL),
    ("non-directional", WithheldCode.NON_DIRECTIONAL),
    ("quality gate", WithheldCode.QUALITY_GATE),
    ("duplikat", WithheldCode.DUPLICATE),
    ("cooldown", WithheldCode.COOLDOWN),
    ("confidence", WithheldCode.CONFIDENCE_FLOOR),
    ("stale", WithheldCode.STALE_EVIDENCE),
    ("evidence older", WithheldCode.STALE_EVIDENCE),
)


def classify(reason: str | None) -> WithheldCode:
    """Kelompokkan sebuah kalimat penahanan.

    Mengembalikan ``UNKNOWN`` untuk kalimat yang tidak dikenali, bukan menebak
    yang paling mirip. Salah kelompok yang diam lebih buruk daripada kelompok
    "tidak diketahui" yang bertumbuh: yang pertama membuat hitungan terlihat
    lengkap sambil salah, yang kedua terlihat sebagai pertanyaan.
    """
    if not reason:
        return WithheldCode.UNKNOWN
    lowered = reason.lower()
    for phrase, code in _PHRASES:
        if phrase in lowered:
            return code
    return WithheldCode.UNKNOWN


def tally(reasons: Any) -> dict[str, int]:
    """Hitung penahanan per kelompok, terbanyak lebih dulu.

    Inilah bentuk yang menjawab "kenapa NO SIGNAL sebanyak ini": satu daftar
    pendek yang bisa dibaca dalam sekali lihat, bukan seribu kalimat.
    """
    counts: dict[str, int] = {}
    for reason in reasons or ():
        code = classify(reason)
        counts[code.value] = counts.get(code.value, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


__all__ = [
    "PERLU_PERHATIAN",
    "Withheld",
    "WithheldCode",
    "classify",
    "tally",
]
