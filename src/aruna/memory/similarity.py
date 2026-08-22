"""Seberapa mirip dua kondisi pasar, dan seberapa banyak yang terbaca.

**Dua angka, dan keduanya tidak boleh dilebur** (PASAL 15.7, 15.23).

``skor`` menjawab "dari yang bisa dibandingkan, berapa yang cocok".
``cakupan`` menjawab "berapa banyak yang bisa dibandingkan sama sekali".
Similarity 100% atas dua dimensi dari delapan bukan hal yang sama dengan 100%
atas delapan-delapannya - yang pertama berarti "yang sedikit itu cocok".

Melebur keduanya menjadi satu angka menghasilkan nilai tinggi justru pada
rekaman yang paling sedikit datanya. Itu keluarga cacat yang sudah dua kali
muncul di sistem ini: kelengkapan integrasi yang terlihat penuh pada pemanggil
yang paling sedikit melapor, dan pembacaan yang menghitung "tidak dilaporkan"
sebagai "hadir".

Dimensi yang ``UNKNOWN`` di salah satu sisi **keluar dari penyebut** dan masuk
ke ``tak_terbaca`` - bukan dihitung sebagai ketidakcocokan (yang akan menghukum
rekaman lama karena data yang tidak pernah ditulis siapa pun) dan bukan sebagai
kecocokan (yang akan melaporkan dua ketiadaan sebagai kemiripan sempurna).
"""

from __future__ import annotations

from dataclasses import dataclass

from aruna.memory.dimensions import TERSIMPAN, Dimensi, sama_ternormalkan
from aruna.memory.fingerprint import Sidik

#: Ambang kemiripan minimum (PASAL 15.8). Pasalnya memberi contohnya sendiri:
#: "Minimum Similarity: 80%". Pemanggil boleh menaikkannya; menurunkannya
#: berarti memutuskan bahwa kondisi yang setengah mirip layak jadi bukti.
AMBANG_MIRIP = 80

#: Bobot tiap dimensi. Hanya dimensi TERSIMPAN yang punya - memberi bobot pada
#: yang selalu UNKNOWN akan mengecilkan cakupan setiap perbandingan tanpa
#: pernah bisa dinaikkan oleh data apa pun.
#:
#: Aset dan timeframe paling berat karena PASAL 15.13 dan 15.14 menyatakan
#: keduanya punya kepribadian sendiri: kemiripan lintas aset bernilai jauh
#: lebih kecil daripada kemiripan di aset yang sama, dan bobot yang rata
#: membuat BTC dan SOL terbaca setara.
BOBOT: dict[Dimensi, int] = {
    Dimensi.ASSET: 5,
    Dimensi.TIMEFRAME: 4,
    Dimensi.REGIME: 4,
    Dimensi.MARKET: 3,
    Dimensi.RISK_LEVEL: 2,
    Dimensi.QUALITY: 2,
    Dimensi.NEWS: 1,
    Dimensi.LIQUIDITY: 1,
    # Lima dimensi teknikal, ditambahkan 2026-08-21 sesudah evaluasi PASAL
    # 15.44 melaporkan selisih +3 poin - sidik jari berdimensi delapan tidak
    # cukup membedakan satu kondisi pasar dari yang lain.
    Dimensi.VOLATILITY: 3,
    Dimensi.MOMENTUM: 3,
    Dimensi.STRUCTURE: 3,
    Dimensi.VOLUME: 2,
    # **Sengaja ringan.** TREND diturunkan dari tanda momentum, jadi ia
    # sebagian besar versi kasar dari dimensi di atasnya. Memberinya bobot
    # setara berarti menghitung satu bacaan dua kali, dan kemiripan yang
    # digelembungkan begitu terlihat lebih meyakinkan justru ketika ia paling
    # sedikit menambah informasi.
    Dimensi.TREND: 1,
}

_TOTAL_BOBOT = sum(BOBOT[d] for d in TERSIMPAN)


@dataclass(frozen=True, slots=True)
class Kemiripan:
    """Hasil satu perbandingan. Angkanya dua, dan namanya disebut."""

    skor: int
    cakupan: int
    cocok: tuple[Dimensi, ...]
    beda: tuple[Dimensi, ...]
    tak_terbaca: tuple[Dimensi, ...]

    @property
    def cukup_mirip(self) -> bool:
        return self.skor >= AMBANG_MIRIP


def bandingkan(a: Sidik, b: Sidik) -> Kemiripan:
    """Kemiripan dua kondisi pasar (PASAL 15.7).

    Penyebut nol - tidak satu pun dimensi terbaca di kedua sisi - menghasilkan
    skor **nol**, bukan seratus dan bukan pengecualian. Jawaban apa pun yang
    bukan nol di situ berarti ARUNA mengaku mengenali kondisi yang tidak pernah
    ia lihat.
    """
    cocok: list[Dimensi] = []
    beda: list[Dimensi] = []
    tak_terbaca: list[Dimensi] = []

    # Membaca `normal`, bukan `nilai`. Keduanya isi yang sama; bedanya
    # `normal` sudah di-`strip().upper()` sekali saat sidiknya dibuat.
    #
    # Fungsi ini dipanggil n kali lipat n sementara sidiknya cuma ada n.
    # Terprofil 2026-08-22 pada 900 ingatan: 39,9 juta panggilan `diketahui`
    # dan 59,5 juta `str.upper` - seluruhnya menormalkan teks yang sama
    # berulang-ulang, dan 99,6% waktu sapuan habis di sini.
    kiri_semua, kanan_semua = a.normal, b.normal
    terbaca = 0
    setuju = 0

    for d in Dimensi:
        kiri, kanan = kiri_semua.get(d), kanan_semua.get(d)
        if kiri is None or kanan is None:
            tak_terbaca.append(d)
            continue
        bobot = BOBOT.get(d, 0)
        terbaca += bobot
        if sama_ternormalkan(kiri, kanan):
            cocok.append(d)
            setuju += bobot
        else:
            beda.append(d)

    return Kemiripan(
        skor=round(setuju * 100 / terbaca) if terbaca else 0,
        cakupan=round(terbaca * 100 / _TOTAL_BOBOT) if _TOTAL_BOBOT else 0,
        cocok=tuple(cocok),
        beda=tuple(beda),
        tak_terbaca=tuple(tak_terbaca),
    )


__all__ = ["AMBANG_MIRIP", "BOBOT", "Kemiripan", "bandingkan"]
