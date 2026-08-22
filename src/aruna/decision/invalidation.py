"""Kapan sebuah signal berhenti berlaku (PASAL 14.21).

*"Setiap signal wajib memiliki invalidation condition."* Kalimat itu menutup
kegagalan yang paling mahal di sistem seperti ini: signal yang tesisnya sudah
runtuh tapi tidak pernah dinyatakan runtuh. Ia tetap terlihat aktif, tetap
dihitung sebagai pendapat ARUNA yang berlaku, dan operator yang membacanya
tidak punya cara tahu bahwa alasan di baliknya sudah hilang.

**Invalidasi harus bisa diperiksa mesin, bukan hanya dibaca manusia.** PASAL
14.21 menyebut dua bentuk - level harga dan kalimat seperti *"bullish structure
breaks"* - dan modul ini mewajibkan **paling sedikit satu** yang berbentuk
level. Alasannya ada di kalimat penutup pasal itu sendiri: *"ARUNA harus
berhenti menganggap signal lama valid."* Sebuah invalidasi yang hanya berupa
kalimat tidak bisa membuat ARUNA berhenti apa pun; ia catatan, bukan syarat.
Kalimatnya tetap dibawa - ia menjelaskan *kenapa* levelnya dipilih - tapi ia
mendampingi, bukan menggantikan.

**Yang memicu adalah penutupan, bukan sundutan.** *"15m candle closes below
63,780"*. Sebuah invalidasi yang berbunyi pada wick akan berbunyi pada hampir
setiap gerakan berisik, dan invalidasi yang terlalu sering berbunyi berhenti
dibedakan dari derau. Bar yang belum tutup tidak memicu apa pun.

**Data yang tidak ada bukan berarti aman.** Kalau bar 15m belum tersedia,
jawabannya "belum bisa diperiksa" - bukan "masih berlaku". Ketiadaan bukti
kehancuran bukan bukti ketiadaan kehancuran, dan lapisan yang mencampur
keduanya akan melaporkan signal mati sebagai signal hidup setiap kali feed-nya
tersendat.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from aruna.decision.score import Arah


class Bar(Protocol):
    """Sedikit yang dibutuhkan dari sebuah lilin.

    Protokol, bukan impor :class:`aruna.data.models.Candle`, supaya lapisan
    keputusan tidak menarik seluruh model data hanya untuk membaca dua atribut -
    dan supaya test bisa menyusun kasus batas tanpa membangun bar lengkap.
    """

    @property
    def close(self) -> Decimal: ...

    @property
    def is_closed(self) -> bool: ...


class Sisi(StrEnum):
    """Arah penembusan yang mematikan tesisnya."""

    BELOW = "tutup di bawah"
    ABOVE = "tutup di atas"

    @property
    def opposite(self) -> Sisi:
        return Sisi.ABOVE if self is Sisi.BELOW else Sisi.BELOW


#: Sisi yang mematikan tiap arah.
#:
#: LONG runtuh ketika harga tutup DI BAWAH levelnya; SHORT ketika tutup di
#: atas. Ditulis sebagai peta supaya salah tanda menjadi kesalahan yang
#: terlihat - dan salah tanda di sini berarti ARUNA tidak pernah membatalkan
#: signal yang sedang salah, yang justru satu-satunya signal yang perlu
#: dibatalkan.
SISI_MEMATIKAN: dict[Arah, Sisi] = {
    Arah.LONG: Sisi.BELOW,
    Arah.SHORT: Sisi.ABOVE,
}


class InvalidationError(ValueError):
    """Syarat pembatalan yang tidak bisa dipakai."""


@dataclass(frozen=True, slots=True)
class Ambang:
    """Satu level yang, kalau ditembus penutupan, membatalkan signalnya."""

    interval: str
    side: Sisi
    price: Decimal

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise InvalidationError(f"level pembatalan tidak masuk akal: {self.price}")
        if not self.interval.strip():
            raise InvalidationError("level pembatalan tanpa timeframe")

    def triggered(self, bar: Bar) -> bool:
        """Apakah bar ini membatalkannya.

        ``is_closed`` menentukan, bukan hiasan. Bar berjalan masih berubah
        sesudah dibaca; membatalkan signal atas harga yang belum final adalah
        membatalkannya atas angka yang mungkin tidak pernah terjadi.
        """
        if not bar.is_closed:
            return False
        if self.side is Sisi.BELOW:
            return bar.close < self.price
        return bar.close > self.price

    def line(self) -> str:
        return f"{self.interval} {self.side.value} {self.price:,}"


@dataclass(frozen=True, slots=True)
class Periksa:
    """Hasil satu pemeriksaan pembatalan."""

    #: Level yang ditembus, kalau ada.
    hit: Ambang | None = None
    #: Timeframe yang levelnya ada tapi barnya tidak tersedia atau belum tutup.
    unchecked: tuple[str, ...] = field(default_factory=tuple)

    @property
    def invalidated(self) -> bool:
        return self.hit is not None

    @property
    def conclusive(self) -> bool:
        """Apakah jawabannya sudah final.

        Dipisahkan dari :attr:`invalidated` dengan sengaja. "Tidak dibatalkan"
        dan "belum bisa diperiksa" menuntut tindakan yang berbeda, dan
        menyatukannya membuat feed yang tersendat terbaca sebagai kabar baik.

        Sebuah level yang tertembus **adalah** kesimpulan, berapa pun level
        lain yang belum bisa diperiksa: tesisnya sudah runtuh, dan memeriksa
        sisanya tidak bisa menghidupkannya kembali.
        """
        return self.hit is not None or not self.unchecked

    def line(self) -> str:
        if self.hit is not None:
            return f"SIGNAL INVALIDATED - {self.hit.line()}"
        if self.unchecked:
            return (
                "belum bisa diperiksa: tidak ada bar tertutup untuk "
                + ", ".join(self.unchecked)
            )
        return "syarat pembatalan belum terjadi"


@dataclass(frozen=True, slots=True)
class Invalidasi:
    """Syarat pembatalan lengkap milik satu signal."""

    decision: Arah
    levels: tuple[Ambang, ...]
    #: Kalimat pendamping - "struktur bullish patah". Menjelaskan kenapa
    #: levelnya dipilih; tidak pernah berdiri sendiri.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.decision is Arah.NO_SIGNAL:
            raise InvalidationError(
                "NO SIGNAL tidak punya tesis yang bisa runtuh"
            )
        if not self.levels:
            raise InvalidationError(
                "signal tanpa level pembatalan tidak akan pernah dibatalkan "
                "(PASAL 14.21)"
            )
        mematikan = SISI_MEMATIKAN[self.decision]
        salah = [a for a in self.levels if a.side is not mematikan]
        if salah:
            raise InvalidationError(
                f"{self.decision.value} dibatalkan oleh {mematikan.value}, "
                f"bukan {salah[0].side.value} - tanda terbalik membuat signal "
                f"yang sedang salah tidak pernah dibatalkan"
            )

    def check(self, bars: Mapping[str, Bar]) -> Periksa:
        """Periksa seluruh level terhadap bar yang tersedia.

        Yang pertama tertembus menghentikan pemeriksaan - satu level cukup
        untuk membatalkan, dan mencari sisanya tidak mengubah apa pun.
        """
        belum: list[str] = []
        for a in self.levels:
            bar = bars.get(a.interval)
            if bar is None or not bar.is_closed:
                belum.append(a.interval)
                continue
            if a.triggered(bar):
                return Periksa(hit=a)
        return Periksa(unchecked=tuple(belum))

    def report(self) -> list[str]:
        """Blok INVALIDATION (PASAL 14.26), sebagai baris."""
        baris = ["⚠️ INVALIDATION", ""]
        baris += [f"  {a.line()}" for a in self.levels]
        if self.notes:
            baris += ["", "  Atau kalau:"]
            baris += [f"    {n}" for n in self.notes]
        return baris


def against_entry(inval: Invalidasi, entry: Decimal) -> None:
    """Tolak level yang sudah tertembus sebelum signalnya terbit.

    Sebuah LONG dengan pembatalan di ATAS entry-nya batal seketika: syaratnya
    sudah terpenuhi pada harga yang dipakai menghitungnya. Signal seperti itu
    terbit dan mati dalam satu tarikan napas, dan yang terlihat di layar
    operator hanyalah signal yang berumur pendek tanpa sebab.

    Terpisah dari ``__post_init__`` karena entry tidak selalu diketahui pada
    saat syaratnya disusun - dan syarat yang tidak bisa dibangun tanpa entry
    akan memaksa urutan penyusunan yang tidak selalu mungkin.
    """
    for a in inval.levels:
        if a.side is Sisi.BELOW and a.price >= entry:
            raise InvalidationError(
                f"pembatalan {a.price:,} tidak di bawah entry {entry:,} - "
                f"signal ini batal sejak lahir"
            )
        if a.side is Sisi.ABOVE and a.price <= entry:
            raise InvalidationError(
                f"pembatalan {a.price:,} tidak di atas entry {entry:,} - "
                f"signal ini batal sejak lahir"
            )


__all__ = [
    "SISI_MEMATIKAN",
    "Ambang",
    "Bar",
    "Invalidasi",
    "InvalidationError",
    "Periksa",
    "Sisi",
    "against_entry",
]
