"""Siapa yang melabeli sebuah baris performa - turunan, atau router.

**Ini modul terpenting Phase 17**, dan yang paling mudah dikira sepele.

Bagian 17.37 minta performa strategi per rezim. Angka itu SUDAH tersimpan di
``strategy_performance`` dengan ``dimensions = {"regime": ...}`` - dan angka itu
**melingkar**.

Sebabnya struktural, bukan bug data. :func:`~aruna.learning.strategies.classify`
menurunkan strategi DARI rezim, jadi sebuah strategi hanya pernah dilabeli pada
rezim yang ada di ``preferred_regimes``-nya sendiri. Untuk strategi yang
preferensinya TUNGGAL - dan sebagian besar begitu - ``regime=X`` dan
``regime=ALL`` adalah himpunan baris yang sama persis. Terukur di produksi
2026-08-23::

    STR-005  regime=ALL       188W / 726L
    STR-005  regime=TRENDING  188W / 726L
    STR-002  regime=ALL       546W / 1605L
    STR-002  regime=BREAKOUT  546W / 1605L

Identik, karena keduanya himpunan baris yang sama. Router yang memeringkat
memakai angka itu memeringkat satu kandidat melawan dirinya sendiri.

**Melingkarnya SEBAGIAN, bukan total**, dan bedanya penting. ``STR-004``
menyukai ``RANGING`` DAN ``LOW_VOLATILITY``, jadi ia benar-benar terlabeli di
dua rezim dan slice per-rezimnya sudah berarti hari ini. Yang melingkar adalah
strategi berpreferensi tunggal - dan itu yang terukur di produksi.

**Yang mengunci bukan katalognya.** ``preferred_regimes`` sudah sanggup
multi-nilai; yang tidak pernah terjadi adalah sebuah strategi dipakai di rezim
DI LUAR preferensinya, karena tidak ada yang pernah memilihnya ke sana.

**Jalan keluarnya Phase 17 itu sendiri.** Begitu ROUTER yang memilih strategi -
bukan turunan dari rezim - sebuah strategi bisa terpakai di beberapa rezim, dan
pasangan (strategi, rezim) menjadi pengamatan yang sungguhan.

Harganya jujur, dan modul ini yang menagihnya: **baris lama tidak bisa
diselamatkan.** Tidak ada cara membedakan "STR-005 dipilih saat TRENDING" dari
"STR-005 ADALAH nama untuk TRENDING", jadi keduanya tidak boleh dijumlahkan -
hasilnya bukan milik salah satu. Sampai baris berlabel router cukup banyak,
:func:`performa_rezim` memulangkan ``None``, dan router memeringkat tanpa bukti
performa. Itu perilaku yang benar, bukan kekurangan yang perlu ditutupi.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = [
    "VERSI_ROUTER",
    "SlicePerforma",
    "dilabeli_router",
    "performa_rezim",
]


#: Penanda baris performa yang label rezimnya berasal dari PILIHAN router.
#:
#: Baris yang lebih tua dilabeli :func:`~aruna.learning.strategies.classify`,
#: yang menurunkan strategi dari rezim - jadi (strategi, rezim) di sana
#: melingkar dan tidak mengukur apa pun. Lihat catatan modul.
#:
#: Dicocokkan dengan awalan, bukan persis: ``router-1.2`` tetap baris router.
#: Versi yang naik bukan sumber label yang berbeda, dan menuntut kecocokan
#: persis akan membuang seluruh sejarah tiap kali parameternya disetel.
VERSI_ROUTER = "router-1"


@dataclass(frozen=True, slots=True)
class SlicePerforma:
    """Performa satu strategi pada satu rezim, sesudah lolos gerbang sampel."""

    win_rate: float
    sample_size: int


def dilabeli_router(row: Any) -> bool:
    """Apakah baris ini dilabeli router, bukan diturunkan dari rezim."""
    return str(row.get("model_version") or "").startswith(VERSI_ROUTER)


def _dimensi(row: Any) -> dict[str, Any]:
    """``dimensions`` sebagai dict, apa pun bentuk yang datang.

    MySQL memulangkan kolom JSON sebagai ``str`` lewat asyncmy. Baris yang
    tidak terurai akan diam-diam tidak pernah cocok, dan slice per rezim akan
    selamanya ``None`` **tanpa satu pun galat** - bentuk kegagalan yang paling
    sulit ditemukan.
    """
    nilai = row.get("dimensions")
    if isinstance(nilai, str):
        try:
            nilai = json.loads(nilai)
        except ValueError:
            return {}
    return nilai if isinstance(nilai, dict) else {}


def performa_rezim(
    rows: Any, *, kode: str, regime: str, minimum: int
) -> SlicePerforma | None:
    """Performa satu strategi pada satu rezim, atau ``None``.

    ``None`` berarti **belum bisa dijawab** - bukan nol, dan bukan buruk.
    Pemanggil yang menyamakannya dengan nol akan membuat setiap strategi baru
    terlihat gagal sejak hari pertama, dan setiap rezim yang jarang muncul
    terlihat sebagai rezim tempat semua strategi kalah.

    Baris turunan disaring habis, bukan dipakai sebagai cadangan. Ia punya
    ratusan sampel - jauh di atas ambang mana pun - jadi membiarkannya lolos
    berarti gerbang sampelnya tidak pernah menggigit.
    """
    cocok = [
        r
        for r in rows
        if r.get("strategy_code") == kode
        and _dimensi(r).get("regime") == regime
        and dilabeli_router(r)
    ]
    n = sum(int(r.get("sample_size") or 0) for r in cocok)
    if n < minimum:
        return None
    menang = sum(int(r.get("wins") or 0) for r in cocok)
    return SlicePerforma(win_rate=menang / n, sample_size=n)
