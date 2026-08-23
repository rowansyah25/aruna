"""Rezim pasar dibaca dari beberapa timeframe sekaligus (bagian 17.3 - 17.8).

**Modul ini tidak menghitung rezim.** Ia membaca yang sudah dihitung dan
tersimpan di tabel ``regimes`` - lengkap dengan keyakinan, alasan, dan
alternatifnya - lalu menjawab satu pertanyaan yang tabel itu tidak jawab:
ketika 5m berkata RANGING dan 4h berkata TRENDING, mana yang primary.

**Kenapa pertanyaan itu penting.** Bagian 17.8 memisahkan PRIMARY REGIME dari
SECONDARY CONDITION supaya pullback kecil tidak terbaca sebagai perubahan tren
besar. Tanpa pemisahan itu, router akan berganti strategi tiap kali harga
mundur satu bar - dan strategi yang berganti tiap lima belas menit bukan
strategi melainkan derau yang diberi nama.

**Interval yang tidak ada dilaporkan, bukan didiamkan.** Rezim yang disimpulkan
dari dua interval sementara enam diminta bukan kesimpulan yang sama kuatnya.
Yang membacanya berhak tahu bedanya, dan ``PetaRezim.interval_hilang`` yang
memberitahunya.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "BOBOT_INTERVAL",
    "BacaanRezim",
    "PetaRezim",
    "susun_peta",
]


#: Bobot tiap interval saat menentukan rezim primary.
#:
#: **Kebijakan, bukan pengukuran**, dan ditulis begitu supaya tidak ada yang
#: mengutipnya sebagai temuan. Yang bisa dipertahankan bukan angkanya melainkan
#: **urutannya**: horizon panjang lebih berat.
#:
#: Alasannya bukan bahwa horizon panjang lebih benar, melainkan bahwa bagian
#: 17.8 justru menuntut pullback pendek TIDAK terbaca sebagai perubahan tren.
#: Tanpa pembobotan, primary akan diserahkan kepada rezim yang bacaannya
#: terbanyak - dan interval pendek selalu terbanyak, karena ia yang paling
#: sering dipindai. Itu membuat jawabannya ditentukan jadwal pemindaian alih-
#: alih pasar.
#:
#: Interval di luar daftar ini tetap ikut dihitung dengan bobot netral 1,0;
#: lihat :func:`susun_peta`.
BOBOT_INTERVAL: dict[str, float] = {
    "5m": 0.5,
    "15m": 1.0,
    "30m": 1.2,
    "1h": 1.6,
    "4h": 2.0,
    "1d": 2.4,
}

#: Bobot untuk interval yang tidak ada di :data:`BOBOT_INTERVAL`.
#:
#: Netral, bukan nol. Bacaan yang benar-benar ada tidak boleh hilang tanpa
#: jejak hanya karena intervalnya belum pernah dipikirkan - yang benar adalah
#: ia dipakai tanpa diistimewakan.
_BOBOT_NETRAL = 1.0


@dataclass(frozen=True, slots=True)
class BacaanRezim:
    """Satu baris ``regimes``, seperlunya saja.

    ``alasan`` ikut karena bagian 17.6 melarang rezim tanpa alasan. Peta yang
    membawa kesimpulan tanpa buktinya tidak bisa dibantah, dan yang tidak bisa
    dibantah bukan bukti melainkan pendapat berformat.
    """

    interval: str
    regime: str
    confidence: float
    alasan: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PetaRezim:
    """Kesimpulan lintas timeframe, berikut apa yang tidak terbaca."""

    #: ``None`` berarti TIDAK TERBACA - bukan "tidak ada rezim". Pemanggil yang
    #: menyamakan keduanya akan memilih strategi atas pasar yang belum pernah
    #: dilihatnya.
    primary: str | None
    #: Rata-rata keyakinan bacaan yang MENDUKUNG primary saja.
    primary_confidence: float
    #: Rezim lain yang terbaca di interval lain (bagian 17.8).
    sekunder: tuple[str, ...]
    per_interval: tuple[BacaanRezim, ...]
    #: Interval yang diminta tapi tidak punya bacaan.
    interval_hilang: tuple[str, ...]

    @property
    def terbaca(self) -> bool:
        return self.primary is not None


def susun_peta(bacaan: tuple[BacaanRezim, ...]) -> PetaRezim:
    """Peta rezim dari beberapa bacaan timeframe.

    Tiap bacaan menyumbang ``bobot_interval * keyakinan`` ke rezimnya. Yang
    dijumlahkan bobot BERSKALA keyakinan dan bukan bobot saja: bacaan 4h yang
    ragu-ragu tidak boleh mengalahkan dua bacaan 1h yang yakin hanya karena
    intervalnya lebih panjang.

    Seri diputus menurut nama rezim, bukan urutan masuk. Bersandar pada urutan
    berarti jawabannya bergantung pada urutan baris yang kebetulan keluar dari
    database - dan itu jawaban yang berubah tanpa ada yang mengubah apa pun.
    """
    if not bacaan:
        return PetaRezim(None, 0.0, (), (), tuple(BOBOT_INTERVAL))

    skor: dict[str, float] = {}
    for b in bacaan:
        bobot = BOBOT_INTERVAL.get(b.interval, _BOBOT_NETRAL)
        skor[b.regime] = skor.get(b.regime, 0.0) + bobot * (b.confidence / 100)

    primary = min(skor, key=lambda r: (-skor[r], r))

    # Keyakinan primary dihitung dari yang MENDUKUNGNYA saja. Bacaan yang
    # menyebut rezim lain bukan bukti yang lemah untuk rezim ini - ia bukti
    # untuk rezim yang lain, dan memasukkannya ke rata-rata mencampur dua
    # pertanyaan menjadi satu angka yang tidak menjawab keduanya.
    dukung = [b for b in bacaan if b.regime == primary]
    percaya = round(sum(b.confidence for b in dukung) / len(dukung), 1)

    sekunder = tuple(sorted({b.regime for b in bacaan if b.regime != primary}))
    ada = {b.interval for b in bacaan}
    hilang = tuple(i for i in BOBOT_INTERVAL if i not in ada)

    return PetaRezim(primary, percaya, sekunder, tuple(bacaan), hilang)
