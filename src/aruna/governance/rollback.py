"""Perubahan parameter otomatis: dicatat, dan bisa dibalikkan (bagian 23).

Bagian 23 menuntut lima bidang untuk tiap perubahan - ``old_value``,
``new_value``, ``reason``, ``trigger``, ``timestamp`` - dan jalan kembali kalau
perubahannya memperburuk performa.

**Sisi proposal sudah terpenuhi sejak lama**, dan modul ini tidak
menyentuhnya: :mod:`aruna.governance.approval` menolak menyetujui proposal yang
validasinya tidak mendukung, dan membalikkan perubahan aktif wajib menjadi
proposal baru supaya tercatat, bukan diam-diam dibatalkan.

**Yang kosong adalah sisi otomatis.** Terukur 2026-08-21: proposal
``exit-at-target`` berstatus APPROVED dengan ``parameters: []``, dan
``exit_at_target`` ternyata hanya hidup di mesin backtest - ``cli.py`` sendiri
menulis *"neither is the live rule"*. Jadi proposal tidak pernah mengubah
parameter hidup, dan tidak ada yang bisa dibalikkan dari sana.

Yang **memang** berubah sendiri dan memengaruhi keputusan adalah kalibrasi
(bagian 9): ia menimpa dirinya tiap hari lewat fase ``upkeep.review``, dan
sejak 2026-08-21 angkanya sampai ke keyakinan yang diterbitkan. Sebelum modul
ini, tidak ada catatan apa yang berubah dan tidak ada jalan kembali.

**Yang modul ini sengaja TIDAK lakukan: membekukan parameter otomatis ketika
angkanya memburuk.** Kalibrasi yang memburuk bisa berarti kalibratornya rusak,
atau bisa berarti pasarnya yang berubah - dan membekukannya pada tebakan
pertama akan mengunci kalibrasi basi di atas pasar yang sudah bergerak. Yang
disediakan di sini adalah **kemampuan** membalikkan berikut jejaknya;
pemicunya tetap keputusan yang disengaja, bukan refleks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aruna.core.clock import isoformat
from aruna.db.types import as_utc

__all__ = [
    "BATAS_RIWAYAT",
    "KUNCI_STATE",
    "PerubahanParameter",
    "balikkan",
    "catat",
    "dari_json",
    "ke_json",
    "terakhir",
]


#: Kunci di ``app_state``. Di sana, bukan di tabel sendiri: perubahan parameter
#: otomatis jumlahnya belasan per bulan, dan tabel baru untuk itu adalah
#: infrastruktur yang tidak dibayar oleh apa pun.
KUNCI_STATE = "perubahan_parameter"

#: Berapa perubahan yang disimpan. Berbatas dengan sengaja - riwayat tanpa
#: batas di ``app_state`` tumbuh selamanya, dan seluruh optimasi basis data
#: hari ini tentang tidak melakukan itu. Yang dibuang selalu yang paling lama.
BATAS_RIWAYAT = 50


@dataclass(frozen=True, slots=True)
class PerubahanParameter:
    """Satu perubahan, dengan kelima bidang yang bagian 23 minta."""

    nama: str
    lama: str
    baru: str
    alasan: str
    pemicu: str
    pada: datetime

    def ringkas(self) -> str:
        """Satu baris yang menyebut KEDUA nilainya.

        Catatan yang hanya menyebut nilai barunya membuat pembacanya tidak bisa
        tahu apa yang hilang - dan itu satu-satunya hal yang berguna dari
        catatan perubahan.
        """
        return (
            f"{self.nama}: {self.lama} -> {self.baru} "
            f"({self.alasan}; dipicu {self.pemicu})"
        )

    def ke_dict(self) -> dict[str, Any]:
        return {
            "nama": self.nama,
            "lama": self.lama,
            "baru": self.baru,
            "alasan": self.alasan,
            "pemicu": self.pemicu,
            "pada": isoformat(self.pada),
        }


def catat(
    riwayat: tuple[PerubahanParameter, ...], perubahan: PerubahanParameter
) -> tuple[PerubahanParameter, ...]:
    """Riwayat baru dengan `perubahan` di ujungnya, tetap dalam batas."""
    return (*riwayat, perubahan)[-BATAS_RIWAYAT:]


def terakhir(
    riwayat: tuple[PerubahanParameter, ...], nama: str
) -> PerubahanParameter | None:
    """Perubahan terakhir untuk satu parameter, atau ``None``."""
    for p in reversed(riwayat):
        if p.nama == nama:
            return p
    return None


def balikkan(
    riwayat: tuple[PerubahanParameter, ...],
    nama: str,
    *,
    pemicu: str,
    pada: datetime,
) -> tuple[str, PerubahanParameter]:
    """Nilai sebelum perubahan terakhir, berikut jejak pembalikannya.

    Memulangkan jejaknya, bukan hanya nilainya: bagian 23 menuntut auditable,
    dan pembalikan yang tidak tercatat membuat riwayatnya berbohong tentang apa
    yang pernah aktif. Pemanggil wajib :func:`catat` jejak itu.

    ``pemicu`` wajib berisi. Pembalikan tanpa pemicu tidak bisa menjawab
    "kenapa ini dibalikkan" berbulan-bulan kemudian - dan itu satu-satunya
    pertanyaan yang akan ditanyakan.
    """
    if not pemicu.strip():
        raise ValueError("pembalikan parameter butuh pemicu yang disebutkan")

    sebelumnya = terakhir(riwayat, nama)
    if sebelumnya is None:
        # `None` yang dipulangkan diam-diam akan diterapkan pemanggil sebagai
        # parameter, dan parameter bernilai None lebih buruk daripada error.
        raise ValueError(f"{nama} belum pernah berubah - tidak ada yang dibalikkan")

    return sebelumnya.lama, PerubahanParameter(
        nama=nama,
        lama=sebelumnya.baru,
        baru=sebelumnya.lama,
        alasan=f"dibalikkan ke nilai sebelum {isoformat(sebelumnya.pada)}",
        pemicu=pemicu.strip(),
        pada=pada,
    )


def ke_json(riwayat: tuple[PerubahanParameter, ...]) -> list[dict[str, Any]]:
    return [p.ke_dict() for p in riwayat]


def dari_json(mentah: Any) -> tuple[PerubahanParameter, ...]:
    """Baca dari ``app_state``, memaafkan bentuk yang tidak dikenal.

    Riwayat yang meledak lebih buruk daripada riwayat yang kosong: yang pertama
    menjatuhkan siklus upkeep, yang kedua hanya kehilangan jejak.
    """
    if not isinstance(mentah, list):
        return ()
    keluar: list[PerubahanParameter] = []
    for isi in mentah:
        if not isinstance(isi, dict):
            continue
        try:
            keluar.append(
                PerubahanParameter(
                    nama=str(isi["nama"]),
                    lama=str(isi["lama"]),
                    baru=str(isi["baru"]),
                    alasan=str(isi["alasan"]),
                    pemicu=str(isi["pemicu"]),
                    pada=as_utc(datetime.fromisoformat(str(isi["pada"]))),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(keluar)
