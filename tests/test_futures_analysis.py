"""Funding, open interest and futures regime (FUTURES SPEC 6, 27, 28).

Three refusals carry this file, and each blocks a specific way of inventing
information:

* funding is a cost *and* a crowding signal, and the two point opposite ways -
  neither is collapsed into a direction;
* open interest is never read alone, because rising OI is not bullish or
  bearish until you know what price did;
* a liquidation cascade replaces the price-only regime rather than sitting
  beside it, because "TRENDING" during forced selling is the least useful true
  thing available.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.enums import Regime
from aruna.data.models import Provenance
from aruna.futures.funding import (
    CROWDED_RATE,
    EXTREME_RATE,
    MIN_HISTORY,
    FundingBias,
    FundingTrend,
    analyse_funding,
)
from aruna.futures.models import FundingRate, OpenInterest
from aruna.futures.openinterest import (
    NOISE_PCT,
    FlowRead,
    analyse_open_interest,
)
from aruna.futures.regime import (
    CASCADE_SHARE,
    FuturesRegime,
    classify_futures_regime,
)

NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _prov() -> Provenance:
    return Provenance(source="test", server_timestamp=NOW)


def _funding(rate: str, *, settled: bool = False, hours_ago: int = 0) -> FundingRate:
    return FundingRate(
        symbol="BTCUSDT",
        rate=Decimal(rate),
        funding_time=NOW - timedelta(hours=hours_ago),
        next_funding_time=NOW + timedelta(hours=8),
        interval_hours=8,
        provenance=_prov(),
        settled=settled,
    )


def _oi(value: str, *, hours_ago: int = 0) -> OpenInterest:
    return OpenInterest(
        symbol="BTCUSDT",
        open_interest=Decimal(value),
        notional=None,
        as_of=NOW - timedelta(hours=hours_ago),
        provenance=_prov(),
    )


# ---------------------------------------------------------------------------
# FUTURES SPEC 27 - funding
# ---------------------------------------------------------------------------


class TestFunding:
    def test_funding_is_never_collapsed_into_a_direction(self) -> None:
        """The refusal that matters: extreme funding says 'strong trend' and
        'crowded, about to squeeze' at the same time."""
        analysis = analyse_funding(_funding("0.002"))
        assert "SHORT dibayar untuk menahan posisi" in analysis.cost_favours()
        assert (
            "tidak ada yang diringkas jadi satu arah" in analysis.to_dict()["note"]
        )
        # There is no attribute that would answer "so, long or short?"
        assert not hasattr(analysis, "direction")

    def test_a_thin_history_reports_no_trend(self) -> None:
        analysis = analyse_funding(
            _funding("0.0001"),
            [_funding("0.0001", settled=True, hours_ago=8 * i) for i in range(3)],
        )
        assert analysis.trend is FundingTrend.UNKNOWN
        assert analysis.mean_rate is None
        assert f"butuh {MIN_HISTORY} sebelum trend" in analysis.findings[0]

    def test_a_full_history_yields_a_trend(self) -> None:
        rising = [
            _funding(str(Decimal("0.0001") * i), settled=True, hours_ago=8 * (12 - i))
            for i in range(1, 13)
        ]
        analysis = analyse_funding(_funding("0.0012"), rising)
        assert analysis.sufficient_history
        assert analysis.trend is FundingTrend.RISING
        assert analysis.mean_rate is not None

    def test_crowded_and_extreme_are_distinguished(self) -> None:
        crowded = analyse_funding(_funding(str(CROWDED_RATE)))
        extreme = analyse_funding(_funding(str(EXTREME_RATE)))

        assert crowded.crowded and not crowded.extreme
        assert extreme.crowded and extreme.extreme
        assert any(
            "kekuatan trend sekaligus risiko squeeze" in f for f in crowded.findings
        )
        assert any(
            "biayanya saja bisa melebihi pergerakan yang diprediksi" in f
            for f in extreme.findings
        )

    def test_the_bias_names_who_pays(self) -> None:
        assert analyse_funding(_funding("0.002")).bias is FundingBias.LONGS_PAY_HEAVILY
        assert analyse_funding(_funding("-0.002")).bias is FundingBias.SHORTS_PAY_HEAVILY
        assert analyse_funding(_funding("0.00001")).bias is FundingBias.NEUTRAL

    def test_the_projected_cost_scales_with_the_horizon(self) -> None:
        eight = analyse_funding(_funding("0.0001"), horizon_hours=8)
        day = analyse_funding(_funding("0.0001"), horizon_hours=24)
        assert day.projected_cost_pct == pytest.approx(
            eight.projected_cost_pct * 3, abs=Decimal("0.000001")
        )

    def test_the_cost_is_labelled_a_projection(self) -> None:
        """The rate moves before it settles; a promise here would be false."""
        analysis = analyse_funding(_funding("0.0005"), horizon_hours=24)
        assert any(
            "ini proyeksi, karena rate-nya bergerak sebelum settlement" in f
            for f in analysis.findings
        )

    def test_no_horizon_means_no_cost_claim(self) -> None:
        assert analyse_funding(_funding("0.0005")).projected_cost_pct is None


# ---------------------------------------------------------------------------
# FUTURES SPEC 28 - open interest
# ---------------------------------------------------------------------------


class TestOpenInterest:
    def test_open_interest_alone_produces_no_read(self) -> None:
        """The spec is explicit: OI tidak boleh digunakan sendirian."""
        analysis = analyse_open_interest(_oi("110000"), _oi("100000", hours_ago=1), None)
        assert analysis.flow is FlowRead.INCONCLUSIVE
        assert analysis.readable is False
        assert "OI sendirian tidak bisa mengatakan" in analysis.findings[0]

    def test_a_single_observation_says_nothing(self) -> None:
        analysis = analyse_open_interest(_oi("100000"), None, Decimal("2.0"))
        assert analysis.flow is FlowRead.INCONCLUSIVE
        assert "satu observasi tunggal tidak mengatakan apa pun" in analysis.findings[0]

    def test_the_four_quadrants(self) -> None:
        up, down = Decimal("2.0"), Decimal("-2.0")
        rising, falling = _oi("110000"), _oi("90000")
        base = _oi("100000", hours_ago=1)

        assert analyse_open_interest(rising, base, up).flow is FlowRead.NEW_LONGS
        assert analyse_open_interest(falling, base, up).flow is FlowRead.SHORT_COVERING
        assert analyse_open_interest(rising, base, down).flow is FlowRead.NEW_SHORTS
        assert (
            analyse_open_interest(falling, base, down).flow
            is FlowRead.LONG_LIQUIDATION
        )

    def test_a_rally_on_new_money_differs_from_a_rally_on_covering(self) -> None:
        """Identical on a price chart, opposite implications."""
        base = _oi("100000", hours_ago=1)
        funded = analyse_open_interest(_oi("110000"), base, Decimal("2.0"))
        covering = analyse_open_interest(_oi("90000"), base, Decimal("2.0"))

        assert funded.is_continuation and not funded.is_exhaustion
        assert covering.is_exhaustion and not covering.is_continuation
        assert any("habis begitu mereka selesai" in f for f in covering.findings)

    def test_a_continuation_read_is_not_a_promise(self) -> None:
        analysis = analyse_open_interest(
            _oi("110000"), _oi("100000", hours_ago=1), Decimal("2.0")
        )
        assert any(
            "bukan janji bahwa pergerakannya berlanjut" in f
            for f in analysis.findings
        )
        assert "tetap bisa berbalik" in analysis.to_dict()["note"]

    def test_a_quiet_market_is_not_forced_into_a_read(self) -> None:
        analysis = analyse_open_interest(
            _oi("100050"), _oi("100000", hours_ago=1), NOISE_PCT / 2
        )
        assert analysis.flow is FlowRead.QUIET
        assert analysis.readable is False

    def test_one_series_moving_alone_is_inconclusive(self) -> None:
        analysis = analyse_open_interest(
            _oi("100050"), _oi("100000", hours_ago=1), Decimal("3.0")
        )
        assert analysis.flow is FlowRead.INCONCLUSIVE
        assert any("tidak membentuk cerita flow" in f for f in analysis.findings)


# ---------------------------------------------------------------------------
# FUTURES SPEC 6 - regime
# ---------------------------------------------------------------------------


class TestFuturesRegime:
    def test_a_cascade_overrides_the_price_read(self) -> None:
        """'TRENDING' during forced selling is the least useful true thing."""
        verdict = classify_futures_regime(
            spot_regime=Regime.TRENDING,
            spot_confidence=0.8,
            liquidation_notional=Decimal(5_000),
            total_open_interest=Decimal(100_000),
        )
        assert verdict.regime is FuturesRegime.LIQUIDATION_DRIVEN
        assert verdict.overrode_spot is True
        assert verdict.spot_regime is Regime.TRENDING
        assert verdict.hostile is True
        assert "bukan pandangan yang sedang disampaikan" in verdict.findings[0]

    def test_small_liquidations_do_not_override(self) -> None:
        verdict = classify_futures_regime(
            spot_regime=Regime.TRENDING,
            spot_confidence=0.8,
            liquidation_notional=Decimal(100),
            total_open_interest=Decimal(100_000),
        )
        assert verdict.regime is FuturesRegime.TRENDING
        assert verdict.overrode_spot is False

    def test_the_cascade_threshold_is_a_share_of_open_interest(self) -> None:
        oi = Decimal(100_000)
        just_under = classify_futures_regime(
            spot_regime=Regime.RANGING,
            liquidation_notional=oi * (CASCADE_SHARE - Decimal("0.001")),
            total_open_interest=oi,
        )
        just_over = classify_futures_regime(
            spot_regime=Regime.RANGING,
            liquidation_notional=oi * CASCADE_SHARE,
            total_open_interest=oi,
        )
        assert just_under.regime is FuturesRegime.RANGING
        assert just_over.regime is FuturesRegime.LIQUIDATION_DRIVEN

    def test_an_unfunded_breakout_is_a_fake_breakout(self) -> None:
        """Spot sees the price leave the range; only OI sees nobody funded it."""
        flat_oi = analyse_open_interest(
            _oi("100050"), _oi("100000", hours_ago=1), Decimal("3.0")
        )
        verdict = classify_futures_regime(
            spot_regime=Regime.BREAKOUT, spot_confidence=0.7, open_interest=flat_oi
        )
        assert verdict.regime is FuturesRegime.FAKE_BREAKOUT
        assert verdict.overrode_spot is True
        assert verdict.hostile is True

    def test_a_breakout_on_short_covering_is_also_fake(self) -> None:
        covering = analyse_open_interest(
            _oi("90000"), _oi("100000", hours_ago=1), Decimal("3.0")
        )
        verdict = classify_futures_regime(
            spot_regime=Regime.BREAKOUT, spot_confidence=0.7, open_interest=covering
        )
        assert verdict.regime is FuturesRegime.FAKE_BREAKOUT

    def test_a_funded_breakout_survives(self) -> None:
        funded = analyse_open_interest(
            _oi("110000"), _oi("100000", hours_ago=1), Decimal("3.0")
        )
        verdict = classify_futures_regime(
            spot_regime=Regime.BREAKOUT, spot_confidence=0.7, open_interest=funded
        )
        assert verdict.regime is FuturesRegime.BREAKOUT
        assert verdict.overrode_spot is False

    def test_without_derivatives_data_it_degrades_to_the_spot_read(self) -> None:
        verdict = classify_futures_regime(
            spot_regime=Regime.TRENDING, spot_confidence=0.6
        )
        assert verdict.regime is FuturesRegime.TRENDING
        assert any(
            "tidak melihat apa pun tambahan tanpa data itu" in f
            for f in verdict.findings
        )

    def test_no_spot_regime_means_uncertain_not_a_guess(self) -> None:
        verdict = classify_futures_regime(spot_regime=None)
        assert verdict.regime is FuturesRegime.UNCERTAIN
        assert any(
            "derivatif sendirian tidak bisa mengklasifikasikannya" in f
            for f in verdict.findings
        )

    def test_extreme_funding_is_noted_without_changing_the_regime(self) -> None:
        verdict = classify_futures_regime(
            spot_regime=Regime.TRENDING,
            spot_confidence=0.6,
            funding=analyse_funding(_funding("0.003")),
        )
        assert verdict.regime is FuturesRegime.TRENDING
        assert any(
            "bubar lebih cepat daripada saat terbentuk" in f
            for f in verdict.findings
        )

    def test_hostile_regimes_are_named_for_the_leverage_engine(self) -> None:
        for regime, expected in (
            (Regime.HIGH_VOLATILITY, True),
            (Regime.NEWS_SHOCK, True),
            (Regime.RANGING, False),
            (Regime.LOW_VOLATILITY, False),
        ):
            verdict = classify_futures_regime(spot_regime=regime)
            assert verdict.hostile is expected, regime
