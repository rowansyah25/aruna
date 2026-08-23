"""Apa yang router putuskan, termasuk ketika ia menolak (bagian 17.29-17.30).

**Kemampuan memulangkan NONE yang paling dijaga di modul ini.** Bagian 17.29
melarang memaksa memilih strategi hanya supaya sistem menghasilkan arah, dan
angka nyatanya menuntutnya: win rate tertinggi di katalog sekarang **25,4%**
(STR-002, 2.151 sampel). Router yang selalu memilih seseorang akan memilih yang
kalah, lalu melaporkannya sebagai pilihan.

Dan penolakannya harus **menyebut sebabnya**. "Tidak ada strategi" tanpa alasan
tidak bisa dibantah, dan yang lebih buruk: nol karena tidak ada yang cocok dan
nol karena fasenya mati terlihat sama persis dari luar - yang pertama normal
sementara yang kedua bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from aruna.core.enums import Regime
from aruna.router.kecocokan import NETRAL, Kecocokan
from aruna.router.rezim import BOBOT_INTERVAL, PetaRezim

__all__ = [
    "AMBANG_KEYAKINAN_REZIM",
    "AMBANG_LAYAK",
    "AlasanKosong",
    "PutusanRouter",
    "pilih",
]


class AlasanKosong(StrEnum):
    """Kenapa tidak ada champion, dalam bentuk yang bisa DIHITUNG.

    **Kalimatnya saja tidak cukup, dan itu terbukti sebelum dikomit.** Versi
    pertama mengelompokkan penolakan dengan memotong kalimatnya, dan karena
    kalimat itu menyebut angkanya - "keyakinan rezim 20%", "keyakinan rezim
    32%" - tiap penolakan menjadi kelompoknya sendiri. Laporan "router menolak
    19 aset" dengan 19 kelompok berisi satu sama tak bergunanya dengan daftar
    mentah.

    Angkanya tetap ada di :attr:`PutusanRouter.alasan_kosong` untuk dibaca
    manusia; yang ini untuk dihitung mesin. Dua pembaca, dua bentuk - bukan
    satu string yang dipaksa melayani keduanya.
    """

    #: Belum ada bacaan sama sekali, atau seluruhnya ``UNCERTAIN``.
    REZIM_TAK_TERBACA = "REZIM_TAK_TERBACA"
    #: Terbaca, tapi buktinya terlalu tipis atau terlalu terbelah.
    KEYAKINAN_KURANG = "KEYAKINAN_KURANG"
    #: Rezimnya jelas; tidak ada strategi yang cukup cocok dengannya.
    TAK_ADA_YANG_COCOK = "TAK_ADA_YANG_COCOK"


#: Keyakinan rezim minimum sebelum strategi apa pun dipilih (bagian 17.30).
#:
#: **Bukan pinjaman.** Rencana Phase 17 menyuruh meminjam
#: :data:`~aruna.signals.quality.MIN_QUALITY` (60), dan itu ditolak justru oleh
#: Global Constraints rencana itu sendiri: ambang yang dipinjam harus dipinjam
#: dari **pertanyaan yang sama**. ``MIN_QUALITY`` menjawab "berapa skor minimum
#: agar sebuah kandidat SINYAL boleh terbit" - pertanyaan yang berbeda, dan
#: salah pinjam sudah tiga kali jadi bug di proyek ini.
#:
#: Yang dipakai diturunkan dari bentuk :attr:`PetaRezim.primary_confidence`
#: sendiri. Karena angka itu = cakupan x kesepakatan x keyakinan, "lebih dari
#: setengah bobot horizon mendukung primary" berarti tepat lima puluh. Dengan
#: bobot nyata (15m 1,0 / 1h 1,6 / 1d 2,4 dari total 5,0), akibatnya:
#:
#: * satu horizon sendirian - 20, 32, atau 48 - **ditolak**
#: * ketiganya berselisih, primary di 1d - 48 - **ditolak**
#: * dua horizon apa pun yang sepakat - 52 ke atas - **diterima**
#:
#: Itu klaim yang bisa dipertahankan, dan ia klaim bagian 17.8 apa adanya: satu
#: horizon pendek tidak boleh memutuskan sendirian.
AMBANG_KEYAKINAN_REZIM = round(
    100.0 * (sum(BOBOT_INTERVAL.values()) / 2) / sum(BOBOT_INTERVAL.values()), 1
)

#: Skor kecocokan minimum sebelum sebuah strategi boleh menjadi champion.
#:
#: **Kebijakan, bukan pengukuran**, dan ditulis begitu supaya tidak ada yang
#: mengutipnya sebagai temuan. Yang bisa dipertahankan: ia harus DI ATAS
#: :data:`~aruna.router.kecocokan.NETRAL`, karena netral adalah skor sebuah
#: strategi yang rezimnya tidak cocok maupun tidak bertentangan - dan memilih
#: atas dasar itu berarti memilih tanpa alasan.
AMBANG_LAYAK = NETRAL + 10

#: Bacaan yang bukan rezim, melainkan classifier yang mengaku tidak tahu.
#:
#: **19,7% bacaan, dan mendiamkannya adalah bug.** Terukur 2026-08-23: 1.860
#: dari 9.437 bacaan 15m dalam tujuh hari berlabel ``UNCERTAIN``.
#:
#: Kalau dipakai apa adanya ia rezim yang tidak ada di ``preferred_regimes``
#: siapa pun, jadi setiap strategi jatuh di bawah netral dan router menolak
#: dengan alasan yang SALAH - "skor tertinggi di bawah ambang" alih-alih
#: "rezimnya belum terbaca". Dua keadaan yang menuntut tindakan berbeda
#: dilaporkan sebagai satu.
#:
#: Prinsipnya sudah berlaku di Phase 16; lihat
#: :data:`~aruna.db.repositories.konteks_pemicu.TIDAK_TERBACA`.
_TIDAK_TERBACA = frozenset({str(Regime.UNCERTAIN)})


@dataclass(frozen=True, slots=True)
class PutusanRouter:
    """Pilihan router untuk satu aset pada satu titik waktu.

    ``champion`` terisi berarti ``alasan_kosong`` kosong, dan sebaliknya. Dua
    bidang yang bisa terisi bersamaan adalah dua sumber kebenaran yang bisa
    bertentangan, dan pembacanya harus menebak mana yang berlaku.
    """

    champion: Kecocokan | None
    challenger: Kecocokan | None
    #: Kosong berarti ada champion. Terisi berarti TIDAK ada, dan **sebabnya
    #: ada di sini** - "tidak ada strategi" tanpa alasan tidak bisa dibantah.
    #: Untuk dibaca manusia; menyebut angkanya.
    alasan_kosong: str = ""
    #: Sebab yang sama, dalam bentuk yang bisa DIHITUNG. ``None`` berarti ada
    #: champion. Lihat :class:`AlasanKosong` untuk kenapa keduanya ada.
    kode_kosong: AlasanKosong | None = None
    #: Rezim yang jadi dasar keputusan, ikut apa adanya supaya laporan tidak
    #: perlu menghitung ulang - dan supaya keputusan lama tetap bisa dibaca
    #: sesudah rezimnya berganti (bagian 17.27).
    regime: str | None = None
    alasan: tuple[str, ...] = field(default_factory=tuple)


def _kosong(sebab: str, kode: AlasanKosong, peta: PetaRezim) -> PutusanRouter:
    return PutusanRouter(None, None, sebab, kode, peta.primary)


def pilih(
    kandidat: tuple[Kecocokan, ...], *, peta: PetaRezim
) -> PutusanRouter:
    """Champion dan challenger, atau penolakan yang menyebut sebabnya.

    Tiga gerbang, dan **urutannya menentukan mutu alasannya**. Rezim yang
    belum terbaca diperiksa lebih dulu daripada skor kandidat: kalau tidak,
    setiap kandidat akan jatuh di bawah ambang - karena rezim yang tak terbaca
    tidak cocok dengan siapa pun - dan router menolak dengan alasan yang salah.
    """
    if peta.primary is None or peta.primary in _TIDAK_TERBACA:
        return _kosong(
            f"rezim belum terbaca ({peta.primary or 'tidak ada bacaan'})",
            AlasanKosong.REZIM_TAK_TERBACA,
            peta,
        )

    if peta.primary_confidence < AMBANG_KEYAKINAN_REZIM:
        return _kosong(
            f"keyakinan rezim {peta.primary_confidence:.0f}% di bawah ambang "
            f"{AMBANG_KEYAKINAN_REZIM:.0f}%",
            AlasanKosong.KEYAKINAN_KURANG,
            peta,
        )

    # Seri diputus menurut kode, bukan urutan masuk. Bersandar pada urutan
    # berarti champion berubah karena urutan baris yang kebetulan keluar dari
    # database - jawaban yang berbeda tanpa ada yang mengubah apa pun.
    layak = sorted(
        (k for k in kandidat if k.skor >= AMBANG_LAYAK),
        key=lambda k: (-k.skor, k.kode),
    )
    if not layak:
        tertinggi = max((k.skor for k in kandidat), default=0)
        return _kosong(
            f"skor tertinggi {tertinggi} di bawah ambang {AMBANG_LAYAK} "
            f"pada rezim {peta.primary}",
            AlasanKosong.TAK_ADA_YANG_COCOK,
            peta,
        )

    return PutusanRouter(
        champion=layak[0],
        challenger=layak[1] if len(layak) > 1 else None,
        alasan_kosong="",
        kode_kosong=None,
        regime=peta.primary,
        alasan=layak[0].alasan,
    )
