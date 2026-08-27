"""Empat timeframe dari satu sumber.

Spec meminta M5 primer dengan M15 konfirmasi, H1 tren, dan H4 konteks besar.
Meminta keempatnya ke venue berarti empat kali kredit dan - lebih buruk -
empat jawaban yang bisa saja tidak sinkron: bar H1 yang ditarik sedetik lebih
lambat dapat memuat pergerakan yang belum ada di M5 saat keputusan diambil.
Itu kebocoran masa depan yang tidak terlihat seperti kebocoran, karena setiap
bar-nya sah dan tidak ada satu pun yang "dari masa depan" bila dilihat
sendiri-sendiri.

Merakitnya dari bar M5 yang sama menutup keduanya: nol kredit tambahan, dan
mustahil ada timeframe yang tahu lebih banyak daripada M5 yang melahirkannya.

``resample_candles`` sudah membuang ember yang tidak lengkap alih-alih
merata-rata, sudah menyaring bar yang belum tutup lewat ``require_closed``,
dan sudah mengikat batas ember ke epoch tetap - bukan ke bar pertama dalam
daftar, yang akan membuat jendela berbeda menghasilkan bar H1 berbeda untuk
jam yang sama. Berkas ini tidak menghitung ulang apa pun; ia menyatakan
timeframe mana yang diminta dan melaporkan mana yang belum cukup bahannya.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aruna.core.enums import Horizon
from aruna.data.models import Candle
from aruna.data.resample import resample_candles

#: Diturunkan dari M5, sesuai spec: konfirmasi, tren, konteks besar.
TIMEFRAME_TURUNAN: tuple[Horizon, ...] = (Horizon.M15, Horizon.H1, Horizon.H4)


@dataclass(frozen=True, slots=True)
class TumpukanTimeframe:
    """Empat timeframe yang seluruhnya lahir dari satu deret M5."""

    m5: list[Candle] = field(default_factory=list)
    m15: list[Candle] = field(default_factory=list)
    h1: list[Candle] = field(default_factory=list)
    h4: list[Candle] = field(default_factory=list)

    def kurang(self) -> tuple[Horizon, ...]:
        """Timeframe turunan yang bahannya belum cukup.

        Terisi bukan berarti rusak: di awal jam, H4 memang belum punya 48 bar
        sementara M15 sudah punya tiga. Pemanggil yang membedakan "belum cukup
        bahan" dari "feed mati" membutuhkan daftar ini, bukan sekadar
        :attr:`lengkap`.
        """
        peta = {Horizon.M15: self.m15, Horizon.H1: self.h1, Horizon.H4: self.h4}
        return tuple(tf for tf in TIMEFRAME_TURUNAN if not peta[tf])

    @property
    def lengkap(self) -> bool:
        return bool(self.m5) and not self.kurang()


def rakit_tumpukan(m5: list[Candle]) -> TumpukanTimeframe:
    """Turunkan M15/H1/H4 dari ``m5``.  Tidak menyentuh jaringan."""
    if not m5:
        return TumpukanTimeframe()
    return TumpukanTimeframe(
        m5=m5,
        m15=resample_candles(m5, Horizon.M15, require_closed=True),
        h1=resample_candles(m5, Horizon.H1, require_closed=True),
        h4=resample_candles(m5, Horizon.H4, require_closed=True),
    )


__all__ = ["TIMEFRAME_TURUNAN", "TumpukanTimeframe", "rakit_tumpukan"]
