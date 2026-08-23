"""Kenapa pilihan berganti (bagian 17.26, 17.28).

Contoh operator 2026-08-23::

    TRENDING UP  ->  Trend Following dipilih
         |
    SIDEWAYS     ->  Trend Following TIDAK LAGI COCOK
                     Mean Reversion menjadi kandidat

**Perilakunya sudah ada sebelum modul ini.** Fase router membaca rezim dan
memilih ulang tiap siklus, jadi begitu rezimnya `RANGING`, `STR-001` memang
jatuh di bawah netral dan `STR-004` naik. Yang TIDAK ada adalah jejaknya:
sebuah baris dengan champion `STR-004` tidak menyebutkan bahwa champion
sebelumnya `STR-001`, apalagi kenapa ia gugur. Pembacanya harus membandingkan
dua baris dan menebak sebabnya.

Adaptasi yang tidak bisa dilihat tidak bisa dibuktikan terjadi, dan bagian
17.26 minta adaptasi.

**Modul ini tidak menilai ulang apa pun**, dan itu batas yang disengaja.
Menghitung sendiri "apakah STR-001 masih cocok" berarti aturan kecocokan kedua
yang harus selamanya sepakat dengan :func:`~aruna.router.kecocokan.nilai` -
dan dua aturan yang harus tetap sepakat sudah beberapa kali jadi bug di proyek
ini. Yang dilakukan di sini murni pembukuan atas apa yang sudah diputuskan:
membandingkan pilihan sekarang dengan yang tersimpan, lalu menamai bedanya.

**Sinyal yang SUDAH terbit tidak ikut berubah** (bagian 17.27). Rezim berganti
sesudah sebuah pilihan tercatat adalah hal biasa; yang berganti adalah pilihan
untuk sinyal BERIKUTNYA. Yang menjaganya bukan modul ini melainkan
``INSERT IGNORE`` di :class:`~aruna.db.repositories.router.RouterRepository`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aruna.router.putusan import PutusanRouter
from aruna.router.rezim import PetaRezim

__all__ = [
    "AlasanInvalid",
    "PilihanSebelumnya",
    "kenapa_berganti",
]


class AlasanInvalid(StrEnum):
    """Kenapa champion sebelumnya tidak lagi memimpin.

    Memulangkan SEBABNYA dan bukan sekadar "berganti": strategi yang gugur
    karena rezimnya berpindah dan strategi yang gugur karena statusnya diubah
    operator menuntut tindakan yang berbeda, dan keduanya terlihat sama kalau
    yang tercatat cuma "champion berubah".
    """

    #: Rezim primary-nya berpindah. Ini kasus contoh operator.
    REZIM_BERGANTI = "REZIM_BERGANTI"
    #: Strategi lamanya hilang dari kandidat yang boleh memimpin - statusnya
    #: turun, atau ia dicabut dari katalog.
    STATUS_BERUBAH = "STATUS_BERUBAH"
    #: Rezimnya sama dan strateginya masih layak; skornya saja yang bergeser.
    #: Paling sering karena stabilitas atau keyakinan rezim berubah.
    TIDAK_LAGI_TERBAIK = "TIDAK_LAGI_TERBAIK"


@dataclass(frozen=True, slots=True)
class PilihanSebelumnya:
    """Baris ``router_pilihan`` terakhir untuk satu aset, seperlunya saja."""

    champion: str | None
    regime: str | None


def kenapa_berganti(
    sebelum: PilihanSebelumnya | None,
    *,
    putusan: PutusanRouter,
    peta: PetaRezim,
    boleh_memimpin: frozenset[str],
) -> tuple[str, ...]:
    """Kalimat yang menerangkan peralihan, atau kosong kalau tidak ada.

    Kosong pada tiga keadaan yang berbeda dan semuanya benar: belum ada
    pilihan sebelumnya, championnya sama, atau dulu tidak ada champion dan
    sekarang pun tidak.

    ``boleh_memimpin`` adalah kode strategi yang saat ini boleh menjadi
    champion - dioper, tidak dihitung di sini. Modul ini tidak boleh punya
    pendapat sendiri tentang kelayakan; itu milik
    :func:`~aruna.router.peringkat.kandidat_layak`.
    """
    if sebelum is None or sebelum.champion is None:
        return ()

    sekarang = None if putusan.champion is None else putusan.champion.kode
    if sekarang == sebelum.champion:
        return ()

    sebab = _sebab(sebelum, peta=peta, boleh_memimpin=boleh_memimpin)
    penerus = sekarang or "tidak ada"
    return (
        f"{sebelum.champion} tidak lagi memimpin ({sebab}); sekarang {penerus}",
        *_rinci(sebab, sebelum, peta),
    )


def _sebab(
    sebelum: PilihanSebelumnya,
    *,
    peta: PetaRezim,
    boleh_memimpin: frozenset[str],
) -> AlasanInvalid:
    """Urutannya menentukan sebab mana yang dilaporkan.

    Status diperiksa lebih dulu daripada rezim: strategi yang statusnya
    diturunkan operator tidak lagi memimpin **apa pun rezimnya**, jadi
    melaporkan "rezim berganti" untuknya menyesatkan pembacanya ke arah yang
    salah - ia akan memeriksa pasar padahal yang berubah katalognya.
    """
    if sebelum.champion not in boleh_memimpin:
        return AlasanInvalid.STATUS_BERUBAH
    if peta.primary != sebelum.regime:
        return AlasanInvalid.REZIM_BERGANTI
    return AlasanInvalid.TIDAK_LAGI_TERBAIK


def _rinci(
    sebab: AlasanInvalid, sebelum: PilihanSebelumnya, peta: PetaRezim
) -> tuple[str, ...]:
    if sebab is not AlasanInvalid.REZIM_BERGANTI:
        return ()
    dari = sebelum.regime or "tidak terbaca"
    ke = peta.primary or "tidak terbaca"
    return (f"rezim berpindah {dari} -> {ke}",)
