"""Siapa yang boleh dipertimbangkan sama sekali (bagian 17.13).

**Disaring di hulu, bukan di peringkat.** Kandidat yang tidak layak dan
kebetulan berskor tinggi akan tercetak di log sebagai "hampir terpilih", dan
pembaca laporan tidak punya cara membedakannya dari kandidat yang benar-benar
kalah bersaing.

Kosakata statusnya
==================

Rencana Phase 17 menulis ``StrategyStatus.DISABLED``. **Nilai itu tidak pernah
ada.** Yang ada lima: ``ACTIVE``, ``DEGRADED``, ``UNDER_REVIEW``,
``SUSPENDED``, ``RETIRED`` - dan artinya diambil dari kata-kata katalognya
sendiri di produksi, bukan dikarang. Diperiksa 2026-08-23::

    STR-002  UNDER_REVIEW  lebih buruk dari rata-rata pada 1043 sample; cukup
                           diukur untuk pantas dipertimbangkan dihentikan
    STR-005  UNDER_REVIEW  lebih buruk dari rata-rata pada 213 sample; ...
    STR-000  UNDER_REVIEW  penampung, bukan strategi yang dipilih siapa pun

"Sedang ditimbang" bukan "dimatikan", dan bedanya menentukan **dua kali**.

Membuang ``UNDER_REVIEW`` sepenuhnya akan membuat ``BREAKOUT`` - rezim
TERBANYAK, 2.254 dari 9.437 bacaan 15m dalam tujuh hari - tidak punya satu pun
kandidat selamanya, karena ``STR-002`` dan ``STR-005`` yang menutupinya
keduanya berstatus itu.

Tapi alasan statusnya juga tidak boleh diabaikan: keduanya diukur lebih buruk
dari rata-rata. Menjadikannya champion berarti memimpin dengan strategi yang
sudah terbukti kalah.

Jalan tengahnya bukan kompromi melainkan justru apa yang slot challenger ada
untuk itu (bagian 17.18): **boleh menantang, tidak boleh memimpin.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aruna.learning.strategies import StrategyStatus

__all__ = [
    "KandidatLayak",
    "kandidat_layak",
]


#: Status yang boleh memimpin.
#:
#: Hanya satu, dan sengaja dieja sebagai himpunan alih-alih perbandingan
#: ``is ACTIVE``: status baru yang suatu hari ditambahkan ke enum akan jatuh ke
#: TIDAK BOLEH secara bawaan, dan itu arah kegagalan yang benar.
_BOLEH_MEMIMPIN = frozenset({StrategyStatus.ACTIVE})

#: Status yang boleh menantang tapi tidak memimpin.
#:
#: "Sedang ditimbang", bukan "dimatikan". Lihat catatan modul untuk angka yang
#: menuntut pembedaan ini.
_BOLEH_MENANTANG = frozenset({
    StrategyStatus.ACTIVE,
    StrategyStatus.DEGRADED,
    StrategyStatus.UNDER_REVIEW,
})

#: Kode yang tidak pernah boleh dipilih, apa pun statusnya.
#:
#: ``STR-000`` menyatakan dirinya sendiri di ``status_reason``: "penampung,
#: bukan strategi yang dipilih siapa pun; besarnya mengukur kelengkapan
#: katalog". Memilihnya berarti melaporkan KETIADAAN strategi sebagai sebuah
#: strategi - dan angka yang terbit darinya akan mengukur kelengkapan katalog,
#: bukan pasar.
_BUKAN_STRATEGI = frozenset({"STR-000"})


@dataclass(frozen=True, slots=True)
class KandidatLayak:
    """Dua daftar, karena ada dua pertanyaan.

    ``challenger`` memuat ``champion`` seluruhnya: apa pun yang boleh memimpin
    tentu boleh menantang. Memisahkannya menjadi dua himpunan yang saling lepas
    akan membuat strategi terbaik hilang dari slot challenger ketika ia sudah
    memimpin - dan slot itu menjadi kosong justru saat kandidatnya paling
    banyak.
    """

    champion: tuple[Any, ...]
    challenger: tuple[Any, ...]


def kandidat_layak(strategi: tuple[Any, ...]) -> KandidatLayak:
    """Pisahkan katalog menjadi yang boleh memimpin dan yang boleh menantang."""
    hidup = tuple(s for s in strategi if s.code not in _BUKAN_STRATEGI)
    return KandidatLayak(
        champion=tuple(s for s in hidup if s.status in _BOLEH_MEMIMPIN),
        challenger=tuple(s for s in hidup if s.status in _BOLEH_MENANTANG),
    )
