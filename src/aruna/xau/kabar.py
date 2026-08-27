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

#: Rezim yang secara langsung melawan arah sebuah sinyal.
#:
#: **Ditambahkan atas keputusan operator 2026-08-28**, sesudah tiga SELL kena
#: stop.  Pilihannya: memperketat perlakuan dekat-stop, atau membuat
#: pembatalan lebih peka.  Yang kedua dipilih, dan alasannya kuat - stop adalah
#: garis yang operator tetapkan sebagai "gagasan ini terbukti salah", dan
#: memindahkannya diam-diam membuat RR yang dijanjikan saat sinyal terbit tidak
#: lagi berlaku.
#:
#: Sebuah SELL yang pasarnya kini membaca TRENDING_BULLISH bukan sedang lambat;
#: premisnya sudah hilang.  ``RANGING`` dan ``UNCERTAIN`` sengaja TIDAK di
#: sini: keduanya berarti tidak terbaca, bukan terbaca-melawan, dan
#: membatalkan gagasan karena pasar sedang tidak jelas akan membatalkan hampir
#: semuanya - RANGING saja 30,8% dari korpus.
REGIME_MELAWAN: dict[str, frozenset[str]] = {
    "BUY": frozenset({"TRENDING_BEARISH", "BREAKDOWN"}),
    "SELL": frozenset({"TRENDING_BULLISH", "BREAKOUT"}),
}

#: Keyakinan minimum sebelum rezim yang melawan dianggap membatalkan.
#:
#: Diukur atas 396 jendela: keyakinan saat rezim TERBACA punya p10 = 0,511.
#: Ambang 0,5 karena itu menerima hampir seluruh bacaan yang sungguhan dan
#: menolak yang di ambang - sebuah rezim yang nyaris tak terbaca tidak cukup
#: kuat untuk membatalkan gagasan yang levelnya masih berdiri.
MIN_KEYAKINAN_BATAL = 0.5


class Keadaan(StrEnum):
    """Keadaan sebuah sinyal yang masih berjalan."""

    BERJALAN = "BERJALAN"
    MENDEKAT_TARGET = "MENDEKAT_TARGET"
    MENDEKAT_STOP = "MENDEKAT_STOP"
    #: Alasan yang melahirkan sinyal ini sudah tidak ada.
    TESIS_BATAL = "TESIS_BATAL"
    HAMPIR_HABIS = "HAMPIR_HABIS"
    #: Horizon empat jam lewat tanpa target maupun stop tersentuh.
    HORIZON_HABIS = "HORIZON_HABIS"


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


