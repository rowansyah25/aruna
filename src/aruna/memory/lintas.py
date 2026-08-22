"""Aset lain sebagai konteks (PASAL 15.18), dan batas jujur tentangnya.

Pasalnya mencontohkan DXY dan emas: *"broad risk-on environment"*. **Keduanya
tidak ada di universe ARUNA** - terukur 2026-08-21, ``assets`` berisi 31 baris,
seluruhnya pasangan USDT kripto dan saham IDX. Tidak ada indeks dolar, tidak
ada logam, dan tidak ada satu pun kelas aset di luar keduanya.

Yang bisa dijawab jujur karena itu lebih sempit, dan namanya harus menyebut
kesempitannya: **berapa banyak aset kripto yang sedang berada di rezim yang
sama**. Menyebutnya "risk-on environment" akan mengklaim pengamatan lintas
kelas aset yang tidak pernah dilakukan - dan pembacanya tidak punya cara untuk
mengetahui bedanya.

Satu batas lagi, dieja pasalnya sendiri: cross-asset context **tidak boleh
menjadi keputusan tunggal**. Modul ini karena itu tidak memulangkan arah, dan
ada test yang gagal kalau suatu saat ia punya.

Dan satu yang tidak dieja pasalnya tapi terukur: BTC, ETH, dan SOL berkorelasi
0,83-0,88 (tabel ``correlations``, disegarkan tiap jam sejak Phase 14). Delapan
aset yang "sejalan" karena itu bukan delapan bukti yang berdiri sendiri -
sebagian besar adalah satu gerakan yang dihitung berkali-kali. Itu sebabnya
yang dilaporkan hanya jumlah dan persennya, bukan keyakinan yang diturunkan
darinya.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from aruna.memory.dimensions import sama

#: Berapa persen aset harus serezim sebelum keadaannya layak disebut sejalan.
#:
#: Di bawah setengah bukan "sejalan" - itu terbelah, dan menyebut mayoritas
#: tipis sebagai konteks yang mendukung adalah membaca derau.
AMBANG_SEJALAN = 60

#: Aset minimum sebelum sesuatu boleh disebut konteks **lintas** aset.
#:
#: Tiga, dan bukan dua: konteks lintas aset yang dihitung dari satu aset adalah
#: konteks aset itu sendiri dengan nama yang lebih meyakinkan, dan dari dua ia
#: satu pasangan - yang tabel korelasi justru sebut sebagai satu posisi.
MINIMUM_ASET = 3


@dataclass(frozen=True, slots=True)
class LintasAset:
    """Berapa banyak aset kripto yang serezim dengan yang sedang dinilai.

    Tidak punya bidang arah, dan tidak akan pernah punya - PASAL 15.18.
    """

    sejalan: int
    total: int
    rezim: str

    @property
    def luas(self) -> bool:
        """Cukup banyak aset untuk disebut konteks lintas aset."""
        return self.total >= MINIMUM_ASET

    @property
    def pct(self) -> int | None:
        """Persen yang serezim, atau ``None`` kalau tidak ada yang terbaca."""
        if not self.total:
            return None
        return round(self.sejalan * 100 / self.total)

    @property
    def sejalan_luas(self) -> bool:
        pct = self.pct
        return bool(self.luas and pct is not None and pct >= AMBANG_SEJALAN)

    def ringkas(self) -> str:
        """Satu baris untuk operator, atau kosong kalau tidak layak disebut.

        Menyebut **kripto** dengan sengaja: ARUNA tidak mengamati DXY maupun
        emas, dan "pasar" tanpa keterangan akan terbaca lebih luas daripada
        yang benar-benar dilihat.
        """
        if not self.luas or self.pct is None:
            return ""
        return (
            f"{self.sejalan}/{self.total} aset kripto berada di rezim "
            f"{self.rezim} ({self.pct}%)"
        )


def baca_lintas(
    baris: Sequence[dict[str, Any]], *, rezim_sekarang: object
) -> LintasAset:
    """Konteks lintas aset dari ingatan terbaru tiap simbol.

    ``UNKNOWN`` tidak pernah dihitung sejalan - aturan yang sama dengan sidik
    jarinya, dan alasan yang sama: dua ketiadaan bukan dua kecocokan.
    """
    dilihat: dict[str, str] = {}
    for r in baris:
        simbol = str(r.get("symbol") or "")
        if simbol and simbol not in dilihat:
            dilihat[simbol] = str(r.get("regime") or "")

    sejalan = sum(1 for rezim in dilihat.values() if sama(rezim, rezim_sekarang))
    return LintasAset(
        sejalan=sejalan,
        total=len(dilihat),
        rezim=str(getattr(rezim_sekarang, "value", rezim_sekarang) or ""),
    )


__all__ = ["AMBANG_SEJALAN", "MINIMUM_ASET", "LintasAset", "baca_lintas"]
