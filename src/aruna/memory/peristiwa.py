"""Konteks peristiwa (PASAL 15.15), dalam bentuk yang datanya sanggup dukung.

Pasalnya membayangkan EVENT -> Market Condition -> Reaction -> Outcome, dengan
contoh *"BTC reaction +4.2%, duration 35 minutes"*.

**Bentuk itu tidak bisa dibangun jujur dari data yang ada, dan itu terukur
2026-08-21.** ``news_events`` berisi 1.156 baris, tapi **750 di antaranya
bersentimen UNKNOWN**, hanya **158 berita** yang tertaut ke aset mana pun, dan
kategorinya IDX - ``BI_RATE``, ``RUPIAH``, ``EARNINGS``, ``MANAGEMENT`` -
sementara keputusan yang dinilai di sini kripto.

Sebuah tabel reaksi peristiwa yang dibangun dari bahan itu akan sebagian besar
berisi UNKNOWN, dan angka reaksi yang dihitung dari berita yang tidak tertaut
ke asetnya adalah angka yang dikarang (§13.26). PASAL 15.27 juga melarang
menyimpan ulang apa yang sudah tersimpan.

Yang **bisa** dijawab, dan sudah ada di ingatan: apa yang terjadi pada
keputusan yang dibuat ketika keadaan beritanya seperti sekarang. Terukur di
korpus produksi:

===========  ======  =====  =========
keadaan      menang  kalah  win rate
NEGATIVE     17      56     **23%**
POSITIVE     320     315    50%
NO_NEWS      927     1.262  42%
NEUTRAL      62      74     46%
===========  ======  =====  =========

Selisih dua puluh poin itu nyata dan sudah tersimpan sejak lama. Yang belum ada
adalah yang membacanya - dan itulah yang modul ini kerjakan.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from aruna.memory.dimensions import Dimensi, diketahui, sama
from aruna.memory.record import Hasil, Ingatan

#: Kasus minimum pada satu keadaan berita sebelum hasilnya boleh dibaca.
#:
#: Sama dengan ambang kasus serupa: lima kasus berita negatif bukan bukti
#: tentang berita negatif, betapa pun mencoloknya angkanya.
SAMPEL_PERISTIWA = 20

#: Arah yang benar-benar mempertaruhkan sesuatu. ``WAIT`` dan ``NO_SIGNAL``
#: tidak, dan menghitungnya akan menenggelamkan win rate yang sesungguhnya -
#: alasan yang sama persis seperti di :func:`aruna.memory.outcome.ringkas`.
_BERARAH = frozenset({"BUY", "LONG", "SELL", "SHORT"})


@dataclass(frozen=True, slots=True)
class Peristiwa:
    """Nasib keputusan lama pada satu keadaan berita.

    Tidak punya bidang arah - konteks peristiwa adalah bukti tambahan, dan
    PASAL 15.42 menyatakan keputusan final tetap milik Phase 14.
    """

    keadaan: str
    menang: int
    kalah: int

    @property
    def total(self) -> int:
        return self.menang + self.kalah

    @property
    def win_rate(self) -> int | None:
        if not self.total:
            return None
        return round(self.menang * 100 / self.total)

    def ringkas(self) -> str:
        """Satu baris untuk operator."""
        return (
            f"berita {self.keadaan}: {self.win_rate}% dari {self.total} "
            f"keputusan berarah"
        )


def baca_peristiwa(
    korpus: Sequence[Ingatan], *, keadaan: object
) -> Peristiwa | None:
    """Hasil historis pada keadaan berita ini, atau ``None``.

    ``None`` untuk tiga hal yang berbeda dan sama-sama sah: keadaannya tidak
    terbaca, korpusnya kosong, atau kasusnya terlalu sedikit. Ketiganya berarti
    "tidak ada yang bisa dikatakan" - dan itu jawaban, bukan kegagalan
    (PASAL 15.37).

    ``UNKNOWN`` tidak pernah menjadi kelompok: mengelompokkan ketiadaan
    menghasilkan statistik tentang tidak ada yang tahu.
    """
    if not diketahui(keadaan):
        return None

    menang = kalah = 0
    for ingatan in korpus:
        if str(ingatan.arah).strip().upper() not in _BERARAH:
            continue
        if not sama(ingatan.sidik.nilai.get(Dimensi.NEWS), keadaan):
            continue
        if ingatan.hasil is Hasil.WIN:
            menang += 1
        elif ingatan.hasil is Hasil.LOSS:
            kalah += 1

    if menang + kalah < SAMPEL_PERISTIWA:
        return None
    return Peristiwa(
        keadaan=str(getattr(keadaan, "value", keadaan)),
        menang=menang,
        kalah=kalah,
    )


__all__ = ["SAMPEL_PERISTIWA", "Peristiwa", "baca_peristiwa"]
