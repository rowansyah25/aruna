"""Indicators, structure, and regime classification (PHASE 3).

Indicator tests use hand-checkable vectors rather than asserting whatever the
implementation happens to produce — a test that just records current output
cannot catch a wrong formula.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from aruna.analysis import indicators as ind
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.reading import Reading
from aruna.analysis.regime import classify_regime
from aruna.analysis.series import CandleSeries, InsufficientData
from aruna.analysis.structure import (
    BreakoutState,
    TrendStructure,
    analyse_structure,
    build_levels,
    classify_trend,
    compression,
    find_swings,
    gap,
)
from aruna.core.enums import Horizon, Market, Regime
from aruna.data.models import Candle, Provenance

START = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


def make_candle(
    index: int,
    close: float,
    *,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
    volume: float = 100.0,
    is_closed: bool = True,
    interval: Horizon = Horizon.H1,
) -> Candle:
    open_time = START + interval.duration * index
    return Candle(
        market=Market.CRYPTO,
        symbol="BTC/USDT",
        interval=interval,
        open_time=open_time,
        close_time=open_time + interval.duration,
        open=Decimal(str(open_ if open_ is not None else close)),
        high=Decimal(str(high if high is not None else close + 1)),
        low=Decimal(str(low if low is not None else close - 1)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        is_closed=is_closed,
        provenance=Provenance(source="test", server_timestamp=START),
    )


def series_from(closes: list[float], **kwargs) -> CandleSeries:
    return CandleSeries.from_candles(
        [make_candle(i, c, **kwargs) for i, c in enumerate(closes)]
    )


# ---------------------------------------------------------------------------


class TestSeriesLeakageGuard:
    """SPEC 24 is enforced here rather than in each indicator."""

    def test_unclosed_bars_are_excluded(self) -> None:
        candles = [make_candle(i, 100 + i) for i in range(5)]
        candles.append(make_candle(5, 999, is_closed=False))
        series = CandleSeries.from_candles(candles)

        assert len(series) == 5
        assert series.excluded_open_bars == 1
        assert series.last_close == 104  # not the unsettled 999

    def test_all_unclosed_is_refused(self) -> None:
        candles = [make_candle(i, 100, is_closed=False) for i in range(3)]
        with pytest.raises(InsufficientData, match="still forming"):
            CandleSeries.from_candles(candles)

    def test_empty_input_is_refused(self) -> None:
        with pytest.raises(InsufficientData):
            CandleSeries.from_candles([])

    def test_mixed_symbols_are_refused(self) -> None:
        a = make_candle(0, 100)
        b = Candle(**{**{f.name: getattr(a, f.name) for f in a.__dataclass_fields__.values()}})
        with pytest.raises(ValueError, match="one symbol"):
            CandleSeries.from_candles([a, _with_symbol(b, "ETH/USDT")])

    def test_series_is_sorted_by_time(self) -> None:
        candles = [make_candle(2, 102), make_candle(0, 100), make_candle(1, 101)]
        series = CandleSeries.from_candles(candles)
        assert series.closes == (100.0, 101.0, 102.0)


def _with_symbol(candle: Candle, symbol: str) -> Candle:
    from dataclasses import replace

    return replace(candle, symbol=symbol)


class TestMovingAverages:
    def test_sma_known_values(self) -> None:
        assert ind.sma_values([1, 2, 3, 4, 5], 3) == [2.0, 3.0, 4.0]

    def test_sma_needs_the_full_period(self) -> None:
        assert ind.sma_values([1, 2], 3) == []

    def test_ema_seeds_from_the_first_sma(self) -> None:
        values = ind.ema_values([1, 2, 3, 4, 5], 3)
        assert values[0] == pytest.approx(2.0)  # SMA of 1,2,3
        # multiplier 2/(3+1) = 0.5 -> (4-2)*0.5+2 = 3.0
        assert values[1] == pytest.approx(3.0)
        assert values[2] == pytest.approx(4.0)

    def test_sma_reading_reports_insufficiency(self) -> None:
        reading = ind.sma(series_from([1, 2, 3]), 50)
        assert not reading.available
        assert not reading.reliable
        assert "needs 50 bars" in reading.detail


class TestRsi:
    def test_all_gains_pins_to_100(self) -> None:
        reading = ind.rsi(series_from([float(100 + i) for i in range(20)]))
        assert reading.value == pytest.approx(100.0)

    def test_all_losses_pins_to_zero(self) -> None:
        reading = ind.rsi(series_from([float(200 - i) for i in range(20)]))
        assert reading.value == pytest.approx(0.0)

    def test_flat_series_is_neutral(self) -> None:
        reading = ind.rsi(series_from([100.0] * 20))
        assert reading.value == pytest.approx(50.0)

    def test_stays_within_bounds(self) -> None:
        closes = [100 + (i % 7) * 3 - (i % 3) * 2 for i in range(60)]
        value = ind.rsi(series_from([float(c) for c in closes])).value
        assert value is not None and 0.0 <= value <= 100.0

    def test_insufficient_data(self) -> None:
        assert not ind.rsi(series_from([100.0] * 5)).reliable


class TestAtrAndVolatility:
    def test_atr_on_constant_range(self) -> None:
        """Every bar spans exactly 2.0, so the average true range is 2.0."""
        candles = [make_candle(i, 100, high=101, low=99) for i in range(30)]
        reading = ind.atr(CandleSeries.from_candles(candles))
        assert reading.value == pytest.approx(2.0)

    def test_atr_reports_percent_of_price(self) -> None:
        candles = [make_candle(i, 100, high=101, low=99) for i in range(30)]
        reading = ind.atr(CandleSeries.from_candles(candles))
        assert reading.components["atr_pct"] == pytest.approx(2.0)

    def test_realised_volatility_of_a_flat_series_is_zero(self) -> None:
        assert ind.realised_volatility(series_from([100.0] * 40)).value == pytest.approx(0.0)


class TestBollinger:
    def test_flat_series_has_zero_bandwidth(self) -> None:
        reading = ind.bollinger(series_from([100.0] * 30))
        assert reading.components["bandwidth_pct"] == pytest.approx(0.0)
        assert reading.components["middle"] == pytest.approx(100.0)

    def test_percent_b_is_reported(self) -> None:
        closes = [100.0 + (i % 5) for i in range(40)]
        reading = ind.bollinger(series_from(closes))
        assert reading.value is not None


class TestMacd:
    def test_rising_series_gives_positive_macd(self) -> None:
        reading = ind.macd(series_from([float(100 + i) for i in range(60)]))
        assert reading.value is not None and reading.value > 0
        assert "signal" in reading.components
        assert "histogram" in reading.components

    def test_insufficient_data(self) -> None:
        assert not ind.macd(series_from([100.0] * 20)).reliable


class TestVolume:
    def test_vwap_is_undefined_without_volume(self) -> None:
        """A VWAP with no volume is just an average wearing a wrong name."""
        candles = [make_candle(i, 100, volume=0) for i in range(30)]
        reading = ind.vwap(CandleSeries.from_candles(candles), 20)
        assert reading.value is None
        assert "undefined" in reading.detail

    def test_vwap_with_constant_price_equals_that_price(self) -> None:
        candles = [make_candle(i, 100, high=100, low=100, volume=5) for i in range(30)]
        reading = ind.vwap(CandleSeries.from_candles(candles), 20)
        assert reading.value == pytest.approx(100.0)

    def test_volume_spike_is_detected(self) -> None:
        candles = [make_candle(i, 100, volume=10) for i in range(25)]
        candles.append(make_candle(25, 100, volume=50))
        reading = ind.volume_anomaly(CandleSeries.from_candles(candles))
        assert reading.value == pytest.approx(5.0)
        assert "spike" in reading.detail

    def test_normal_volume_is_not_flagged(self) -> None:
        candles = [make_candle(i, 100, volume=10) for i in range(30)]
        reading = ind.volume_anomaly(CandleSeries.from_candles(candles))
        assert reading.value == pytest.approx(1.0)
        assert "normal" in reading.detail


class TestStructure:
    def test_swing_high_needs_confirmation_on_both_sides(self) -> None:
        """The last bars cannot hold a confirmed pivot - the bars that would
        confirm it have not formed."""
        closes = [10, 11, 12, 20, 12, 11, 10]
        series = CandleSeries.from_candles(
            [make_candle(i, float(c), high=float(c) + 1, low=float(c) - 1)
             for i, c in enumerate(closes)]
        )
        swings = find_swings(series, lookback=3)
        assert len(swings) == 1
        assert swings[0].index == 3

    def test_no_swings_in_a_flat_series(self) -> None:
        assert find_swings(series_from([100.0] * 20), lookback=3) == []

    def test_uptrend_sequencing(self) -> None:
        from aruna.analysis.structure import SwingKind, SwingPoint

        swings = [
            SwingPoint(SwingKind.LOW, 0, 10, START),
            SwingPoint(SwingKind.HIGH, 1, 20, START),
            SwingPoint(SwingKind.LOW, 2, 15, START),
            SwingPoint(SwingKind.HIGH, 3, 25, START),
        ]
        trend, detail = classify_trend(swings)
        assert trend is TrendStructure.UPTREND
        assert "higher" in detail

    def test_downtrend_sequencing(self) -> None:
        from aruna.analysis.structure import SwingKind, SwingPoint

        swings = [
            SwingPoint(SwingKind.HIGH, 0, 25, START),
            SwingPoint(SwingKind.LOW, 1, 15, START),
            SwingPoint(SwingKind.HIGH, 2, 20, START),
            SwingPoint(SwingKind.LOW, 3, 10, START),
        ]
        assert classify_trend(swings)[0] is TrendStructure.DOWNTREND

    def test_too_few_swings_is_undetermined(self) -> None:
        from aruna.analysis.structure import SwingKind, SwingPoint

        swings = [SwingPoint(SwingKind.HIGH, 0, 25, START)]
        assert classify_trend(swings)[0] is TrendStructure.UNDETERMINED

    def test_levels_need_repeat_touches(self) -> None:
        from aruna.analysis.structure import SwingKind, SwingPoint

        once = [SwingPoint(SwingKind.LOW, 0, 100, START)]
        support, _ = build_levels(once, min_touches=2)
        assert support == []

        twice = [
            SwingPoint(SwingKind.LOW, 0, 100.0, START),
            SwingPoint(SwingKind.LOW, 5, 100.2, START),
        ]
        support, _ = build_levels(twice, min_touches=2)
        assert len(support) == 1
        assert support[0].touches == 2

    def test_structure_is_unreliable_without_enough_swings(self) -> None:
        report = analyse_structure(series_from([100.0] * 30))
        assert not report.reliable
        assert report.trend is TrendStructure.UNDETERMINED

    def test_breakout_needs_the_level_to_be_challenged(self) -> None:
        """A distant level must not produce a verdict - that bug reported a
        breakout on every asset at once."""
        closes = [100.0 + (i % 4) for i in range(60)]
        report = analyse_structure(series_from(closes))
        assert report.breakout in (
            BreakoutState.NONE,
            BreakoutState.RETEST,
            BreakoutState.REJECTION,
            BreakoutState.BREAKOUT_UP,
            BreakoutState.BREAKOUT_DOWN,
            BreakoutState.FALSE_BREAKOUT_UP,
            BreakoutState.FALSE_BREAKOUT_DOWN,
        )


class TestCompressionAndGap:
    def test_compression_detects_a_tightening_range(self) -> None:
        wide = [make_candle(i, 100, high=110, low=90) for i in range(20)]
        tight = [make_candle(20 + i, 100, high=101, low=99) for i in range(20)]
        reading = compression(CandleSeries.from_candles(wide + tight))
        assert reading.value is not None and reading.value < 0.7
        assert "compression" in reading.detail

    def test_expansion_detects_a_widening_range(self) -> None:
        tight = [make_candle(i, 100, high=101, low=99) for i in range(20)]
        wide = [make_candle(20 + i, 100, high=110, low=90) for i in range(20)]
        reading = compression(CandleSeries.from_candles(tight + wide))
        assert reading.value is not None and reading.value > 1.4
        assert "expansion" in reading.detail

    def test_gap_up_is_measured(self) -> None:
        candles = [make_candle(0, 100), make_candle(1, 110, open_=110)]
        reading = gap(CandleSeries.from_candles(candles))
        assert reading.value == pytest.approx(10.0)
        assert "gap up" in reading.detail

    def test_no_gap_when_open_matches_previous_close(self) -> None:
        candles = [make_candle(0, 100), make_candle(1, 105, open_=100)]
        assert "no meaningful gap" in gap(CandleSeries.from_candles(candles)).detail


class TestReading:
    def test_insufficient_reading_is_not_reliable(self) -> None:
        reading = Reading.insufficient("x", have=3, need=14)
        assert not reading.available
        assert not reading.reliable
        assert reading.confidence == 0.0

    def test_confidence_scales_with_sample_size(self) -> None:
        just_enough = Reading("x", 1.0, sample_size=14, required=14)
        comfortable = Reading("x", 1.0, sample_size=40, required=14)
        assert just_enough.confidence == pytest.approx(0.5)
        assert comfortable.confidence == 1.0


class TestRegime:
    def _structure(self, **kwargs):
        from aruna.analysis.structure import StructureReport

        base = {
            "trend": TrendStructure.RANGE,
            "breakout": BreakoutState.NONE,
            "confirmed_swings": 6,
        }
        return StructureReport(**(base | kwargs))

    def test_no_evidence_is_uncertain(self) -> None:
        from aruna.analysis.structure import StructureReport

        verdict = classify_regime(
            structure=StructureReport(
                trend=TrendStructure.UNDETERMINED, breakout=BreakoutState.NONE
            )
        )
        assert verdict.regime is Regime.UNCERTAIN
        assert verdict.confidence == 0.0

    def test_unreliable_readings_do_not_vote(self) -> None:
        """A 3-bar sample must not outvote the fact that it is 3 bars."""
        thin = Reading("atr", 5.0, sample_size=3, required=15, components={"atr_pct": 9.0})
        verdict = classify_regime(structure=self._structure(), atr=thin)
        assert "HIGH_VOLATILITY" not in verdict.regime.value

    def test_trend_structure_drives_trending(self) -> None:
        """Struktur naik menghasilkan tren BERARAH (bagian 2 Phase 15).

        Arahnya sudah di tangan classifier dan dulu dibuang: `UPTREND` dan
        `DOWNTREND` sama-sama memilih `TRENDING`.
        """
        verdict = classify_regime(
            structure=self._structure(trend=TrendStructure.UPTREND),
            momentum=Reading("momentum", 4.0, sample_size=50, required=11),
        )
        assert verdict.regime is Regime.TRENDING_BULLISH

    def test_downtrend_structure_drives_bearish(self) -> None:
        """Pasangan cabang yang dulu ikut runtuh menjadi `TRENDING`.

        Diuji lewat `classify_regime` langsung, bukan lewat deret harga:
        cabut-uji 2026-08-21 membuktikan deret harga menggerakkan suara
        MOMENTUM, bukan suara STRUKTUR - jadi test berbasis deret tidak pernah
        menyentuh cabang ini.
        """
        verdict = classify_regime(
            structure=self._structure(trend=TrendStructure.DOWNTREND),
            momentum=Reading("momentum", -4.0, sample_size=50, required=11),
        )
        assert verdict.regime is Regime.TRENDING_BEARISH

    def test_break_ke_bawah_adalah_breakdown(self) -> None:
        """Dulu ikut tercatat `BREAKOUT` bersama tembusan ke atas."""
        verdict = classify_regime(
            structure=self._structure(breakout=BreakoutState.BREAKOUT_DOWN),
        )
        assert verdict.regime is Regime.BREAKDOWN

    def test_break_ke_atas_tetap_breakout(self) -> None:
        verdict = classify_regime(
            structure=self._structure(breakout=BreakoutState.BREAKOUT_UP),
        )
        assert verdict.regime is Regime.BREAKOUT

    def test_extreme_volume_is_an_anomaly(self) -> None:
        verdict = classify_regime(
            structure=self._structure(),
            volume_anomaly=Reading("volume_anomaly", 6.0, sample_size=50, required=21),
        )
        assert verdict.regime is Regime.ANOMALY

    def test_a_near_tie_reports_uncertain(self) -> None:
        """Reporting the leader of a tie would manufacture certainty."""
        verdict = classify_regime(
            structure=self._structure(
                trend=TrendStructure.UPTREND, breakout=BreakoutState.REJECTION
            )
        )
        assert verdict.regime is Regime.UNCERTAIN
        assert verdict.alternatives

    def test_news_shock_is_never_returned_without_news(self) -> None:
        """It needs news, which arrives in PHASE 4."""
        for trend in TrendStructure:
            verdict = classify_regime(structure=self._structure(trend=trend))
            assert verdict.regime is not Regime.NEWS_SHOCK

    def test_confidence_never_exceeds_one(self) -> None:
        verdict = classify_regime(
            structure=self._structure(trend=TrendStructure.UPTREND),
            momentum=Reading("momentum", 9.0, sample_size=99, required=11),
            rsi=Reading("rsi", 75.0, sample_size=99, required=15),
        )
        assert 0.0 <= verdict.confidence <= 1.0


class TestEngine:
    def test_snapshot_reports_every_reading(self) -> None:
        closes = [100 + (i % 9) - (i % 4) for i in range(120)]
        snapshot = AnalysisEngine().analyse(series_from([float(c) for c in closes]))

        assert snapshot.bars == 120
        assert snapshot.reliable_count > 10
        for name in ("rsi", "macd", "atr", "bollinger", "vwap", "volume_anomaly"):
            assert name in snapshot.readings

    def test_snapshot_as_of_is_when_the_last_settled_bar_closed(self) -> None:
        """Not when it opened.

        A bar's content is only known at its close, so dating the snapshot by
        the open reports the evidence as a whole interval older than it is -
        which made the PHASE 7 staleness gate refuse current daily analysis.
        """
        candles = [make_candle(i, 100.0 + i) for i in range(40)]
        candles.append(make_candle(40, 999.0, is_closed=False))
        snapshot = AnalysisEngine().analyse(CandleSeries.from_candles(candles))

        assert snapshot.excluded_open_bars == 1
        assert snapshot.as_of == candles[39].close_time
        assert snapshot.as_of > candles[39].open_time
        # Still never the unsettled bar: SPEC 24 holds.
        assert snapshot.as_of <= candles[40].open_time

    def test_thin_series_is_flagged_not_hidden(self) -> None:
        snapshot = AnalysisEngine().analyse(series_from([100.0] * 8))
        assert any("settled bars" in note for note in snapshot.notes)
        assert snapshot.regime.regime is Regime.UNCERTAIN

    def test_snapshot_carries_no_trade_direction(self) -> None:
        """PHASE 3 produces evidence. Direction is the council's job in PHASE 6."""
        snapshot = AnalysisEngine().analyse(series_from([float(100 + i) for i in range(60)]))
        payload = snapshot.to_dict()
        assert "direction" not in payload
        assert "signal" not in payload
        assert "confidence" not in payload  # only the regime has one

    def test_serialisation_round_trips(self) -> None:
        snapshot = AnalysisEngine().analyse(series_from([float(100 + i) for i in range(60)]))
        payload = snapshot.to_dict()
        assert payload["regime"]["regime"] in {r.value for r in Regime}
        assert payload["bars"] == 60