def _regime_melawan(regime: object | None, arah: Decision) -> str | None:
    """Nama rezim kalau ia melawan ``arah`` dengan keyakinan cukup.

    ``None`` berarti tidak melawan - termasuk saat rezimnya tidak terbaca sama
    sekali.  Rezim yang tidak terbaca BUKAN rezim yang melawan: membatalkan
    gagasan karena pasar sedang tidak jelas akan membatalkan hampir semuanya.
    """
    if regime is None:
        return None
    nama = getattr(getattr(regime, "regime", None), "value", None)
    if nama not in REGIME_MELAWAN.get(arah.value, frozenset()):
        return None
    keyakinan = getattr(regime, "confidence", 0.0) or 0.0
    return nama if keyakinan >= MIN_KEYAKINAN_BATAL else None


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
    regime: object | None = None,
) -> Kabar:
    """Baca keadaan satu sinyal yang masih berjalan.

    Urutannya disengaja: yang paling menentukan lebih dulu.  Sebuah tesis yang
    sudah batal tidak perlu dilaporkan sebagai "mendekati target".

    ``regime`` adalah pembacaan rezim M5 terbaru.  Rezim yang kini membaca
    LAWAN arah sinyal membatalkan gagasannya sama seperti level yang hilang -
    keduanya berarti alasannya tidak ada lagi, bukan bahwa ia lambat.
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

    # 1b. Pasar kini membaca LAWAN arah sinyal. Sebuah SELL di pasar yang
    #     TRENDING_BULLISH bukan sedang lambat - premisnya sudah hilang.
    melawan = _regime_melawan(regime, arah)
    if melawan is not None:
        return Kabar(
            keadaan=Keadaan.TESIS_BATAL,
            alasan=(
                f"rezim kini {melawan} - melawan arah {arah.value}, "
                "jadi premis sinyal ini sudah tidak berdiri"
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


@dataclass(frozen=True, slots=True)
class Penutup:
    """Putusan saat horizon habis tanpa target maupun stop tersentuh.

    Dua pilihan, dan keduanya harus punya dasar yang bisa dibantah - bukan
    selera.  Diamkan operator di titik ini dan ia memegang sesuatu tanpa
    keterangan apa pun, yang justru keadaan tempat kerugian paling sering
    dibiarkan tumbuh.
    """

    tahan: bool
    alasan: str
    arah_benar: bool | None
    gerak_pct: Decimal
    level_masih_ada: bool


def nilai_penutup(
    *,
    arah: Decision,
    target: Decimal,
    atr: Decimal,
    struktur: StructureReport,
    arah_benar: bool | None,
    gerak_pct: Decimal,
) -> Penutup:
    """Tahan atau tutup, saat horizon habis tanpa level tersentuh.

    Aturannya dua pertanyaan, dan urutannya menentukan:

    1. **Apakah alasannya masih ada?**  Level yang melahirkan sinyal ini.
       Kalau hilang, gagasannya tidak "lambat" - ia sudah tidak ada, dan
       menahannya berarti menunggu sesuatu yang tak lagi diramalkan siapa pun.
    2. **Apakah arahnya benar?**  Harga bergerak ke arah yang dipanggil, cuma
       belum sejauh targetnya.  Itu gagasan yang hidup dan lambat - berbeda
       dari gagasan yang salah.

    Arah yang salah menutup, apa pun levelnya: bertahan pada bacaan yang sudah
    terbukti meleset adalah menahan kerugian demi terlihat konsisten.
    """
    if not arah.is_directional:
        raise ValueError(f"hanya sinyal berarah yang punya penutup, bukan {arah.value}")

    naik = arah is Decision.BUY
    masih = _level_masih_ada(struktur, target, atr, naik)

    if not masih:
        return Penutup(
            tahan=False,
            alasan=(
                f"level {target:,.2f} yang jadi alasannya sudah tidak terbaca; "
                "gagasannya bukan lambat, ia sudah tidak ada"
            ),
            arah_benar=arah_benar,
            gerak_pct=gerak_pct,
            level_masih_ada=False,
        )

    if arah_benar is False:
        return Penutup(
            tahan=False,
            alasan=(
                f"harga bergerak {gerak_pct:+.2f}% - melawan panggilan, "
                "jadi bacaannya meleset"
            ),
            arah_benar=arah_benar,
            gerak_pct=gerak_pct,
            level_masih_ada=True,
        )

    if arah_benar is None:
        return Penutup(
            tahan=False,
            alasan="arah tidak terukur, jadi tidak ada dasar untuk menahan",
            arah_benar=None,
            gerak_pct=gerak_pct,
            level_masih_ada=True,
        )

    return Penutup(
        tahan=True,
        alasan=(
            f"harga bergerak {gerak_pct:+.2f}% searah panggilan dan levelnya "
            "masih terbaca - gagasannya hidup, cuma lebih lambat dari empat jam"
        ),
        arah_benar=True,
        gerak_pct=gerak_pct,
        level_masih_ada=True,
    )


def susun_penutup(
    penutup: Penutup,
    *,
    arah: Decision,
    entry: Decimal,
    harga_tutup: Decimal,
    target: Decimal,
) -> str:
    """Pesan saat horizon habis.  Selalu berisi putusan, tidak pernah diam."""
    putusan = "TAHAN DULU" if penutup.tahan else "SEBAIKNYA DITUTUP"
    baris = [
        "XAU/USD — horizon 4 jam habis, target tidak tercapai",
        "",
        f"sinyal   {arah.value}",
        f"entry    {entry:,.2f}",
        f"sekarang {harga_tutup:,.2f}  ({penutup.gerak_pct:+.2f}%)",
        f"target   {target:,.2f}  — tidak tersentuh",
        f"arah     {'benar' if penutup.arah_benar else 'meleset'}"
        if penutup.arah_benar is not None
        else "arah     tidak terukur",
        f"level    {'masih terbaca' if penutup.level_masih_ada else 'sudah hilang'}",
        "",
        f"→ {putusan}",
        f"  {penutup.alasan}",
    ]
    if not penutup.tahan and penutup.arah_benar is False:
        baris.append("  Saya salah membaca arahnya.")
    baris += [
        "",
        "Keputusan tetap di Anda. ARUNA menganalisa saja dan tidak",
        "menempatkan order apa pun.",
    ]
    return "\n".join(baris)


__all__ = [
    "AMBANG_DEKAT_ATR",
    "MIN_KEYAKINAN_BATAL",
    "REGIME_MELAWAN",
    "SISA_HAMPIR_HABIS",
    "TOLERANSI_LEVEL_ATR",
    "Kabar",
    "Keadaan",
    "Penutup",
    "nilai_kabar",
    "nilai_penutup",
    "susun_kabar",
    "susun_penutup",
]
