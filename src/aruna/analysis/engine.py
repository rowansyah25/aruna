"""Analysis engine: candles in, evidence out.

Produces a :class:`TechnicalSnapshot` - every SPEC 6 indicator, the SPEC 6
structure read, and the SPEC 9 regime, each carrying its own sample size.

The snapshot is explicitly **not** a recommendation. It contains no direction,
no confidence in a trade, no target. Turning evidence into a decision is the
council's job in PHASE 6, and doing it here would bypass the cross-protest,
veto and judge machinery the specification is built around.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.analysis import indicators as ind
from aruna.analysis.reading import Reading
from aruna.analysis.regime import RegimeVerdict, classify_regime
from aruna.analysis.series import CandleSeries
from aruna.analysis.structure import StructureReport, analyse_structure, compression, gap
from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import Horizon, Market

#: Bars below which nothing meaningful can be said at all.
MINIMUM_BARS = 15


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    market: Market
    symbol: str
    interval: Horizon
    computed_at: datetime
    #: The instant the evidence runs through: the *close* of the newest settled
    #: bar. Everything here derives from data at or before it (SPEC 24). Not the
    #: bar's open time - a daily bar labelled by its open would report evidence
    #: as a day older than it is, and a freshness check built on that would
    #: refuse perfectly current analysis.
    as_of: datetime
    bars: int
    readings: dict[str, Reading]
    structure: StructureReport
    regime: RegimeVerdict
    excluded_open_bars: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def reading(self, name: str) -> Reading | None:
        return self.readings.get(name)

    def value(self, name: str) -> float | None:
        reading = self.readings.get(name)
        return reading.value if reading and reading.reliable else None

    @property
    def reliable_count(self) -> int:
        return sum(1 for r in self.readings.values() if r.reliable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market.value,
            "symbol": self.symbol,
            "interval": self.interval.value,
            "computed_at": isoformat(self.computed_at),
            "as_of": isoformat(self.as_of),
            "bars": self.bars,
            "excluded_open_bars": self.excluded_open_bars,
            "reliable_readings": self.reliable_count,
            "readings": {name: r.to_dict() for name, r in self.readings.items()},
            "structure": self.structure.to_dict(),
            "regime": self.regime.to_dict(),
            "notes": list(self.notes),
        }

    def summary_line(self) -> str:
        parts = [
            f"{self.symbol} {self.interval.value}",
            f"regime={self.regime.regime.value}",
            f"conf={self.regime.confidence:.2f}",
            f"trend={self.structure.trend.value}",
            f"bars={self.bars}",
            f"reliable={self.reliable_count}/{len(self.readings)}",
        ]
        if self.structure.breakout.value != "NONE":
            parts.append(self.structure.breakout.value)
        return "  ".join(parts)


class AnalysisEngine:
    """Computes a technical snapshot from a candle series."""

    def analyse(self, series: CandleSeries) -> TechnicalSnapshot:
        readings: dict[str, Reading] = {}

        def record(reading: Reading) -> Reading:
            readings[reading.name] = reading
            return reading

        # SPEC 6 indicator set.
        record(ind.sma(series, 20))
        record(ind.sma(series, 50))
        record(ind.ema(series, 9))
        record(ind.ema(series, 21))
        record(ind.ema(series, 50))
        rsi = record(ind.rsi(series))
        macd = record(ind.macd(series))
        atr = record(ind.atr(series))
        bollinger = record(ind.bollinger(series))
        record(ind.realised_volatility(series))
        momentum = record(ind.momentum(series))
        vwap = record(ind.vwap(series, 20))
        volume_anomaly = record(ind.volume_anomaly(series))
        volume_trend = record(ind.volume_trend(series))
        squeeze = record(compression(series))
        record(gap(series))

        structure = analyse_structure(series)
        regime = classify_regime(
            structure=structure,
            atr=atr,
            momentum=momentum,
            rsi=rsi,
            bollinger=bollinger,
            compression=squeeze,
            volume_anomaly=volume_anomaly,
            volume_trend=volume_trend,
        )

        notes: list[str] = []
        if len(series) < MINIMUM_BARS:
            notes.append(
                f"only {len(series)} settled bars; most indicators need more"
            )
        if not vwap.available and vwap.detail:
            notes.append(f"vwap: {vwap.detail}")
        if series.excluded_open_bars:
            notes.append(
                f"{series.excluded_open_bars} unsettled bar(s) excluded (SPEC 24)"
            )
        if macd.reliable and abs(macd.components.get("histogram", 0.0)) > 0:
            previous = macd.components.get("previous_histogram", 0.0)
            current = macd.components.get("histogram", 0.0)
            if previous < 0 <= current:
                notes.append("MACD histogram crossed up")
            elif previous > 0 >= current:
                notes.append("MACD histogram crossed down")

        return TechnicalSnapshot(
            market=series.market,
            symbol=series.symbol,
            interval=series.interval,
            computed_at=now_utc(),
            as_of=series.data_through,
            bars=len(series),
            readings=readings,
            structure=structure,
            regime=regime,
            excluded_open_bars=series.excluded_open_bars,
            notes=tuple(notes),
        )


__all__ = ["MINIMUM_BARS", "AnalysisEngine", "TechnicalSnapshot"]
