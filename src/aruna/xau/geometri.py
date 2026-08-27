"""Entry, stop, dan target untuk satu keputusan XAU - beserta RR-nya.

**Stop dari volatilitas, target dari struktur.**  Keduanya menjawab pertanyaan
berbeda.  Stop bertanya "seberapa jauh harga bisa bergerak melawan sebelum
gagasan ini terbukti salah", dan itu pertanyaan tentang volatilitas - ATR
menjawabnya.  Target bertanya "ke mana harga masuk akal pergi", dan itu
pertanyaan tentang tempat: level yang sudah berkali-kali menahan harga.

**Kenapa target TIDAK dikarang dari kelipatan ATR.**  Kalau target ditulis
sebagai ``n x ATR`` dengan ``n`` yang dipilih supaya lulus, RR menjadi
konstanta - dan gerbang RR tidak akan pernah menyala sekali pun sambil terlihat
bekerja.  Gerbang yang selalu lolos lebih buruk daripada tidak ada gerbang,
karena laporan akan menyebutnya "lulus".  Diambil dari level yang benar-benar
disentuh, RR berubah tiap keadaan dan penolakannya berarti sesuatu.

**Tidak ada level di arah tujuan berarti tidak ada geometri.**  Ke mana harga
akan pergi tidak diketahui, dan itu berbeda dari diketahui-tapi-dekat.
Menambalnya dengan ATR akan mengarang sebuah target.

**Lantai dua ATR dipinjam dari futures, bukan kodenya.**  ``src/aruna/futures/``
tidak boleh disentuh, tapi pelajarannya berlaku sama di sini: satu ATR adalah
pergerakan khas, jadi menargetkan satu ATR berarti menargetkan hasil imbang
yang terukur paling buruk.  :data:`MIN_TARGET_ATR` tidak ditegakkan di sini -
``geometri`` melaporkan :attr:`Geometri.target_atr` dan gerbang keputusan yang
menolak, supaya angka penyebabnya ikut tersimpan bersama penolakannya.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from aruna.analysis.structure import Level
from aruna.core.enums import Decision
from aruna.xau.bukti import BuktiXau

#: Jarak stop dalam satuan ATR.
STOP_ATR = Decimal("1.5")

#: Lantai jarak target, dalam ATR.  Ditegakkan di gerbang, bukan di sini.
MIN_TARGET_ATR = Decimal("2.0")


@dataclass(frozen=True, slots=True)
class Geometri:
    """Tiga harga dan jarak di antaranya, semuanya terukur."""

    entry: Decimal
    stop: Decimal
    target: Decimal
    atr: Decimal
    #: Berapa kali level target disentuh harga - bukti kekuatannya, bukan hiasan.
    sentuhan_target: int

    @property
    def jarak_stop(self) -> Decimal:
        return abs(self.entry - self.stop)

    @property
    def jarak_target(self) -> Decimal:
        return abs(self.target - self.entry)

    @property
    def target_atr(self) -> Decimal:
        """Jarak target dalam satuan ATR.  Dibandingkan ke MIN_TARGET_ATR."""
        return self.jarak_target / self.atr

    @property
    def rr(self) -> float:
        return float(self.jarak_target / self.jarak_stop)


def _level_terdekat(levels: tuple[Level, ...], harga: Decimal, *, di_atas: bool) -> Level | None:
    """Level terdekat di satu sisi harga.

    Yang terdekat, bukan yang terjauh: target yang lebih jauh memberi RR lebih
    bagus di atas kertas dan lebih kecil kemungkinannya benar-benar tercapai.
    """
    batas = float(harga)
    sisi = [
        lvl for lvl in levels if (lvl.price > batas if di_atas else lvl.price < batas)
    ]
    if not sisi:
        return None
    return min(sisi, key=lambda lvl: abs(lvl.price - batas))


def rakit_geometri(
    bukti: BuktiXau, arah: Decision, harga: Decimal
) -> Geometri | None:
    """Geometri M5 untuk ``arah`` pada ``harga``.

    ``None`` saat ATR belum terukur atau tidak ada level struktur di arah
    tujuan - dua keadaan yang sama-sama berarti jaraknya TIDAK DIKETAHUI.
    """
    if not arah.is_directional:
        raise ValueError(
            f"arah harus BUY atau SELL untuk merakit geometri, bukan {arah.value}"
        )

    bacaan = bukti.m5.reading("atr")
    if bacaan is None or not bacaan.available or bacaan.value <= 0:
        return None
    atr = Decimal(str(bacaan.value))

    naik = arah is Decision.BUY
    struktur = bukti.m5.structure
    level = _level_terdekat(
        struktur.resistance if naik else struktur.support, harga, di_atas=naik
    )
    if level is None:
        return None

    jarak_stop = STOP_ATR * atr
    return Geometri(
        entry=harga,
        stop=harga - jarak_stop if naik else harga + jarak_stop,
        target=Decimal(str(level.price)),
        atr=atr,
        sentuhan_target=level.touches,
    )


__all__ = ["MIN_TARGET_ATR", "STOP_ATR", "Geometri", "rakit_geometri"]
