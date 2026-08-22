"""Bentuk satu skenario (bagian 16.7, 16.15).

Sebelas bidang yang bagian 16.15 minta, ditambah rincian bagian 16.7. Bukan
karena kelengkapan itu indah, melainkan karena skenario yang tidak menyebut
pemicunya tidak bisa dibantah, dan yang tidak menyebut invalidasinya tidak bisa
salah.

**Tiga hal yang modul ini sengaja tolak.**

Ia tidak punya bidang arah. Bagian 16.18: Phase 16 menghasilkan *scenario
evidence*, bukan FINAL LONG atau FINAL SHORT. Skenario yang membawa arah
berhenti menjadi bukti dan menjadi mesin keputusan kedua - dan keputusan final
milik Phase 14.

Ia tidak menyebut bobotnya probabilitas. Bagian 16.6 menyatakannya dengan huruf
besar: *scenario weight bukan probability pasar yang telah terkalibrasi secara
statistik*. Nama seperti ``probability`` akan membuat pembaca berikutnya
memperlakukannya begitu, dan bagian 16.1 justru melarang hasil simulasi
dianggap kepastian.

Ia menolak skenario tanpa invalidasi. Bagian 16.11 menuntutnya, dan alasannya
lebih dalam daripada kelengkapan bidang: skenario yang tidak bisa salah bukan
skenario melainkan keyakinan yang dipakaikan format.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "LABEL_BUKTI",
    "HasilSkenario",
    "Invalidasi",
    "Kerapuhan",
    "Skenario",
]


#: Bagian 16.1. Melekat pada tiap keluaran, dan tidak bisa dilepas: hasil
#: simulasi yang beredar tanpa label ini akan dibaca sebagai FACT oleh siapa
#: pun yang menerimanya di ujung sana.
LABEL_BUKTI = "SIMULATION EVIDENCE"

#: Bagian 16.6, dibawa bersama angkanya. Angka tanpa kalimat ini akan
#: diperlakukan sebagai peluang pasar dalam waktu satu pembacaan.
CATATAN_BOBOT = (
    "keluaran simulasi relatif, bukan probabilitas pasar yang terkalibrasi"
)


class Kerapuhan(StrEnum):
    """Bagian 16.10.

    ``RAPUH`` ketika seluruh skenario runtuh oleh satu syarat yang hilang.
    Itu bukan cacat skenarionya - itu keterangan yang paling berguna tentang
    dia, dan menyembunyikannya membuat skenario bergantung-satu-benang terbaca
    sekokoh yang berdiri di atas beberapa.
    """

    RAPUH = "RAPUH"
    KOKOH = "KOKOH"


class HasilSkenario(StrEnum):
    """Bagian 16.19.

    Tiga yang pasalnya sebut, ditambah ``BELUM``: skenario yang horizonnya
    belum lewat bukan skenario yang salah, dan menyatukan keduanya membuat
    evaluasi menghukum simulasi karena waktu belum berjalan.
    """

    BENAR = "BENAR"
    SALAH = "SALAH"
    SEBAGIAN = "SEBAGIAN"
    BELUM = "BELUM"


@dataclass(frozen=True, slots=True)
class Invalidasi:
    """Syarat yang, kalau terjadi, membatalkan skenario (bagian 16.11)."""

    syarat: tuple[str, ...] = field(default_factory=tuple)

    @property
    def kerapuhan(self) -> Kerapuhan:
        return Kerapuhan.RAPUH if len(self.syarat) <= 1 else Kerapuhan.KOKOH


@dataclass(frozen=True, slots=True)
class Skenario:
    """Satu kemungkinan perkembangan, berikut cara membantahnya."""

    scenario_id: str
    market: str
    asset: str
    timestamp: datetime
    nama: str
    deskripsi: str
    kondisi_awal: tuple[str, ...]
    pemicu: str
    #: Rantai konsekuensi, bukan satu kalimat - bagian 16.8 meminta efek
    #: orde-dua, dan efek orde-dua adalah urutan.
    perkembangan: tuple[str, ...]
    invalidasi: Invalidasi
    risiko: str
    keyakinan: float
    #: 0-100, relatif terhadap skenario lain pada simulasi yang sama.
    #: Lihat :data:`CATATAN_BOBOT`.
    bobot: int
    bukti: tuple[str, ...]
    versi_simulasi: str

    def __post_init__(self) -> None:
        if not self.invalidasi.syarat:
            raise ValueError(
                "skenario tanpa invalidasi ditolak (bagian 16.11): skenario "
                "yang tidak bisa salah bukan skenario melainkan keyakinan "
                "yang dipakaikan format"
            )

    @property
    def kerapuhan(self) -> Kerapuhan:
        return self.invalidasi.kerapuhan

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": LABEL_BUKTI,
            "scenario_id": self.scenario_id,
            "market": self.market,
            "asset": self.asset,
            "timestamp": self.timestamp.isoformat(),
            "nama": self.nama,
            "deskripsi": self.deskripsi,
            "kondisi_awal": list(self.kondisi_awal),
            "pemicu": self.pemicu,
            "perkembangan": list(self.perkembangan),
            "invalidasi": list(self.invalidasi.syarat),
            "kerapuhan": self.kerapuhan.value,
            "risiko": self.risiko,
            "keyakinan": round(self.keyakinan, 3),
            "bobot": self.bobot,
            "bobot_catatan": CATATAN_BOBOT,
            "bukti": list(self.bukti),
            "versi_simulasi": self.versi_simulasi,
        }
