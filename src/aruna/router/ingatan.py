"""Ingatan Phase 15 sebagai bukti bagi router (bagian 17.20).

**Ditunda di rencana awal, dan alasannya masih separuh benar.** Rencana menulis:
"Mesin kemiripan sudah ada dan mahal (sapuan kuadratik yang sempat memblokir
loop 154 detik). Menyambungkannya menuntut gerbang kinerjanya sendiri."

Yang mahal adalah **sapuan kemiripannya** - mengambil sampai lima ribu ingatan
per (pasar, timeframe) lalu membandingkan tiga belas dimensi berbobot di Python,
untuk tiap aset, tiap siklus. Modul ini tidak melakukannya. Ia mengajukan
pertanyaan yang jauh lebih sempit dan terindeks::

    untuk simbol ini, pada 15m, di rezim ini - sejarah pernah berkata apa?

Satu ``GROUP BY`` untuk seluruh aset sekaligus. Bedanya bukan penghematan
melainkan pertanyaan yang berbeda, dan modul ini **tidak** mengaku sebagai mesin
kemiripan PASAL 15: tidak ada pembobotan dimensi, tidak ada cakupan, tidak ada
skor kemiripan. Yang dipakai bersama hanyalah korpusnya.

Kenapa hanya 15m
================

**Karena pengukuran ARUNA sendiri mengatakannya**, dan router tidak boleh
memutuskan ulang apa yang sudah diputuskan. Gerbang PASAL 15.44 hidup di
``app_state['memory_manfaat']``, dan isinya per 2026-08-23::

    15m  mendukung 121W/106L = 53,3%   melawan 67W/106L = 38,7%   +14,6 pt
    1h   mendukung 123W/88L  = 58,3%   melawan 32W/23L  = 58,2%   +0,1 pt
    1d   mendukung  26W/0L   = 100%    melawan 51W/1L   = 98,1%   +1,9 pt

Ingatan hanya terbukti MEMBEDAKAN di 15m. Di 1h dan 1d, kasus yang didukung
ingatan dan yang dilawannya berakhir sama saja - jadi memberinya bobot di sana
adalah memaksakan penggunaan memory atas bukti yang menyatakan ia tidak
menambah apa-apa.

Putusannya dibaca lewat :class:`~aruna.memory.manfaat.Manfaat`, bukan dihitung
ulang di sini.

Apa yang ia boleh ubah
======================

**Menskalakan, tidak memihak.** Ingatan mencatat hasil per KONDISI - simbol,
timeframe, rezim - bukan per strategi. Ia tidak punya apa pun untuk dikatakan
tentang STR-001 melawan STR-004, jadi ia menskalakan skor seluruh kandidat
sama rata. Yang berubah karena itu bukan SIAPA yang dipilih melainkan APAKAH
ada yang cukup layak dipilih sama sekali - dan itu memang yang ingatan tahu.

Ini juga yang membuatnya aman: penskalaan yang seragam tidak bisa membalik
peringkat, jadi ingatan tidak akan pernah menaikkan strategi yang kalah di atas
yang menang.
"""

from __future__ import annotations

from dataclasses import dataclass

from aruna.memory.context import Pengaruh

__all__ = [
    "INTERVAL_INGATAN",
    "MINIMUM_INGATAN",
    "BacaanIngatan",
    "pengaruh",
]


#: Timeframe yang ingatannya boleh membobot keputusan router.
#:
#: Lima belas menit, dan itu **bukan pilihan melainkan pembacaan**: gerbang
#: PASAL 15.44 hanya menyatakan 15m yang terbukti membedakan. Lihat catatan
#: modul untuk angkanya.
INTERVAL_INGATAN = "15m"

#: Berapa ingatan sekondisi sebelum angkanya boleh membobot apa pun.
#:
#: Dua puluh, dan angkanya diturunkan dari lebar selang kepercayaannya sendiri.
#: Pada dua puluh sampel, selang binomial 95% untuk win rate 50% membentang
#: sekitar +/-22 poin - sudah cukup sempit untuk membedakan "jelas buruk" dari
#: "jelas baik", dan masih terlalu lebar untuk mempercayai selisih kecil. Itu
#: sebabnya :func:`pengaruh` hanya bereaksi pada selisih BESAR dari netral, dan
#: diam pada yang di tengah.
MINIMUM_INGATAN = 20

