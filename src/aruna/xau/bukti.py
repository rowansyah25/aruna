"""Bukti teknikal per timeframe, seluruhnya dari satu deret M5.

`AnalysisEngine` dipakai apa adanya - enam belas indikator, laporan struktur,
dan klasifikasi rezim yang sama persis dengan yang dipakai crypto. Tidak ada
indikator versi XAU: dua implementasi RSI yang berbeda akan menghasilkan dua
angka yang tidak bisa dibandingkan, dan yang salah tidak akan pernah ketahuan
karena tak ada yang membandingkannya.

**`as_of` diambil dari M5 dan hanya dari M5.**  Timeframe besar tersettle lebih
jarang: pukul 09:05 UTC, M5 sudah punya bar yang tutup 09:05 sementara H4 masih
berhenti di 08:00.  Mengambil yang tertua akan melaporkan bukti empat jam lebih
tua daripada yang sebenarnya ada, dan gerbang kesegaran yang berdiri di atas
angka itu akan menolak analisis yang mutakhir.  Yang benar adalah M5 - spec
menetapkan keputusan final berasal dari M5, jadi kesegaran keputusan itu adalah
kesegaran M5.

`None` untuk timeframe besar berarti **belum cukup bahan**, bukan nol dan bukan
kerusakan: di awal jam, H4 memang belum punya 48 bar M5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aruna.analysis.engine import AnalysisEngine, TechnicalSnapshot
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.core.enums import Horizon
from aruna.data.models import Candle
from aruna.xau.timeframes import TumpukanTimeframe

_ENGINE = AnalysisEngine()


@dataclass(frozen=True, slots=True)
class BuktiXau:
    """Bukti teknikal empat timeframe pada satu instan."""

    m5: TechnicalSnapshot
    m15: TechnicalSnapshot | None = None
    h1: TechnicalSnapshot | None = None
    h4: TechnicalSnapshot | None = None

    @property
    def as_of(self) -> datetime:
        """Close bar M5 tersettle terbaru.  Lihat docstring modul."""
        return self.m5.as_of

    def tersedia(self) -> tuple[Horizon, ...]:
        """Timeframe yang buktinya benar-benar terhitung."""
        peta = {
            Horizon.M5: self.m5,
            Horizon.M15: self.m15,
            Horizon.H1: self.h1,
            Horizon.H4: self.h4,
        }
        return tuple(tf for tf, snap in peta.items() if snap is not None)


def _analisa(candles: list[Candle]) -> TechnicalSnapshot | None:
    """`None` saat bahannya kurang - bukan snapshot setengah jadi."""
    if not candles:
        return None
    try:
        return _ENGINE.analyse(CandleSeries.from_candles(candles))
    except InsufficientData:
        return None


def rakit_bukti(tumpukan: TumpukanTimeframe) -> BuktiXau | None:
    """Hitung bukti tiap timeframe.  ``None`` kalau M5 sendiri tidak ada."""
    m5 = _analisa(tumpukan.m5)
    if m5 is None:
        return None
    return BuktiXau(
        m5=m5,
        m15=_analisa(tumpukan.m15),
        h1=_analisa(tumpukan.h1),
        h4=_analisa(tumpukan.h4),
    )


__all__ = ["BuktiXau", "rakit_bukti"]
