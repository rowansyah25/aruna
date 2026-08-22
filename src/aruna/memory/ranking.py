"""Urutan dan bobot ingatan (PASAL 15.11, 15.21, 15.22).

**Peluruhan, bukan pemotongan.** PASAL 15.21 menyatakan HISTORICAL VALUE tidak
pernah nol: data lama tetap berguna untuk konteks jangka panjang, dan bobot nol
sama saja dengan menghapusnya - yang pasalnya larang secara terpisah. Peluruhan
eksponensial memberi keduanya sekaligus: yang baru jauh lebih berat, yang lama
tetap terdengar.

**Yang harus dieja, dan bukan cuma di rencana:** korpus ARUNA baru beberapa
hari (terukur 2026-08-17 s/d 08-20). Pada :data:`SETENGAH_UMUR_HARI` tiga puluh
hari, seluruh bobot kebaruan sekarang berada di antara 0,91 dan 1,00 - jadi
peluruhan **praktis tidak berpengaruh hari ini**.

Itu bukan alasan menghapusnya, dan bukan alasan mengecilkan setengah-umurnya
supaya angkanya terlihat bekerja. Sebuah parameter yang disetel agar
menghasilkan variasi pada korpus yang belum punya variasi adalah parameter yang
dipilih demi tampilannya.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from aruna.memory.record import Ingatan, Mutu
from aruna.memory.similarity import Kemiripan

#: Umur di mana sebuah ingatan bernilai setengah dari yang baru saja terjadi.
#:
#: Tiga puluh hari: cukup panjang untuk menampung satu siklus rezim pasar,
#: cukup pendek supaya ingatan setahun lalu tidak berdebat setara dengan
#: minggu ini. Bisa diubah pemanggil - lihat argumen ``setengah_umur``.
SETENGAH_UMUR_HARI = 30.0

#: Bobot mutu (PASAL 15.24). Low-quality memory tidak boleh berbobot tinggi -
#: dan tidak boleh nol juga, karena mutu rendah berarti "kurang bisa
#: dipercaya", bukan "tidak pernah terjadi".
_BOBOT_MUTU: dict[Mutu, float] = {
    Mutu.HIGH: 1.0,
    Mutu.MEDIUM: 0.6,
    Mutu.LOW: 0.3,
}


def bobot_kebaruan(
    umur_hari: float, *, setengah_umur: float = SETENGAH_UMUR_HARI
) -> float:
    """Bobot sebuah ingatan berdasarkan umurnya. Selalu di atas nol.

    Umur negatif dijepit ke nol: jam yang salah atau stempel waktu di masa
    depan menghasilkan pangkat negatif, dan itu memberi bobot **di atas satu** -
    sebuah ingatan yang lebih berharga daripada yang baru saja terjadi, semata
    karena jamnya rusak.
    """
    return 0.5 ** (max(umur_hari, 0.0) / setengah_umur)


def peringkat(
    cocok: Sequence[tuple[Ingatan, Kemiripan]], *, as_of: datetime
) -> list[tuple[Ingatan, Kemiripan, float]]:
    """Urutkan kasus serupa, paling relevan lebih dulu (PASAL 15.22).

    Urutannya: kemiripan lebih dulu, lalu bobot - dan bobot itu sendiri
    gabungan kebaruan dengan mutu. Kemiripan didahulukan dengan sengaja: sebuah
    ingatan yang sangat baru tapi kondisinya berbeda tidak menerangkan kondisi
    sekarang, betapa pun segarnya.

    ``as_of`` wajib, dan bukan kehati-hatian umum: umur dihitung terhadapnya,
    jadi backtest yang memakai "sekarang" akan menganggap seluruh ingatan lebih
    tua daripada yang sebenarnya pada saat keputusan itu dibuat.
    """
    berbobot: list[tuple[Ingatan, Kemiripan, float]] = []
    for ingatan, mirip in cocok:
        umur = (as_of - ingatan.locked_at).total_seconds() / 86400
        bobot = bobot_kebaruan(umur) * _BOBOT_MUTU.get(ingatan.mutu, 0.3)
        berbobot.append((ingatan, mirip, bobot))

    berbobot.sort(key=lambda b: (b[1].skor, b[2]), reverse=True)
    return berbobot


__all__ = ["SETENGAH_UMUR_HARI", "bobot_kebaruan", "peringkat"]
