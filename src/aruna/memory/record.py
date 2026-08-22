"""Satu rekaman ingatan, dan seberapa layak ia dipercaya (PASAL 15.3, 15.24-15.26).

**Bekunya bukan gaya penulisan.** PASAL 15.25 melarang mengubah outcome,
menghapus LOSS, mengubah signal lama, timestamp, agent vote, dan versi model
sesudah hasilnya final. Larangan yang hanya ada di dokumen akan dilanggar oleh
kode yang tidak membaca dokumen; ``frozen=True`` memindahkannya ke tipe, jadi
"mengubah outcome" berhenti menjadi pilihan yang tersedia.

Koreksi tetap mungkin, dan caranya satu: **rekaman koreksi baru**, bukan
overwrite. Itu sebabnya tidak ada satu pun metode di kelas ini yang memulangkan
salinan yang diubah.

Mutunya (PASAL 15.24) dinilai dari tiga hal yang bisa diperiksa, bukan dari
selera: seberapa banyak dimensinya terbaca, apakah hasilnya sudah final, dan
apakah waktunya masuk akal. Ingatan tanpa hasil tidak bisa mengajari apa pun
tentang hasil - ia **selalu** LOW, berapa pun cakupannya.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from aruna.memory.fingerprint import Sidik

#: Ambang cakupan untuk mutu. Cakupan adalah persen bobot dimensi yang
#: benar-benar terbaca - lihat :mod:`aruna.memory.similarity`.
CAKUPAN_TINGGI = 70
CAKUPAN_RENDAH = 40

#: Kunci yang membuat satu peristiwa hanya punya satu ingatan (PASAL 15.26).
#:
#: ``signal_id`` sudah UNIQUE di ``signal_snapshots``, jadi memakainya berarti
#: anti-duplikat ingatan bersandar pada jaminan yang sudah ditegakkan database -
#: bukan pada kunci baru yang suatu saat berselisih dengan yang lama.
KUNCI_UNIK: tuple[str, ...] = ("signal_id",)


class Mutu(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Hasil(StrEnum):
    """Nasib satu keputusan lama.

    ``NEUTRAL`` bukan ``UNKNOWN``: yang pertama berarti harganya diukur dan
    tidak bergerak berarti, yang kedua berarti tidak ada yang mengukurnya.
    """

    WIN = "WIN"
    LOSS = "LOSS"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Ingatan:
    """Satu kondisi pasar yang pernah terjadi, beserta apa yang menyusulnya."""

    signal_id: str
    sidik: Sidik
    #: Keputusan yang diambil waktu itu - BUY / SELL / WAIT / NO_SIGNAL.
    arah: str
    hasil: Hasil
    #: Gerak pasar sesudahnya, apa adanya: positif berarti harga naik. Yang
    #: membalik tandanya untuk posisi SHORT adalah lapisan yang menilai, bukan
    #: yang mencatat - membaliknya dua kali membuat kekalahan besar tercatat
    #: sebagai kemenangan besar.
    move_pct: Decimal | None
    locked_at: datetime
    resolved_at: datetime | None
    model_version: str
    cakupan: int
    mutu: Mutu

    @property
    def final(self) -> bool:
        return self.hasil is not Hasil.UNKNOWN and self.resolved_at is not None


def mutu_dari(
    *,
    cakupan: int,
    hasil: Hasil,
    locked_at: datetime,
    resolved_at: datetime | None,
) -> Mutu:
    """Mutu satu ingatan (PASAL 15.24).

    Tiga hal menjatuhkannya ke ``LOW`` tanpa syarat, dan ketiganya berarti
    ingatan itu tidak bisa dipakai untuk menyimpulkan hasil:

    * hasilnya belum final - tidak ada yang bisa dipelajari darinya;
    * waktu resolusinya tidak ada - PASAL 15.39 tidak bisa menyaringnya, dan
      yang tidak bisa disaring tidak boleh berbobot tinggi;
    * waktunya terbalik - jam yang salah atau baris yang tertukar, dan
      penyaring waktu akan tetap terlihat bekerja di atasnya.
    """
    if hasil is Hasil.UNKNOWN or resolved_at is None:
        return Mutu.LOW
    if resolved_at < locked_at:
        return Mutu.LOW
    if cakupan >= CAKUPAN_TINGGI:
        return Mutu.HIGH
    if cakupan < CAKUPAN_RENDAH:
        return Mutu.LOW
    return Mutu.MEDIUM


__all__ = [
    "CAKUPAN_RENDAH",
    "CAKUPAN_TINGGI",
    "KUNCI_UNIK",
    "Hasil",
    "Ingatan",
    "Mutu",
    "mutu_dari",
]
