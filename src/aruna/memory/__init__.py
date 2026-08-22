"""Market Memory & Context Engine (PASAL 15).

Menjawab satu pertanyaan: *"apakah aku pernah melihat kondisi seperti ini, dan
apa yang terjadi waktu itu?"* - sebagai **bukti tambahan** untuk Phase 14,
bukan sebagai pengambil keputusan (PASAL 15.42).

Paket ini murni: tanpa I/O, tanpa database, tanpa jaringan. Yang menyentuh
database hanya :mod:`aruna.db.repositories.memory`, dan yang menyambungkannya
ke keputusan hanya :mod:`aruna.futures.service`.

Ingatan ARUNA **tidak menyimpan ulang** apa pun. Ia proyeksi dari
``signal_snapshots`` dan ``outcome_snapshots`` yang keduanya sudah immutable -
PASAL 15.27 melarang menyimpan raw market data berulang, dan sumbernya sudah
menyimpan kebenarannya.

**PASAL 15.1 — ANALYST ONLY.** Tidak ada satu pun jalur dari paket ini menuju
eksekusi: tidak ada pemanggilan order, tidak ada perubahan posisi, tidak ada
adapter bursa. Yang dipulangkannya hanya bacaan, dan pembacanya hanya penyusun
pesan.

**PASAL 15.46 — alur mesin konteks**, dan di mana tiap langkahnya hidup::

    kondisi sekarang        futures/service.py:_konteks_historis
      -> sidik jari         memory/fingerprint.py:Sidik
      -> cari ingatan       db/repositories/memory.py:cari      (PASAL 15.29)
      -> peringkat mirip    memory/similarity.py + ranking.py
      -> saring mutu        memory/record.py:mutu_dari
      -> hasil historis     memory/outcome.py:ringkas
      -> periksa konflik    memory/context.py:Pengaruh
      -> kontribusi         memory/context.py:_kontribusi      (PASAL 15.30)
      -> PHASE 14           futures/service.py:attach_memory

**PASAL 15.47 — tempat Phase 15 dalam arsitektur.** Ia berdiri antara Phase 13
dan Phase 14: memberi konteks, tidak memutuskan. Phase 12 tetap yang belajar
(PASAL 15.33), Phase 14 tetap yang memutuskan (PASAL 15.42).

**PASAL 15.49 — dan ini yang membedakannya dari sekadar analisis.** ARUNA tidak
lagi hanya menjawab "apa yang sedang terjadi", tapi juga "pernahkah aku melihat
yang seperti ini", "apa yang terjadi waktu itu", "seberapa relevan", dan
"apakah bukti sekarang sejalan atau bertentangan". Keputusan finalnya tetap
milik Phase 14.
"""

from aruna.memory.context import (
    MARGIN_PENGARUH,
    MAX_JEJAK_ID,
    KonteksHistoris,
    Pengaruh,
    susun,
)
from aruna.memory.dimensions import (
    TAK_TERSIMPAN,
    TERSIMPAN,
    UNKNOWN,
    Dimensi,
    diketahui,
    sama,
)
from aruna.memory.fingerprint import (
    QUALITY_HIGH,
    QUALITY_LOW,
    SPREAD_TIGHT,
    SPREAD_WIDE,
    Sidik,
    band_kualitas,
    band_likuiditas,
    band_news,
)
from aruna.memory.outcome import (
    KALIMAT_TIDAK_ADA,
    KALIMAT_TIDAK_CUKUP,
    SAMPEL_MINIMUM,
    Ringkasan,
    ringkas,
)
from aruna.memory.ranking import SETENGAH_UMUR_HARI, bobot_kebaruan, peringkat
from aruna.memory.record import (
    KUNCI_UNIK,
    Hasil,
    Ingatan,
    Mutu,
    mutu_dari,
)
from aruna.memory.similarity import AMBANG_MIRIP, BOBOT, Kemiripan, bandingkan

__all__ = [
    "AMBANG_MIRIP",
    "BOBOT",
    "KALIMAT_TIDAK_ADA",
    "KALIMAT_TIDAK_CUKUP",
    "KUNCI_UNIK",
    "MARGIN_PENGARUH",
    "MAX_JEJAK_ID",
    "QUALITY_HIGH",
    "QUALITY_LOW",
    "SAMPEL_MINIMUM",
    "SETENGAH_UMUR_HARI",
    "SPREAD_TIGHT",
    "SPREAD_WIDE",
    "TAK_TERSIMPAN",
    "TERSIMPAN",
    "UNKNOWN",
    "Dimensi",
    "Hasil",
    "Ingatan",
    "Kemiripan",
    "KonteksHistoris",
    "Mutu",
    "Pengaruh",
    "Ringkasan",
    "Sidik",
    "band_kualitas",
    "band_likuiditas",
    "band_news",
    "bandingkan",
    "bobot_kebaruan",
    "diketahui",
    "mutu_dari",
    "peringkat",
    "ringkas",
    "sama",
    "susun",
]
