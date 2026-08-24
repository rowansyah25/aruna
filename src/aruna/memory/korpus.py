"""Rekam jejak kasus serupa, untuk faktor ``historical`` (bagian 18.4).

**Yang tersisa dari modul ini kecil, dan ceritanya perlu diketahui pembaca
berikutnya.** Modul ini dibangun 2026-08-24 untuk merangkai rekam jejak ke jalur
spot, lengkap dengan pembaca korpus ber-TTL dan pencari kemiripan. Jalur spot
dicabut sehari kemudian atas keputusan operator, dan pembaca korpusnya ikut -
satu-satunya pemakainya hilang, dan kode tanpa pemakai adalah kode yang tidak
bisa dibuktikan benar.

Yang bertahan aturan yang **dipakai jalur futures**: bagaimana satu ringkasan
kasus serupa dibaca menjadi akurasi dan sampel. Ia tinggal di sini, bukan di
``futures/service.py``, supaya kalau kelak ada pemakai kedua ia meminjam - bukan
menyalin - dan "rekam jejak berapa" tidak punya dua jawaban.
"""

from __future__ import annotations

from typing import Any

from aruna.memory.outcome import EJAAN_ARAH

#: Berapa ingatan yang boleh diambil dalam satu pembacaan korpus.
#:
#: Dipakai ``FuturesService._bahan_ingatan``. Tinggal di sini dan bukan di sana
#: karena ia menjawab pertanyaan tentang **korpus**, bukan tentang futures -
#: dan batas yang berbeda di dua pembaca berarti dua ukuran sampel di bawah satu
#: nama "rekam jejak".
MEMORY_KANDIDAT = 6000


def rekam_jejak(ringkasan: Any, arah: Any) -> tuple[float | None, int]:
    """Akurasi kasus serupa untuk ARAH yang diambil, dan sampelnya.

    Untuk :func:`~aruna.signals.quality.historical_factor`, yang menerima
    akurasi 0..1 dan jumlah sampel.

    **Per arah, bukan keseluruhan.** Rekam jejak LONG di kondisi ini tidak
    mengatakan apa pun tentang SHORT, dan meratakannya menghasilkan angka yang
    bukan rekam jejak salah satunya.

    **Sampelnya yang DINILAI, bukan yang cocok.**
    :attr:`~aruna.memory.outcome.Ringkasan.per_arah` menghitung seluruh kasus di
    arah itu termasuk yang hasilnya ``NEUTRAL`` - memakainya akan melaporkan
    rekam jejak lebih tebal daripada yang benar-benar ada. Terukur 2026-08-24:
    satu sidik nyata mencocokkan 161 ingatan, dan hanya **11** di antaranya
    pernah menang atau kalah.

    Gerbang :attr:`~aruna.memory.outcome.Ringkasan.cukup` dihormati lebih dulu:
    Phase 15 menolak mengubah korpus setipis itu menjadi persen, dan mutu yang
    memakainya diam-diam akan menerbitkan angka yang pemiliknya sendiri tolak
    cetak. Ambang kedua - ``needed`` milik ``historical_factor`` - menjawab
    pertanyaan yang berbeda: bukan "boleh disebut?" melainkan "cukup untuk
    dinilai?".
    """
    if ringkasan is None or not getattr(ringkasan, "cukup", False):
        return None, 0
    kunci = EJAAN_ARAH.get(
        str(getattr(arah, "value", arah) or "").strip().upper()
    )
    if kunci is None:
        return None, 0
    persen = (getattr(ringkasan, "win_rate", None) or {}).get(kunci)
    sampel = int((getattr(ringkasan, "dinilai", None) or {}).get(kunci, 0))
    return (None if persen is None else float(persen) / 100.0), sampel


__all__ = [
    "MEMORY_KANDIDAT",
    "rekam_jejak",
]
