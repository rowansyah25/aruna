"""Keputusan tidak boleh berbalik tanpa konfirmasi (bagian 18.25 - 18.28).

**Celah yang ditemukan 2026-08-24.** :mod:`aruna.signals.repetition` sudah
menahan pengulangan - kandidat yang arahnya SAMA dengan yang barusan, di setup
yang belum berubah, ditolak sebagai duplikat (PASAL 11.6). Tapi sebuah
**pembalikan** bukan duplikat: LONG lalu SHORT punya arah yang berbeda, jadi ia
lolos setiap pemeriksaan yang ada.

Akibatnya urutan yang bagian 18.25 larang bisa terjadi tanpa satu pun penjaga::

    10:00 LONG   10:01 NO SIGNAL   10:02 LONG   10:03 SHORT   10:04 LONG

Aturan konfirmasinya
====================

Sebuah pembalikan mengklaim **kebalikan** dari yang baru saja ARUNA katakan.
Buktinya karena itu harus setidaknya sekuat bukti yang melahirkan klaim
pertama - kalau tidak, ARUNA berbalik atas dasar yang lebih tipis daripada
dasar ia berkomitmen.

Yang dituntut di sini bukan ambang baru melainkan yang bisa diperiksa: **harga
harus benar-benar bergerak MELAWAN arah sebelumnya**, sebesar
:data:`~aruna.signals.repetition.MATERIAL_MOVE_PCT`. Klaim lama tidak cukup
"kurang didukung" - ia harus terbukti salah.

Ambangnya dipinjam, bukan diketik ulang: pertanyaannya sama dengan yang
`is_duplicate` ajukan - berapa gerak yang membuat ini keadaan yang berbeda.

**Menahan pembalikan, bukan membalik keputusan.** Pembalikan yang tidak
terkonfirmasi menjadi NO SIGNAL, tidak menjadi "tetap LONG" - yang kedua berarti
ARUNA memaksakan pandangan lama atas bukti yang sudah goyah, dan bagian 18.43
melarang gerbang mengubah arah.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.signals.repetition import MATERIAL_MOVE_PCT

__all__ = [
    "Peralihan",
    "hitung_pembalikan",
    "perlu_konfirmasi",
]


@dataclass(frozen=True, slots=True)
class Peralihan:
    """Satu pembalikan keputusan, berikut sebabnya (bagian 18.28).

    Dicatat lengkap karena pembalikan adalah kejadian yang paling mahal untuk
    tidak bisa dijelaskan: pembacanya baru saja diberi tahu satu hal, lalu
    diberi tahu kebalikannya.
    """

    symbol: str
    horizon: str
    sebelum: str
    sesudah: str
    pada: datetime
    #: Gerak harga sejak keputusan sebelumnya, dalam persen. Tandanya relatif
    #: terhadap harga, bukan terhadap arah - penafsirannya milik pembaca.
    gerak_pct: float | None = None
    alasan: tuple[str, ...] = field(default_factory=tuple)

    @property
    def terkonfirmasi(self) -> bool:
        return not self.alasan

    def ringkas(self) -> str:
        gerak = "gerak tak terukur" if self.gerak_pct is None else f"{self.gerak_pct:+.2f}%"
        return f"{self.symbol} {self.horizon}: {self.sebelum} -> {self.sesudah} ({gerak})"


def _melawan(arah: Any, gerak_pct: float) -> float:
    """Seberapa jauh harga bergerak MELAWAN ``arah``, dalam persen.

    Negatif berarti bergerak mendukung. Arah yang tidak dikenali memulangkan
    nol - bukan angka besar: keputusan yang arahnya tidak terbaca tidak boleh
    diam-diam dianggap terbantah.
    """
    nama = str(getattr(arah, "value", arah) or "").upper()
    if nama == "BUY":
        return -gerak_pct
    if nama == "SELL":
        return gerak_pct
    return 0.0


def perlu_konfirmasi(
    sebelumnya: Any, kandidat: Any, *, gerak_pct: float | None
) -> tuple[str, ...]:
    """Alasan kenapa pembalikan ini belum boleh terbit, atau kosong.

    Kosong berarti boleh - entah karena ini bukan pembalikan sama sekali, atau
    karena pembalikannya sudah terkonfirmasi.

    ``gerak_pct`` adalah perubahan harga sejak keputusan sebelumnya. ``None``
    berarti **belum bisa diperiksa**, dan itu menahan: sebuah pembalikan yang
    tidak bisa dibuktikan terkonfirmasi tidak boleh lewat hanya karena
    pengukurannya gagal.
    """
    if sebelumnya is None or kandidat is None:
        return ()

    lama = str(getattr(sebelumnya.direction, "value", sebelumnya.direction) or "")
    baru = str(getattr(kandidat.direction, "value", kandidat.direction) or "")
    if lama == baru:
        return ()
    if not _berarah(lama) or not _berarah(baru):
        # Berhenti berpendapat atau mulai berpendapat bukan pembalikan.
        # Menuntut konfirmasi untuk berhenti akan membuat ARUNA bertahan pada
        # pandangan justru ketika buktinya menghilang.
        return ()

    if gerak_pct is None:
        return (
            f"pembalikan {lama} -> {baru} tidak bisa diperiksa: gerak harga "
            "sejak keputusan sebelumnya tidak terukur",
        )

    lawan = _melawan(sebelumnya.direction, gerak_pct)
    if lawan < MATERIAL_MOVE_PCT:
        return (
            f"pembalikan {lama} -> {baru} belum terkonfirmasi: harga baru "
            f"bergerak {lawan:+.2f}% melawan {lama}, butuh "
            f"{MATERIAL_MOVE_PCT:.2f}%",
        )
    return ()


def _berarah(nama: str) -> bool:
    return nama.upper() in ("BUY", "SELL")


def hitung_pembalikan(riwayat: Any) -> tuple[int, int]:
    """``(pembalikan, yang_terkonfirmasi)`` dari deret :class:`Peralihan`.

    Untuk laporan harian (bagian 18.52). Yang dilaporkan **dua angka, bukan
    satu**: empat pembalikan yang seluruhnya terkonfirmasi adalah pasar yang
    memang berbalik empat kali, sementara empat yang tak satu pun terkonfirmasi
    adalah ARUNA yang bergoyang. Satu angka "pembalikan: 4" tidak membedakan
    keduanya.
    """
    daftar = list(riwayat or ())
    return len(daftar), sum(1 for p in daftar if p.terkonfirmasi)
