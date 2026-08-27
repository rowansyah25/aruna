"""Kabar lanjutan atas sinyal XAU yang masih berjalan.

Sebuah sinyal yang terbit lalu didiamkan sampai horizonnya habis membuat
operator menunggu empat jam tanpa tahu apakah gagasannya masih hidup.  Modul
ini mengabarkan perubahan keadaan - dan hanya perubahan.

**Hanya saat keadaan BERGANTI.**  XAU menick tiap lima menit; mengabarkan tiap
tick berarti empat puluh delapan pesan untuk satu gagasan, dan yang penting
akan tenggelam di antaranya.  Yang dikirim adalah transisi, bukan denyut.

**Pembatalan dini adalah kejujuran, bukan kelemahan.**  Sebuah sinyal berdiri
di atas satu alasan: level struktur yang jadi targetnya.  Kalau level itu
hilang dari pembacaan struktur yang baru, alasannya hilang - dan menunggu stop
tersentuh hanya untuk "menyelesaikan" gagasan yang sudah batal adalah menahan
kerugian demi terlihat konsisten.  ARUNA mengatakannya apa adanya, termasuk
bahwa ia salah membaca.

**Tetap ANALIS.**  "Sebaiknya ditutup" adalah pembacaan bahwa gagasannya batal,
bukan perintah eksekusi.  ARUNA tidak menempatkan order, tidak menghitung
ukuran posisi, dan tidak menyentuh dana - operator yang memutuskan.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from aruna.analysis.structure import StructureReport
from aruna.core.enums import Decision

#: Sisa jarak ke target/stop yang dianggap "sudah dekat", dalam ATR.
#:
#: Setengah ATR: di bawah itu satu bar khas sudah cukup menyentuhnya, jadi
#: kabarnya datang saat masih ada waktu untuk berarti - bukan sesudah harganya
#: lewat.
AMBANG_DEKAT_ATR = Decimal("0.5")

#: Sisa bar horizon yang dianggap "hampir habis".
SISA_HAMPIR_HABIS = 6

#: Seberapa dekat level struktur harus ke target lama untuk dianggap MASIH
#: level yang sama.  Level bergeser sedikit tiap bar karena swing baru masuk
#: hitungan; menuntut kecocokan persis akan membatalkan tiap sinyal dalam satu
#: tick.
TOLERANSI_LEVEL_ATR = Decimal("0.75")


class Keadaan(StrEnum):
    """Keadaan sebuah sinyal yang masih berjalan."""

    BERJALAN = "BERJALAN"
    MENDEKAT_TARGET = "MENDEKAT_TARGET"
    MENDEKAT_STOP = "MENDEKAT_STOP"
    #: Alasan yang melahirkan sinyal ini sudah tidak ada.
    TESIS_BATAL = "TESIS_BATAL"
    HAMPIR_HABIS = "HAMPIR_HABIS"


@dataclass(frozen=True, slots=True)
class Kabar:
    keadaan: Keadaan
    alasan: str
    harga: Decimal
    sisa_bar: int
    #: Jarak ke target dan stop dalam ATR, supaya kabarnya bisa dibantah.
    ke_target_atr: Decimal | None = None
    ke_stop_atr: Decimal | None = None

    @property
    def perlu_dikabarkan(self) -> bool:
        """``BERJALAN`` adalah keadaan bawaan - tidak ada yang perlu dikatakan."""
        return self.keadaan is not Keadaan.BERJALAN

    @property
    def menyarankan_tutup(self) -> bool:
        return self.keadaan is Keadaan.TESIS_BATAL


def _level_masih_ada(
    struktur: StructureReport, target: Decimal, atr: Decimal, naik: bool
) -> bool:
    """Apakah level yang jadi target masih terbaca di struktur yang baru.

    Dibandingkan dengan toleransi, bukan persis: level bergeser sedikit tiap
    bar karena swing baru masuk hitungan, dan menuntut kecocokan persis akan
    membatalkan tiap sinyal dalam satu tick - yang berarti fitur ini tak pernah
    berguna sekali pun.
    """
    kandidat = struktur.resistance if naik else struktur.support
    if not kandidat:
        return False
    toleransi = atr * TOLERANSI_LEVEL_ATR
    return any(abs(Decimal(str(lvl.price)) - target) <= toleransi for lvl in kandidat)


def nilai_kabar(
    *,
    arah: Decision,
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
    atr: Decimal,
    harga: Decimal,
    struktur: StructureReport,
    sisa_bar: int,
) -> Kabar:
    """Baca keadaan satu sinyal yang masih berjalan.

    Urutannya disengaja: yang paling menentukan lebih dulu.  Sebuah tesis yang
    sudah batal tidak perlu dilaporkan sebagai "mendekati target".
    """
    if not arah.is_directional:
        raise ValueError(f"hanya sinyal berarah yang dikabari, bukan {arah.value}")

    naik = arah is Decision.BUY
    ke_target = (target - harga) if naik else (harga - target)
    ke_stop = (harga - stop) if naik else (stop - harga)
    ke_target_atr = ke_target / atr if atr else None
    ke_stop_atr = ke_stop / atr if atr else None

    dasar = {
        "harga": harga,
        "sisa_bar": sisa_bar,
        "ke_target_atr": ke_target_atr,
        "ke_stop_atr": ke_stop_atr,
    }

    # 1. Alasannya hilang. Ini yang paling menentukan - menunggu stop tersentuh
    #    demi "menyelesaikan" gagasan yang sudah batal adalah menahan kerugian
    #    supaya terlihat konsisten.
    if not _level_masih_ada(struktur, target, atr, naik):
        return Kabar(
            keadaan=Keadaan.TESIS_BATAL,
            alasan=(
                f"level {target:,.2f} yang jadi alasan sinyal ini sudah tidak "
                "terbaca lagi di struktur"
            ),
            **dasar,
        )

    if ke_stop_atr is not None and ke_stop_atr <= AMBANG_DEKAT_ATR:
        return Kabar(
            keadaan=Keadaan.MENDEKAT_STOP,
            alasan=f"tinggal {ke_stop_atr:.2f} ATR dari stop",
            **dasar,
        )

    if ke_target_atr is not None and ke_target_atr <= AMBANG_DEKAT_ATR:
        return Kabar(
            keadaan=Keadaan.MENDEKAT_TARGET,
            alasan=f"tinggal {ke_target_atr:.2f} ATR dari target",
            **dasar,
        )

    if sisa_bar <= SISA_HAMPIR_HABIS:
        return Kabar(
            keadaan=Keadaan.HAMPIR_HABIS,
            alasan=(
                f"sisa {sisa_bar} bar dan masih {ke_target_atr:.2f} ATR dari "
                "target"
                if ke_target_atr is not None
                else f"sisa {sisa_bar} bar"
            ),
            **dasar,
        )

    return Kabar(keadaan=Keadaan.BERJALAN, alasan="belum ada yang berubah", **dasar)


def susun_kabar(kabar: Kabar, *, arah: Decision, as_of: str) -> str:
    """Pesan untuk satu perubahan keadaan."""
    kepala = {
        Keadaan.MENDEKAT_TARGET: "XAU/USD — mendekati target",
        Keadaan.MENDEKAT_STOP: "XAU/USD — mendekati stop",
        Keadaan.TESIS_BATAL: "XAU/USD — gagasan ini BATAL",
        Keadaan.HAMPIR_HABIS: "XAU/USD — horizon hampir habis",
    }[kabar.keadaan]

    baris = [
        kepala,
        "",
        f"sinyal  {arah.value}",
        f"harga   {kabar.harga:,.2f}",
        f"alasan  {kabar.alasan}",
        f"sisa    {kabar.sisa_bar} bar M5",
    ]
    if kabar.ke_target_atr is not None:
        baris.append(f"jarak   {kabar.ke_target_atr:.2f} ATR ke target, "
                     f"{kabar.ke_stop_atr:.2f} ATR ke stop")

    if kabar.menyarankan_tutup:
        baris += [
            "",
            "Sinyal ini saya keluarkan karena level itu. Levelnya sudah tidak",
            "ada, jadi alasannya juga tidak ada — saya salah membacanya.",
            "Sebaiknya ditutup daripada menunggu stop, tapi itu keputusan Anda:",
            "ARUNA menganalisa saja dan tidak menempatkan order apa pun.",
        ]

    return "\n".join(baris)


__all__ = [
    "AMBANG_DEKAT_ATR",
    "SISA_HAMPIR_HABIS",
    "TOLERANSI_LEVEL_ATR",
    "Kabar",
    "Keadaan",
    "nilai_kabar",
    "susun_kabar",
]
