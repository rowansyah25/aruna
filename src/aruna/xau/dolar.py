"""Kekuatan dolar sebagai BUKTI untuk XAU - tidak pernah sebagai aturan.

**Ini EUR/USD, bukan DXY, dan bedanya dinyatakan di mana-mana.**  Diukur
2026-08-28: Twelve Data menjawab 404 untuk ``DXY``, ``DX=F``, ``US10Y``, dan
``TNX`` - indeks dolar dan yield sama sekali tidak tersedia di paket ini.
EUR/USD tersedia, dan ia 57,6% bobot keranjang DXY, jadi ia proksi yang wajar.
Ia tetap BUKAN DXY, dan tak satu pun nama di modul ini berpura-pura sebaliknya.

**Jebakan yang hampir termakan:** simbol ``USDX`` ADA di venue ini - tapi ia
"SGI Enhanced Core ETF", instrumen yang sama sekali berbeda.  Simbolnya
resolve, harganya masuk akal, dan artinya salah.  Sebuah proksi dolar yang
diam-diam adalah ETF obligasi akan menghasilkan bukti yang tidak pernah
membantah apa pun karena tidak berhubungan dengan apa pun.

**Korelasi dihitung atas RETURN, bukan atas harga.**  Diukur atas 5000 bar M5
yang stempel waktunya disejajarkan:

    return : r = 0,348
    harga  : r = 0,879   <- MENYESATKAN

Dua deret yang sama-sama menanjak selama tujuh belas hari akan berkorelasi
tinggi tanpa hubungan apa pun.  Angka 0,879 itu spurious, dan memakainya akan
membuat bukti ini terlihat empat kali lebih kuat daripada sebenarnya.

**Kekuatannya jujur: lemah, tapi tandanya konsisten.**  Sembilan belas jendela
250-bar: median +0,366, rentang -0,046 sampai +0,586, bertanda positif pada
17 dari 19.  Artinya EUR/USD naik cenderung bersamaan dengan XAU naik - dolar
melemah, emas menguat - tapi r sekitar 0,35 hanya menjelaskan sekitar
seperdelapan ragam.

Karena itu modul ini **merekam**, tidak memutuskan.  Spec melarang keras "DXY
naik = pasti SELL XAUUSD", dan angka di atas menunjukkan kenapa larangan itu
benar: sebuah aturan absolut di atas r=0,35 akan salah sangat sering.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal

from aruna.core.enums import Horizon
from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.models import Candle
from aruna.data.provider import MarketDataProvider

log = get_logger(__name__)

#: Proksi kekuatan dolar.  BUKAN DXY - lihat docstring modul.
SIMBOL_PROKSI = "EUR/USD"

#: Bobot EUR/USD dalam keranjang DXY, untuk dicantumkan di laporan supaya
#: pembacanya tahu seberapa jauh proksi ini dari yang diproksikan.
BOBOT_DALAM_DXY = 0.576

#: Return minimum yang dibutuhkan sebelum korelasi berarti apa pun.
#:
#: Di bawah ini `statistics.correlation` tetap memulangkan angka, dan angka itu
#: akan berayun liar dari sampel ke sampel.  Sebuah korelasi yang dihitung dari
#: dua puluh return bukan korelasi yang lemah - ia korelasi yang tidak diukur.
MIN_RETURN = 100


@dataclass(frozen=True, slots=True)
class BuktiDolar:
    """Keadaan proksi dolar pada satu keputusan."""

    simbol: str
    #: Korelasi return dengan XAU.  ``None`` = tidak terukur, bukan nol.
    korelasi: float | None
    #: Berapa return yang masuk hitungan - penyebut yang membuat r bisa dinilai.
    sampel: int
    #: Gerak proksi selama jendela, persen.  ``None`` kalau tak terhitung.
    gerak_pct: Decimal | None

    @property
    def terukur(self) -> bool:
        return self.korelasi is not None


def _return(deret: list[float]) -> list[float]:
    return [
        (deret[i] - deret[i - 1]) / deret[i - 1]
        for i in range(1, len(deret))
        if deret[i - 1]
    ]


def hitung_bukti_dolar(
    xau: list[Candle], proksi: list[Candle], *, simbol: str = SIMBOL_PROKSI
) -> BuktiDolar:
    """Ukur hubungan XAU dengan proksi dolar pada bar yang WAKTUNYA SAMA.

    Penyejajaran stempel waktu bukan kerapian: dua deret M5 dari venue yang
    sama pun bisa punya bar yang berbeda saat salah satunya bolong, dan
    korelasi yang dihitung atas bar yang bergeser satu langkah mengukur
    hubungan yang tidak ada.
    """
    peta = {c.open_time: float(c.close) for c in proksi}
    sejajar = [(float(c.close), peta[c.open_time]) for c in xau if c.open_time in peta]

    if len(sejajar) < MIN_RETURN + 1:
        return BuktiDolar(simbol=simbol, korelasi=None, sampel=0, gerak_pct=None)

    ret_x = _return([a for a, _ in sejajar])
    ret_p = _return([b for _, b in sejajar])
    n = min(len(ret_x), len(ret_p))

    korelasi: float | None
    try:
        korelasi = round(statistics.correlation(ret_x[:n], ret_p[:n]), 4)
    except statistics.StatisticsError:
        # Salah satu deret datar sempurna - tidak ada ragam untuk dikorelasikan.
        korelasi = None

    awal, akhir = sejajar[0][1], sejajar[-1][1]
    gerak = Decimal(str(round((akhir - awal) / awal * 100, 6))) if awal else None

    return BuktiDolar(simbol=simbol, korelasi=korelasi, sampel=n, gerak_pct=gerak)


async def tarik_proksi(
    provider: MarketDataProvider, *, limit: int, simbol: str = SIMBOL_PROKSI
) -> list[Candle]:
    """Tarik bar proksi.  Daftar kosong kalau venue tidak menjawab.

    Kegagalan di sini TIDAK boleh menjatuhkan keputusan XAU: proksi dolar
    adalah bukti tambahan, dan ketiadaan bukti tambahan bukan alasan berhenti
    menilai.  Itu sebabnya galatnya ditelan di sini dan dilaporkan lewat log,
    bukan dilempar ke pemanggil.
    """
    try:
        return await provider.fetch_candles(simbol, Horizon.M5, limit=limit)
    except DataSourceUnavailableError as exc:
        log.warning("xau.proksi_dolar_gagal", simbol=simbol, sebab=str(exc))
        return []


__all__ = [
    "BOBOT_DALAM_DXY",
    "MIN_RETURN",
    "SIMBOL_PROKSI",
    "BuktiDolar",
    "hitung_bukti_dolar",
    "tarik_proksi",
]