#: Seberapa jauh dari 50% sebuah win rate harus bergeser untuk dianggap
#: berkata sesuatu. Lima belas poin - kira-kira selebar selang kepercayaan pada
#: sampel minimum, jadi yang lolos memang lebih besar daripada deraunya.
_SELISIH_BERARTI = 15.0

#: Sejauh mana ingatan boleh menarik skor. Nol koma dua berarti kondisi yang
#: sejarahnya paling buruk pun hanya menurunkan seperlima jarak dari netral -
#: ingatan adalah bukti, bukan veto (PASAL 15.42).
_RENTANG = 0.2


@dataclass(frozen=True, slots=True)
class BacaanIngatan:
    """Berapa ingatan sekondisi yang menang, dari berapa yang tuntas."""

    menang: int
    total: int

    @property
    def cukup(self) -> bool:
        return self.total >= MINIMUM_INGATAN

    @property
    def win_rate(self) -> float | None:
        """``None`` berarti **belum bisa dijawab**, bukan nol.

        Kondisi yang belum pernah terjadi dan kondisi yang selalu berakhir
        buruk adalah dua hal yang sangat berbeda, dan menyamakannya membuat
        tiap aset yang baru dipantau terlihat berbahaya.
        """
        if not self.total:
            return None
        return round(100.0 * self.menang / self.total, 1)


def pengaruh(
    bacaan: BacaanIngatan | None, *, dipakai: bool
) -> tuple[Pengaruh, float, str]:
    """Bagaimana sejarah berdiri terhadap keputusan ini.

    Memulangkan ``(pengaruh, skala, alasan)``. ``skala`` adalah pengali untuk
    jarak skor dari netral - satu berarti ingatan tidak mengubah apa pun.

    ``dipakai`` datang dari :attr:`~aruna.memory.manfaat.Manfaat.dipakai`,
    **dioper bukan dihitung**: yang memutuskan apakah ingatan pantas diberi
    bobot adalah evaluasi PASAL 15.44, dan dua tempat yang memutuskan hal yang
    sama dengan aturan berbeda adalah bug yang menunggu giliran.

    Kosakata pengaruhnya dipinjam dari :class:`~aruna.memory.context.Pengaruh`
    dengan alasan yang sama - dua kosakata untuk satu gagasan menghasilkan
    laporan yang tidak bisa disandingkan.
    """
    if not dipakai:
        return (
            Pengaruh.NEUTRAL,
            1.0,
            "ingatan tidak diberi bobot: evaluasi PASAL 15.44 belum "
            "membuktikannya membantu di timeframe ini",
        )
    if bacaan is None or not bacaan.cukup:
        ada = 0 if bacaan is None else bacaan.total
        return (
            Pengaruh.NEUTRAL,
            1.0,
            f"ingatan sekondisi baru {ada}, butuh {MINIMUM_INGATAN}",
        )

    wr = bacaan.win_rate
    assert wr is not None  # `cukup` sudah menjamin total > 0
    selisih = wr - 50.0
    if abs(selisih) < _SELISIH_BERARTI:
        return (
            Pengaruh.NEUTRAL,
            1.0,
            f"ingatan sekondisi {wr:.0f}% atas {bacaan.total} kasus - "
            "terlalu dekat netral untuk berkata apa pun",
        )

    # Diskalakan sebanding besarnya, dibatasi `_RENTANG`. Yang buruk menarik
    # kembali ke netral; yang baik mendorong sedikit menjauh - tapi tidak
    # pernah lebih daripada seperlima, karena ingatan adalah bukti bukan veto.
    arah = 1.0 if selisih > 0 else -1.0
    besar = min(1.0, abs(selisih) / 50.0)
    skala = 1.0 + arah * _RENTANG * besar
    sikap = Pengaruh.SUPPORTIVE if selisih > 0 else Pengaruh.CONTRARY
    return (
        sikap,
        round(skala, 3),
        f"ingatan sekondisi {wr:.0f}% atas {bacaan.total} kasus ({sikap.value})",
    )
