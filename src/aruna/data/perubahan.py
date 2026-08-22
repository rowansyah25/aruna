"""Apakah sebuah snapshot membawa keterangan baru, atau hanya mengulang.

`market_snapshots` terukur pada 2026-08-21 berisi 422.172 baris dan 286 MB -
62% dari seluruh basis data - dan tumbuh sekitar 69.048 baris sehari. 60.227
di antaranya redundan secara isi. Dan sejarahnya tidak punya satu pun pembaca:
ketiga pemanggil tabel itu membaca baris terbaru per simbol.

Modul ini memutuskan mana yang layak masuk SQL. Ia **murni**: tidak ada
basis data, tidak ada jaringan, dan tidak ada jam - `sejak_detik` dioper
pemanggil, supaya jawabannya bisa diuji tanpa menunggu waktu nyata.

Yang menahan modul ini agar tidak menghapus informasi adalah
`JEDA_WAJIB_DETIK`. Satu baris tetap ditulis secara berkala meskipun tidak ada
yang berubah, sehingga "tidak ada baris karena pasar diam" tidak bisa
disalahbaca sebagai "tidak ada baris karena ARUNA berhenti melihat" - yang
kedua adalah kegagalan dan harus tetap terlihat.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum

from aruna.data.models import Snapshot

__all__ = [
    "AMBANG_HARGA_PCT",
    "AMBANG_SPREAD_BPS",
    "AMBANG_VOLUME_PCT",
    "JEDA_WAJIB_DETIK",
    "Perubahan",
    "layak_simpan",
]


class Perubahan(StrEnum):
    """Kenapa sebuah baris layak disimpan.

    Sebabnya dicatat, bukan hanya keputusannya, karena satu-satunya cara
    memeriksa gerbang ini di produksi adalah membaca alasannya di log.
    """

    PERTAMA = "PERTAMA"
    HARGA = "HARGA"
    VOLUME = "VOLUME"
    SPREAD = "SPREAD"
    MUTU = "MUTU"
    SESI = "SESI"
    WAKTU = "WAKTU"


#: Gerak harga yang dianggap berarti, dalam persen.  Di bawah ini adalah
#: getaran tick terakhir, bukan pergerakan pasar.
AMBANG_HARGA_PCT = 0.15

#: `volume_24h` merambat naik sepanjang hari pada setiap poll. Tanpa ambang,
#: kolom ini sendirian membuat setiap snapshot tampak berubah dan gerbangnya
#: tidak menahan apa pun.
AMBANG_VOLUME_PCT = 5.0

#: Spread yang melebar adalah likuiditas yang menguap - keterangan risiko,
#: bukan derau harga.
AMBANG_SPREAD_BPS = 2.0

#: Jeda terpanjang yang boleh lewat tanpa satu baris pun, dalam detik.
#: Lima belas menit: cukup jarang untuk memangkas 69.048 baris sehari menjadi
#: sekitar 1.920 pada dua puluh aset, cukup sering untuk membuat umpan yang
#: mati terlihat dalam satu siklus laporan.
JEDA_WAJIB_DETIK = 900.0


def layak_simpan(
    baru: Snapshot,
    lama: Snapshot | None,
    *,
    sejak_detik: float,
) -> tuple[bool, frozenset[Perubahan]]:
    """Apakah `baru` layak ditulis ke SQL, dan kenapa.

    `lama` adalah snapshot terakhir yang **benar-benar tersimpan** untuk aset
    yang sama, bukan yang terakhir dilihat - kalau tidak, harga bisa merambat
    melewati ambang berkali-kali tanpa satu baris pun ditulis.

    `sejak_detik` diukur terhadap `lama` itu juga.
    """
    if lama is None:
        return True, frozenset({Perubahan.PERTAMA})

    sebab: set[Perubahan] = set()

    if _melewati_pct(baru.last_price, lama.last_price, AMBANG_HARGA_PCT):
        sebab.add(Perubahan.HARGA)
    if _melewati_pct(baru.volume_24h, lama.volume_24h, AMBANG_VOLUME_PCT):
        sebab.add(Perubahan.VOLUME)
    if _melewati_mutlak(baru.spread_bps, lama.spread_bps, AMBANG_SPREAD_BPS):
        sebab.add(Perubahan.SPREAD)
    if baru.quality is not lama.quality:
        sebab.add(Perubahan.MUTU)
    if baru.session != lama.session or baru.market_open != lama.market_open:
        sebab.add(Perubahan.SESI)

    # Diperiksa terakhir dan ditambahkan, tidak menggantikan: kalau harga
    # bergerak DAN jeda wajib lewat, log harus menyebut keduanya. Menyebut
    # "disimpan karena waktu" untuk baris yang sesungguhnya disimpan karena
    # pasar bergerak akan membuat gerbang ini tampak lebih ketat daripada
    # sebenarnya.
    if sejak_detik >= JEDA_WAJIB_DETIK:
        sebab.add(Perubahan.WAKTU)

    return bool(sebab), frozenset(sebab)


def _melewati_pct(baru: Decimal | None, lama: Decimal | None, ambang: float) -> bool:
    """Perubahan relatif yang melewati `ambang` persen.

    Dasar nol berarti umpan rusak atau baru hidup; perubahan apa pun darinya
    adalah keterangan baru, dan pembagian dengan nol tidak boleh menjatuhkan
    seluruh lintasan poll.
    """
    if baru is None and lama is None:
        return False
    if baru is None or lama is None:
        return True
    if lama == 0:
        return baru != 0
    try:
        return abs((baru - lama) / lama) * 100 >= Decimal(str(ambang))
    except (InvalidOperation, ZeroDivisionError):
        return True


def _melewati_mutlak(
    baru: Decimal | None, lama: Decimal | None, ambang: float
) -> bool:
    """Perubahan mutlak yang melewati `ambang`.

    Dipakai untuk spread, yang sudah dinyatakan dalam basis point - menyatakan
    perubahannya sebagai persen dari basis point akan mengaburkan artinya.
    """
    if baru is None and lama is None:
        return False
    if baru is None or lama is None:
        return True
    return abs(baru - lama) >= Decimal(str(ambang))
