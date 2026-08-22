"""Frasa yang DILARANG diucapkan ARUNA (SPEC 51, FUTURES SPEC 51).

Daftar ini dulu tinggal di :mod:`aruna.futures.plan`, seolah-olah larangannya
hanya berlaku untuk futures. Tidak. SPEC 51 melarang **ARUNA** mengucapkannya,
di jalur mana pun - jadi lapisan agent harus bisa memakainya tanpa ikut menarik
seluruh modul futures.

Ada dua jenis teks yang sampai ke operator, dan keduanya butuh perlakuan
berbeda:

* **Teks tulisan ARUNA sendiri.** Kalau ini memuat frasa terlarang, itu bug -
  dan yang benar adalah menolak mengirim, keras-keras. :func:`find_forbidden`
  dipakai oleh guard di jalur render.

* **Teks kutipan dari luar** - judul berita RSS, misalnya. ARUNA tidak
  menulisnya dan tidak bisa mengatur isinya. Menolak mengirim seluruh pesan
  gara-gara sebuah judul berita berbunyi "guaranteed profit" berarti satu
  headline dari internet bisa membungkam log perdebatan, diam-diam. Untuk ini
  :func:`mask_forbidden` menyensor frasanya saja dan **menandai** bahwa ada
  yang disensor, sehingga ARUNA tidak pernah mengucapkannya, pesannya tetap
  sampai, dan pembaca tahu ada yang dihilangkan.

Menyensor tanpa menandai akan lebih buruk daripada keduanya: itu memalsukan
kutipan.
"""

from __future__ import annotations

import re

FORBIDDEN_CLAIMS: tuple[str, ...] = (
    "100% win",
    "100% profit",
    "pasti profit",
    "pasti untung",
    "pasti naik",
    "pasti turun",
    "pasti berhasil",
    "dijamin profit",
    "guaranteed profit",
    "guaranteed win",
    "risk free",
    "risk-free",
    "no risk",
    "tanpa risiko",
    "leverage aman",
    "safe leverage",
    "sure thing",
    "can't lose",
    "cannot lose",
)

#: Pengganti yang terlihat. Sengaja bukan penghapusan diam-diam: pembaca harus
#: tahu bahwa kutipannya dipotong, bukan mengira itu bunyi aslinya.
MASK = "[klaim disensor]"

_PATTERN = re.compile(
    "|".join(re.escape(claim) for claim in FORBIDDEN_CLAIMS), re.IGNORECASE
)


def find_forbidden(text: str) -> tuple[str, ...]:
    """Frasa terlarang yang muncul di ``text``, apa adanya (huruf kecil)."""
    lowered = text.lower()
    return tuple(claim for claim in FORBIDDEN_CLAIMS if claim in lowered)


def mask_forbidden(text: str) -> str:
    """Sensor frasa terlarang di dalam teks kutipan dari luar.

    Dipakai HANYA untuk teks yang bukan tulisan ARUNA. Untuk kalimat yang
    ditulis ARUNA sendiri, penyensoran justru menyembunyikan bug - pakai
    :func:`find_forbidden` dan tolak kirim.
    """
    return _PATTERN.sub(MASK, text)


__all__ = ["FORBIDDEN_CLAIMS", "MASK", "find_forbidden", "mask_forbidden"]
