"""Prediction lock, paper trading, multi-horizon, outcomes (PHASE 7).

The load-bearing tests here are the SPEC 20 ones: a locked prediction cannot be
mutated, and any edit that somehow happened is detectable afterwards. Everything
in PHASE 8 and 9 - calibration, autopsy, backtest - is measuring nothing if a
past forecast can be quietly adjusted, so those tests are the ones that must
never be relaxed.

Close behind them are the honesty tests: no target is invented when ATR is
unavailable, a WAIT is never scored as a wrong direction, and net PnL is never
reported as gross.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.agents.context import DecisionContext, MarketState
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import CandleSeries
from aruna.core.enums import Decision, Horizon, Market
from aruna.council.session import Council
from aruna.data.models import Candle, Provenance
from aruna.signals.lock import (
    MAX_EVIDENCE_AGE_MULTIPLE,
    MIN_LOCK_CONFIDENCE,
    TARGET_ATR_MULTIPLE,
    ImmutabilityError,
    LeakageError,
    build_signal,
    covers_costs,
    evidence_age_note,
    round_trip_cost_pct,
    should_lock,
    supersede,
    verify_integrity,
)
from aruna.signals.models import (
    LockedSignal,
    OutcomeClass,
    SignalStatus,
    TradeResult,
)
from aruna.signals.multihorizon import HorizonView, MultiHorizonView, build_view
from aruna.signals.outcome import (
    FLAT_THRESHOLD_PCT,
    MIN_OBSERVATIONS,
    build_samples,
    exit_point,
    format_result,
    is_resolvable,
    resolve,
    sample_offsets,
    sampling_intervals,
    stop_price,
    summarise,
)
from aruna.signals.paper import (
    CRYPTO_COSTS,
    IDX_COSTS,
    close_trade,
    cost_model,
    open_trade,
)
from aruna.signals.paper import summarise as summarise_trades
from aruna.signals.report import format_signal

#: Deliberately in the past. Synthetic candles run forward from here, and a
#: fixture whose "evidence" is dated after the wall clock would trip the SPEC 24
#: leakage guard in every test rather than in the one that means to.
NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _context(
    closes: list[float], *, market: Market = Market.CRYPTO, **state
) -> DecisionContext:
    candles = [
        Candle(
            market=market,
            symbol="BTC/USDT",
            interval=Horizon.H1,
            open_time=NOW + timedelta(hours=i),
            close_time=NOW + timedelta(hours=i + 1),
            open=Decimal(str(c)),
            high=Decimal(str(c + 1)),
            low=Decimal(str(c - 1)),
            close=Decimal(str(c)),
            volume=Decimal(100),
            provenance=Provenance(source="test", server_timestamp=NOW),
        )
        for i, c in enumerate(closes)
    ]
    technical = AnalysisEngine().analyse(CandleSeries.from_candles(candles))
    return DecisionContext(
        market=market,
        symbol="BTC/USDT" if market is Market.CRYPTO else "BBCA",
        interval=Horizon.H1,
        as_of=technical.as_of,
        state=MarketState(**({"last_price": Decimal(str(closes[-1]))} | state)),
        technical=technical,
    )


RISING = [float(100 + i * 1.5) for i in range(80)]
FALLING = [float(220 - i * 1.5) for i in range(80)]


def _signal(**overrides) -> LockedSignal:
    """A locked signal built directly, for tests that do not need a council."""
    base = {
        "signal_id": "test000000000001",
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "horizon": Horizon.H1,
        "direction": Decision.BUY,
        "confidence": 0.7,
        "reference_price": Decimal(1000),
        "entry_price": Decimal(1000),
        "target_price": Decimal(1050),
        "expected_move_pct": 5.0,
        "locked_at": NOW,
        "as_of": NOW - timedelta(minutes=1),
        "resolves_at": NOW + timedelta(hours=1),
        "reasoning": ("because the test said so",),
    }
    return LockedSignal(**(base | overrides))


def _prices(*pairs: tuple[int, float]) -> list[tuple[datetime, Decimal]]:
    return [(NOW + timedelta(minutes=m), Decimal(str(p))) for m, p in pairs]


# ---------------------------------------------------------------------------
# SPEC 20 - the prediction lock
# ---------------------------------------------------------------------------


class TestImmutability:
    """SPEC 20. These are the tests that must never be relaxed."""

    def test_a_locked_signal_cannot_be_mutated(self) -> None:
        signal = _signal()
        for field_name, value in [
            ("direction", Decision.SELL),
            ("confidence", 0.99),
            ("entry_price", Decimal(1)),
            ("target_price", Decimal(9999)),
            ("reasoning", ("rewritten",)),
            ("locked_at", NOW + timedelta(days=1)),
            ("horizon", Horizon.D1),
        ]:
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(signal, field_name, value)

    def test_fingerprint_covers_every_field_spec_20_freezes(self) -> None:
        original = _signal()
        for field_name, value in [
            ("signal_id", "test000000000002"),
            ("direction", Decision.SELL),
            ("confidence", 0.71),
            ("reference_price", Decimal(1001)),
            ("entry_price", Decimal(1001)),
            ("target_price", Decimal(1051)),
            ("expected_move_pct", 5.1),
            ("locked_at", NOW + timedelta(seconds=1)),
            ("as_of", NOW - timedelta(minutes=2)),
            ("reasoning", ("a different argument",)),
        ]:
            altered = dataclasses.replace(original, **{field_name: value})
            assert altered.fingerprint != original.fingerprint, field_name

    def test_fingerprint_survives_the_storage_round_trip(self) -> None:
        """The hash must be invariant under DECIMAL(30,12) storage.

        Without this the fingerprint is worse than useless: every untouched
        record would fail verification and be marked INVALIDATED, and the one
        mechanism protecting past predictions would be destroying them.
        """
        original = _signal(
            reference_price=Decimal(1000),
            entry_price=Decimal(1000),
            target_price=Decimal(1050),
            confidence=0.7,
        )
        # Exactly what MySQL hands back for those columns.
        as_stored = dataclasses.replace(
            original,
            reference_price=Decimal("1000.000000000000"),
            entry_price=Decimal("1000.000000000000"),
            target_price=Decimal("1050.000000000000"),
            confidence=float(Decimal("0.700")),
        )
        assert as_stored.fingerprint == original.fingerprint

    def test_a_zero_expected_move_is_not_hashed_as_missing(self) -> None:
        # 0.0 is falsy; "no move predicted" and "no prediction" are different.
        assert (
            _signal(expected_move_pct=0.0).fingerprint
            != _signal(expected_move_pct=None).fingerprint
        )

    def test_status_change_does_not_change_the_fingerprint(self) -> None:
        # The lifecycle must be able to advance without the record looking
        # tampered with - that is why status lives in a separate table.
        original = _signal()
        resolved = dataclasses.replace(original, status=SignalStatus.RESOLVED)
        assert resolved.fingerprint == original.fingerprint

    def test_verify_integrity_refuses_an_altered_record(self) -> None:
        original = _signal()
        stored = original.fingerprint

        verify_integrity(original, stored)  # unchanged: no raise

        tampered = dataclasses.replace(original, confidence=0.99)
        with pytest.raises(ImmutabilityError) as exc:
            verify_integrity(tampered, stored)
        assert "altered since it was locked" in str(exc.value)
        assert "cannot be scored" in str(exc.value)

    def test_supersede_leaves_the_original_untouched(self) -> None:
        original = _signal()
        before = original.fingerprint
        context = _context(FALLING)
        verdict = Council().convene(context)

        retired, replacement = supersede(original, verdict, context, model_version="t")

        assert retired.fingerprint == before
        assert retired.direction is original.direction
        assert retired.confidence == original.confidence
        assert retired.status is SignalStatus.SUPERSEDED
        assert replacement.signal_id != original.signal_id
        assert replacement.supersedes == original.signal_id

    def test_a_superseded_signal_cannot_be_superseded_again(self) -> None:
        original = _signal(status=SignalStatus.SUPERSEDED)
        context = _context(FALLING)
        with pytest.raises(ImmutabilityError):
            supersede(original, Council().convene(context), context, model_version="t")


def _directional(context: DecisionContext, decision: Decision, confidence=0.7):
    """A council verdict forced to a direction.

    The council's own conclusion on a synthetic series is WAIT - correctly, a
    ruler-straight line has no structure to read. Forcing the decision keeps
    these tests about the lock, which is what they are meant to test, instead of
    silently asserting nothing when the council stands aside.
    """
    return dataclasses.replace(
        Council().convene(context), decision=decision, confidence=confidence
    )


class TestLocking:
    def test_no_target_is_invented_when_atr_is_unavailable(self) -> None:
        # Six bars is too few for ATR. A round number here would be scored
        # later as though it had been a real forecast.
        context = _context([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        signal = build_signal(
            _directional(context, Decision.BUY),
            context,
            model_version="t",
            locked_at=context.as_of + timedelta(minutes=1),
        )

        assert signal.is_directional
        assert signal.target_price is None
        assert signal.expected_move_pct is None
        assert "NOT AVAILABLE" in format_signal(signal)

    def test_target_is_derived_from_measured_atr(self) -> None:
        context = _context(RISING)
        moment = context.as_of + timedelta(minutes=1)
        atr = context.reading("atr")
        assert atr is not None and atr.value  # the fixture must actually have one

        long = build_signal(
            _directional(context, Decision.BUY),
            context,
            model_version="t",
            locked_at=moment,
        )
        short = build_signal(
            _directional(context, Decision.SELL),
            context,
            model_version="t",
            locked_at=moment,
        )

        distance = Decimal(str(atr.value)) * Decimal(str(TARGET_ATR_MULTIPLE))
        assert long.target_price == long.reference_price + distance
        assert short.target_price == short.reference_price - distance
        assert long.expected_move_pct is not None and long.expected_move_pct > 0
        assert short.expected_move_pct is not None and short.expected_move_pct < 0

    def test_as_of_never_follows_locked_at(self) -> None:
        # SPEC 24 in structural form: the evidence predates the prediction.
        context = _context(RISING)
        signal = build_signal(
            Council().convene(context),
            context,
            model_version="t",
            locked_at=context.as_of + timedelta(minutes=1),
        )
        assert signal.as_of <= signal.locked_at

    def test_evidence_from_after_the_lock_is_refused(self) -> None:
        # The database CHECK would also refuse this; failing here gives the
        # caller a sentence instead of a constraint name.
        context = _context(RISING)
        with pytest.raises(LeakageError) as exc:
            build_signal(
                Council().convene(context),
                context,
                model_version="t",
                locked_at=context.as_of - timedelta(hours=1),
            )
        assert "after the lock" in str(exc.value)
        assert "cannot be based on data from after it was made" in str(exc.value)

    def test_wait_is_recorded_but_not_published(self) -> None:
        signal = _signal(direction=Decision.WAIT, confidence=0.9)
        lockable, reason = should_lock(signal)
        assert lockable is False
        assert "not a position" in reason

    def test_confidence_floor_withholds_a_weak_call(self) -> None:
        weak = _signal(confidence=MIN_LOCK_CONFIDENCE - 0.01)
        lockable, reason = should_lock(weak)
        assert lockable is False
        assert "below the" in reason

        strong = _signal(confidence=MIN_LOCK_CONFIDENCE + 0.01)
        assert should_lock(strong)[0] is True

    def test_stale_evidence_withholds_the_signal(self) -> None:
        # A "1h call" whose newest bar closed six hours ago is not a 1h call.
        stale = _signal(as_of=NOW - timedelta(hours=6))
        lockable, reason = should_lock(stale)
        assert lockable is False
        assert "stale" in reason
        assert "360 minute(s) old" in reason

    def test_a_target_smaller_than_the_costs_is_withheld(self) -> None:
        """A target the round trip eats is withheld, not published.

        The rule comes from the PHASE 9 1h backtest, where an average target
        of 0.398% sat under a 0.70% round trip and every prediction lost money
        even when the direction was right and the target was hit exactly.
        Publishing that is not a bad forecast, it is a misleading one.

        **That specific example no longer fails this gate.** The crypto round
        trip is 0.30% at Binance spot rates, so 0.398% now clears it. The
        finding was about a fee schedule that ARUNA no longer pays, and
        keeping 0.398% here would have quietly turned a withholding test into
        a publishing one. The threshold moved; the rule did not.
        """
        signal = _signal(target_price=Decimal("1002.00"), expected_move_pct=0.20)
        lockable, reason = should_lock(signal)

        assert lockable is False
        assert "0.20% against a 0.30% round-trip cost" in reason
        assert "even if the direction is right" in reason

    def test_the_old_phase_9_target_now_clears_the_lower_fee(self) -> None:
        """The consequence of the fee change, asserted rather than assumed.

        0.398% was a guaranteed loser at 0.70% and is publishable at 0.30%.
        Nothing about the forecasting improved. Anyone comparing a crypto
        publish rate from before 2026-08-17 with one from after is reading two
        different gates.
        """
        signal = _signal(target_price=Decimal("1003.98"), expected_move_pct=0.398)
        lockable, _ = should_lock(signal)
        assert lockable is True

    def test_a_target_that_clears_the_costs_is_published(self) -> None:
        signal = _signal(target_price=Decimal(1050), expected_move_pct=5.0)
        lockable, reason = should_lock(signal)
        assert lockable is True
        assert "clears the 0.30% round-trip cost" in reason

    def test_a_signal_with_no_target_cannot_be_shown_to_cover_costs(self) -> None:
        """Necessary honesty: without a target there is no magnitude claim to
        check, so it is not published as tradeable."""
        lockable, reason = should_lock(
            _signal(target_price=None, expected_move_pct=None)
        )
        assert lockable is False
        assert "cannot be shown to be covered" in reason

    def test_the_round_trip_cost_matches_what_the_paper_trader_charges(self) -> None:
        # Crypto: 0.10 entry + 0.10 exit + 10bps slippage (Binance spot taker,
        # no BNB discount). Was 0.70 while the venue charged 0.30 per side.
        assert round_trip_cost_pct(_signal()) == pytest.approx(0.30)
        # IDX is asymmetric and unchanged: 0.20 buy, 0.30 sell, 15bps.
        assert round_trip_cost_pct(
            _signal(market=Market.IDX, symbol="BBCA")
        ) == pytest.approx(0.65)

    def test_crypto_is_now_the_cheaper_market_to_round_trip(self) -> None:
        """The ordering flipped, and reports that compare markets will show it.

        Crypto cost more than IDX to trade until the venue changed; it now
        costs less than half. A crypto strategy that starts outperforming an
        IDX one across this date did not necessarily get better.
        """
        assert round_trip_cost_pct(_signal()) < round_trip_cost_pct(
            _signal(market=Market.IDX, symbol="BBCA")
        )

    def test_a_quoted_spread_raises_the_bar(self) -> None:
        """A wide spread is part of what a round trip costs."""
        tight = _signal(bid=Decimal("999.5"), ask=Decimal("1000.5"))  # 0.1%
        wide = _signal(bid=Decimal(995), ask=Decimal(1005))  # 1.0%

        assert round_trip_cost_pct(wide) > round_trip_cost_pct(tight)
        assert round_trip_cost_pct(tight) == pytest.approx(0.40, abs=0.01)
        assert round_trip_cost_pct(wide) == pytest.approx(1.30, abs=0.01)

    def test_a_wide_spread_can_withhold_a_signal_that_would_otherwise_publish(
        self,
    ) -> None:
        move = 1.2
        target = Decimal("1012")
        assert should_lock(_signal(target_price=target, expected_move_pct=move))[0]

        withheld, reason = should_lock(
            _signal(
                target_price=target,
                expected_move_pct=move,
                bid=Decimal(995),
                ask=Decimal(1005),
            )
        )
        assert withheld is False
        assert "1.30% round-trip cost" in reason

    def test_clearing_costs_is_not_claimed_to_be_profitable(self) -> None:
        """The 1d horizon clears costs and still lost money. The check is a
        necessary condition and the docstring says so."""
        assert "necessary condition, not a sufficient one" in covers_costs.__doc__

    def test_evidence_age_note_respects_the_horizon(self) -> None:
        # The same 30-minute gap is stale for 15m and fine for 1h.
        gap = timedelta(minutes=30)
        assert evidence_age_note(NOW - gap, Horizon.M15, NOW) is not None
        assert evidence_age_note(NOW - gap, Horizon.H1, NOW) is None
        assert MAX_EVIDENCE_AGE_MULTIPLE == 1.0

    def test_the_staleness_caveat_is_part_of_the_frozen_record(self) -> None:
        context = _context(RISING)
        signal = build_signal(
            Council().convene(context),
            context,
            model_version="t",
            locked_at=context.as_of + timedelta(days=3),
        )
        assert any("stale" in line for line in signal.reasoning)
        # And therefore covered by the fingerprint, not a runtime flag.
        assert signal.fingerprint != dataclasses.replace(
            signal, reasoning=signal.reasoning[:-1]
        ).fingerprint


# ---------------------------------------------------------------------------
# SPEC 10 - multi-horizon
# ---------------------------------------------------------------------------


class TestMultiHorizon:
    def test_horizons_are_not_forced_to_agree(self) -> None:
        # SPEC 10's own worked example: 5M BUY, 15M SELL, 1H SELL.
        view = MultiHorizonView(
            symbol="BTC/USDT",
            views=(
                HorizonView(Horizon.M5, Decision.BUY, 0.82),
                HorizonView(Horizon.M15, Decision.SELL, 0.71),
                HorizonView(Horizon.H1, Decision.SELL, 0.64),
            ),
        )
        assert view.conflicted is True
        scope = view.scope()
        assert "BUY 5m ONLY" in scope
        assert "SELL 15m, 1h ONLY" in scope

    def test_a_horizon_below_the_floor_does_not_qualify(self) -> None:
        view = MultiHorizonView(
            symbol="BTC/USDT",
            views=(HorizonView(Horizon.H1, Decision.BUY, 0.10),),
        )
        assert view.scope() == "NO HORIZON QUALIFIES"
        assert view.tradeable() == ()

    def test_waits_never_qualify(self) -> None:
        view = MultiHorizonView(
            symbol="BTC/USDT",
            views=(HorizonView(Horizon.H1, Decision.WAIT, 0.99),),
        )
        assert view.directional == ()
        assert view.scope() == "NO HORIZON QUALIFIES"

    def test_build_view_orders_by_horizon_length(self) -> None:
        context = _context(RISING)
        verdict = Council().convene(context)
        view = build_view("BTC/USDT", {Horizon.D1: verdict, Horizon.M15: verdict})
        assert [v.horizon for v in view.views] == [Horizon.M15, Horizon.D1]


# ---------------------------------------------------------------------------
# SPEC 22, 23 - outcomes
# ---------------------------------------------------------------------------


class TestOutcomes:
    def test_prices_outside_the_horizon_are_dropped(self) -> None:
        # SPEC 24: a price from after the horizon would flatter or punish the
        # prediction for a move it never claimed.
        signal = _signal()
        samples = build_samples(
            signal,
            _prices((-30, 900.0), (10, 1010.0), (30, 1020.0), (999, 5000.0)),
        )
        assert [s.price for s in samples] == [Decimal("1010.0"), Decimal("1020.0")]
        assert samples[-1].is_final is True

    def test_resolve_scores_against_the_original_prediction(self) -> None:
        signal = _signal()
        outcome = resolve(
            signal, build_samples(signal, _prices((30, 1030.0))), resolved_at=NOW
        )
        assert outcome.reference_price == signal.reference_price
        assert outcome.predicted_move_pct == signal.expected_move_pct
        assert outcome.actual_move_pct == pytest.approx(3.0)
        # Predicted +5%, got +3%: the error is signed and negative.
        assert outcome.prediction_error == pytest.approx(-2.0)
        assert outcome.direction_correct is True

    def test_wrong_from_start_versus_right_then_reversed(self) -> None:
        signal = _signal()

        never_good = resolve(
            signal,
            build_samples(signal, _prices((10, 990.0), (20, 980.0), (30, 970.0))),
            resolved_at=NOW,
        )
        assert never_good.outcome_class is OutcomeClass.WRONG_FROM_START
        assert never_good.direction_correct is False

        gave_it_back = resolve(
            signal,
            build_samples(signal, _prices((10, 1020.0), (20, 1010.0), (30, 990.0))),
            resolved_at=NOW,
        )
        assert gave_it_back.outcome_class is OutcomeClass.RIGHT_THEN_REVERSED
        # Same closing direction, different lesson - which is the whole point.
        assert gave_it_back.direction_correct is False

    def test_target_reached_counts_a_touch_during_the_horizon(self) -> None:
        signal = _signal()
        outcome = resolve(
            signal,
            build_samples(signal, _prices((10, 1060.0), (30, 1005.0))),
            resolved_at=NOW,
        )
        assert outcome.target_reached is True
        assert outcome.outcome_class is OutcomeClass.TARGET_REACHED

    def test_a_wait_is_never_scored_as_a_wrong_direction(self) -> None:
        wait = _signal(direction=Decision.WAIT, target_price=None, expected_move_pct=None)
        outcome = resolve(
            wait, build_samples(wait, _prices((30, 1050.0))), resolved_at=NOW
        )
        assert outcome.outcome_class is OutcomeClass.NO_POSITION

        block = format_result(wait, outcome)
        assert "WRONG" not in block
        assert "N/A - no position was taken" in block
        assert "MARKET RANGE" in block

    def test_a_wait_still_records_what_the_market_did(self) -> None:
        # SPEC 28 cannot judge a missed move if the excursion was zeroed.
        wait = _signal(direction=Decision.WAIT, target_price=None, expected_move_pct=None)
        outcome = resolve(
            wait,
            build_samples(wait, _prices((10, 1080.0), (30, 960.0))),
            resolved_at=NOW,
        )
        assert outcome.max_favourable_pct == pytest.approx(8.0)
        assert outcome.max_adverse_pct == pytest.approx(-4.0)

    def test_short_side_excursions_are_measured_in_its_own_favour(self) -> None:
        short = _signal(direction=Decision.SELL, target_price=Decimal(950))
        outcome = resolve(
            short,
            build_samples(short, _prices((10, 1020.0), (30, 980.0))),
            resolved_at=NOW,
        )
        assert outcome.direction_correct is True
        assert outcome.max_favourable_pct == pytest.approx(2.0)
        assert outcome.max_adverse_pct == pytest.approx(-2.0)

    def test_samples_are_taken_at_the_spec_23_offsets(self) -> None:
        """Not "every price we happened to have".

        The offsets are what make the path legible: five points spread across
        the horizon, labelled by where in it they fall.
        """
        signal = _signal()  # 1h horizon
        minute_by_minute = _prices(*[(m, 1000.0 + m) for m in range(0, 61)])
        samples = build_samples(signal, minute_by_minute)

        assert [s.offset_label for s in samples] == [
            "T+10%",
            "T+25%",
            "T+50%",
            "T+75%",
            "T+100%",
        ]
        assert [s.sampled_at.minute for s in samples] == [6, 15, 30, 45, 0]
        assert samples[-1].is_final is True
        assert sum(1 for s in samples if s.is_final) == 1

    def test_coarse_data_does_not_produce_duplicate_labels(self) -> None:
        # Three observations cannot fill five offsets; the same bar must not be
        # written twice under two names.
        signal = _signal()
        samples = build_samples(signal, _prices((5, 1010.0), (30, 1020.0), (60, 1030.0)))
        labels = [s.offset_label for s in samples]
        assert len(labels) == len(set(labels))
        assert len(samples) == 3

    def test_a_move_inside_the_noise_band_is_not_a_correct_call(self) -> None:
        """A +0.001% drift is not a successful BUY.

        Without the threshold, a track record measures rounding rather than
        skill - every flat market resolves in favour of whichever side happens
        to sit on the right side of the last decimal place.
        """
        signal = _signal()
        drift = Decimal(1000) * (1 + Decimal(str(FLAT_THRESHOLD_PCT / 200)))
        outcome = resolve(
            signal, build_samples(signal, [(NOW + timedelta(minutes=30), drift)]),
            resolved_at=NOW,
        )
        assert 0 < outcome.actual_move_pct < FLAT_THRESHOLD_PCT
        assert outcome.direction_correct is False

    def test_a_real_move_past_the_noise_band_still_counts(self) -> None:
        signal = _signal()
        outcome = resolve(
            signal,
            build_samples(signal, _prices((30, 1010.0))),
            resolved_at=NOW,
        )
        assert outcome.direction_correct is True

    def test_the_exit_point_takes_the_target_when_it_was_touched(self) -> None:
        signal = _signal()  # BUY, entry 1000, target 1050
        samples = build_samples(signal, _prices((10, 1060.0), (30, 1005.0)))
        price, when, reason = exit_point(signal, samples)

        assert price == signal.target_price
        assert when == NOW + timedelta(minutes=10)
        assert "target touched" in reason

    def test_the_exit_point_holds_to_expiry_when_the_target_was_missed(self) -> None:
        signal = _signal()
        samples = build_samples(signal, _prices((10, 1010.0), (30, 1020.0)))
        price, _, reason = exit_point(signal, samples)

        assert price == Decimal("1020.0")
        assert "no level reached" in reason

    def test_the_two_exit_rules_agree_when_the_target_was_missed(self) -> None:
        """They differ only on the predictions that worked - which is why
        taking profit early cannot help unless losers are cut too."""
        signal = _signal()
        samples = build_samples(signal, _prices((10, 990.0), (30, 970.0)))
        outcome = resolve(signal, samples, resolved_at=NOW)
        price, _, _ = exit_point(signal, samples)
        assert price == outcome.final_price

    def test_a_short_exits_when_price_falls_through_its_target(self) -> None:
        short = _signal(direction=Decision.SELL, target_price=Decimal(950))
        samples = build_samples(short, _prices((10, 940.0), (30, 990.0)))
        price, _, reason = exit_point(short, samples)

        assert price == Decimal(950)
        assert "target touched" in reason

    def test_the_stop_mirrors_the_target(self) -> None:
        """1:1 reward to risk is the neutral choice; any other ratio is a
        strategy claim needing its own evidence."""
        long = _signal()  # entry 1000, target 1050
        assert stop_price(long) == Decimal(950)

        short = _signal(direction=Decision.SELL, target_price=Decimal(950))
        assert stop_price(short) == Decimal(1050)

    def test_no_target_means_no_stop(self) -> None:
        assert stop_price(_signal(target_price=None)) is None

    def test_a_stop_closes_the_position_at_the_stop(self) -> None:
        signal = _signal()  # stop at 950
        samples = build_samples(signal, _prices((10, 940.0), (30, 1060.0)))
        price, when, reason = exit_point(signal, samples, stop_loss=True)

        assert price == Decimal(950)
        assert when == NOW + timedelta(minutes=10)
        assert "stopped out" in reason

    def test_without_the_flag_the_stop_is_ignored(self) -> None:
        signal = _signal()
        samples = build_samples(signal, _prices((10, 940.0), (30, 1060.0)))
        price, _, reason = exit_point(signal, samples, stop_loss=False)
        assert price == signal.target_price
        assert "target touched" in reason

    def test_the_stop_wins_when_a_sample_is_beyond_both_levels(self) -> None:
        """Intrabar order is unknowable, and assuming the profitable one is how
        backtests lie."""
        signal = _signal()
        # One observation below the stop, a later one above the target.
        samples = build_samples(signal, _prices((10, 900.0), (30, 1100.0)))
        price, _, reason = exit_point(
            signal, samples, take_profit=True, stop_loss=True
        )
        assert price == Decimal(950)
        assert "stopped out" in reason

    def test_a_short_stops_out_when_price_rises(self) -> None:
        short = _signal(direction=Decision.SELL, target_price=Decimal(950))
        samples = build_samples(short, _prices((10, 1060.0), (30, 940.0)))
        price, _, reason = exit_point(short, samples, stop_loss=True)
        assert price == Decimal(1050)
        assert "stopped out" in reason

    def test_a_wait_has_no_target_to_exit_at(self) -> None:
        wait = _signal(direction=Decision.WAIT, target_price=None, expected_move_pct=None)
        samples = build_samples(wait, _prices((30, 1050.0)))
        _, _, reason = exit_point(wait, samples)
        assert "held to horizon expiry" in reason

    def test_resolving_without_observations_raises(self) -> None:
        with pytest.raises(ValueError, match="no price observations"):
            resolve(_signal(), [], resolved_at=NOW)

    def test_a_signal_is_not_resolvable_before_its_horizon(self) -> None:
        signal = _signal()
        ready, reason = is_resolvable(signal, reference=NOW + timedelta(minutes=30))
        assert ready is False
        assert "remain" in reason

        ready, _ = is_resolvable(signal, reference=NOW + timedelta(hours=2))
        assert ready is True

    def test_idx_resolution_flags_a_closed_exchange(self) -> None:
        signal = _signal(market=Market.IDX, symbol="BBCA")
        ready, reason = is_resolvable(
            signal, market_open=False, reference=NOW + timedelta(hours=2)
        )
        assert ready is True
        assert "closed for part of it" in reason

    def test_sample_offsets_scale_with_the_horizon(self) -> None:
        assert [d for _, d in sample_offsets(Horizon.H1)][-1] == timedelta(hours=1)
        assert [d for _, d in sample_offsets(Horizon.M15)][-1] == timedelta(minutes=15)

    def test_sampling_never_uses_only_the_horizons_own_interval(self) -> None:
        """A 1d prediction read from 1d candles has one observation - the close.

        Every SPEC 23 class that depends on the path becomes unreachable, so
        the daily signals ARUNA actually locks could never be classified as
        anything but "right" or "wrong at the end".
        """
        for horizon in (Horizon.M15, Horizon.H1, Horizon.D1):
            candidates = sampling_intervals(horizon)
            assert candidates[0].duration < horizon.duration, horizon
            # Enough room for a path, not just an endpoint.
            observations = horizon.duration / candidates[0].duration
            assert observations >= MIN_OBSERVATIONS, horizon

    def test_the_horizons_own_interval_remains_a_last_resort(self) -> None:
        # Scoring from a single close is poor, but refusing to score at all is
        # worse - and the service reports when it had to fall back.
        assert sampling_intervals(Horizon.D1)[-1] is Horizon.D1
        assert sampling_intervals(Horizon.M1) == (Horizon.M1,)

    def test_summarise_reports_no_accuracy_without_directional_calls(self) -> None:
        wait = _signal(direction=Decision.WAIT, target_price=None, expected_move_pct=None)
        outcome = resolve(
            wait, build_samples(wait, _prices((30, 1000.0))), resolved_at=NOW
        )
        assert summarise([outcome])["note"] == "no directional outcomes yet"


# ---------------------------------------------------------------------------
# SPEC 34, 46 - paper trading
# ---------------------------------------------------------------------------


class TestPaperTrading:
    def test_costs_are_charged_and_net_is_below_gross(self) -> None:
        signal = _signal()
        trade = close_trade(
            open_trade(signal, capital=Decimal(1_000_000), opened_at=NOW),
            Decimal(1100),
            closed_at=NOW + timedelta(hours=1),
        )
        assert trade.gross_pnl > 0
        assert trade.total_costs > 0
        assert trade.net_pnl == trade.gross_pnl - trade.total_costs
        assert trade.net_pnl < trade.gross_pnl
        assert trade.result is TradeResult.WIN

    def test_a_move_smaller_than_costs_is_a_loss(self) -> None:
        # The figure that decides whether a marginal edge is real.
        signal = _signal()
        trade = close_trade(
            open_trade(signal, capital=Decimal(1_000_000), opened_at=NOW),
            Decimal("1002"),
            closed_at=NOW + timedelta(hours=1),
        )
        assert trade.gross_pnl > 0
        assert trade.net_pnl < 0
        assert trade.result is TradeResult.LOSS

    def test_entry_fills_on_the_side_that_costs_you(self) -> None:
        buy = open_trade(
            _signal(bid=Decimal(999), ask=Decimal(1001)),
            capital=Decimal(1_000_000),
            opened_at=NOW,
        )
        assert buy.entry_price == Decimal(1001)

        sell = open_trade(
            _signal(direction=Decision.SELL, bid=Decimal(999), ask=Decimal(1001)),
            capital=Decimal(1_000_000),
            opened_at=NOW,
        )
        assert sell.entry_price == Decimal(999)

    def test_no_spread_is_charged_when_none_was_quoted(self) -> None:
        trade = open_trade(_signal(), capital=Decimal(1_000_000), opened_at=NOW)
        assert trade.spread_cost == Decimal(0)

    def test_a_wait_has_no_position_to_open(self) -> None:
        with pytest.raises(ValueError, match="no position to open"):
            open_trade(
                _signal(direction=Decision.WAIT),
                capital=Decimal(1_000_000),
                opened_at=NOW,
            )

    def test_a_closed_trade_cannot_be_closed_again(self) -> None:
        trade = close_trade(
            open_trade(_signal(), capital=Decimal(1_000_000), opened_at=NOW),
            Decimal(1100),
            closed_at=NOW,
        )
        with pytest.raises(ValueError, match="already closed"):
            close_trade(trade, Decimal(1200), closed_at=NOW)

    def test_short_profits_when_price_falls(self) -> None:
        trade = close_trade(
            open_trade(
                _signal(direction=Decision.SELL, target_price=Decimal(900)),
                capital=Decimal(1_000_000),
                opened_at=NOW,
            ),
            Decimal(900),
            closed_at=NOW + timedelta(hours=1),
        )
        assert trade.gross_pnl > 0
        assert trade.result is TradeResult.WIN

    def test_the_target_multiple_is_not_called_an_r_multiple(self) -> None:
        """R is measured against the risk taken - the distance to a stop.

        ARUNA has no stop loss, so there is no R to report. The number is the
        net PnL against the distance to the modelled target, and it is named
        for what it is.
        """
        trade = close_trade(
            open_trade(_signal(), capital=Decimal(1_000_000), opened_at=NOW),
            Decimal(1050),
            closed_at=NOW + timedelta(hours=1),
            target_distance=Decimal(50),  # entry 1000, target 1050
        )
        assert trade.target_multiple is not None
        # Price reached the target exactly, so gross is 1.0x the distance. The
        # multiple lands at 0.94: costs still took 6% of a textbook winner,
        # which is the whole reason SPEC 34 insists on net. It took 14% at the
        # previous venue's 0.30%-per-side schedule - the bite shrank with the
        # fee, it did not disappear, and a multiple of exactly 1.0 here would
        # mean costs had stopped being charged at all.
        assert 0.9 < trade.target_multiple < 1.0
        assert trade.gross_pnl == Decimal(50) * trade.quantity
        assert not hasattr(trade, "r_multiple")
        assert "r_multiple" not in trade.to_dict()
        assert "target_multiple" in trade.to_dict()

    def test_no_target_distance_means_no_multiple(self) -> None:
        trade = close_trade(
            open_trade(_signal(), capital=Decimal(1_000_000), opened_at=NOW),
            Decimal(1050),
            closed_at=NOW + timedelta(hours=1),
        )
        assert trade.target_multiple is None

    def test_idx_sell_side_costs_more_than_the_buy_side(self) -> None:
        assert IDX_COSTS.sell_fee_pct > IDX_COSTS.taker_fee_pct
        assert cost_model(Market.IDX) is IDX_COSTS
        assert cost_model(Market.CRYPTO) is CRYPTO_COSTS

    def test_summary_reports_net_and_the_cost_ratio(self) -> None:
        trades = [
            close_trade(
                open_trade(
                    _signal(signal_id=f"paper{i:011d}"),
                    capital=Decimal(1_000_000),
                    opened_at=NOW,
                ),
                Decimal(1100 if i % 2 == 0 else 950),
                closed_at=NOW + timedelta(hours=1),
            )
            for i in range(4)
        ]
        summary = summarise_trades(trades)
        assert summary["trades"] == 4
        assert summary["trading_mode"] == "PAPER"
        assert summary["cost_ratio"] is not None
        assert Decimal(str(summary["net_pnl"])) < Decimal(str(summary["gross_pnl"]))

    def test_an_open_trade_is_not_counted_as_performance(self) -> None:
        trade = open_trade(_signal(), capital=Decimal(1_000_000), opened_at=NOW)
        assert trade.is_open is True
        assert summarise_trades([trade])["trades"] == 0


# ---------------------------------------------------------------------------
# SPEC 21, 46 - what is published
# ---------------------------------------------------------------------------


class TestPublication:
    def test_the_block_states_paper_and_the_lock(self) -> None:
        block = format_signal(_signal())
        assert "PAPER TRADE" in block
        assert "no orders" in block
        assert "locked and will not be edited" in block
        assert "test000000000001" in block

    def test_a_missing_target_is_stated_not_hidden(self) -> None:
        block = format_signal(_signal(target_price=None, expected_move_pct=None))
        assert "TARGET:      NOT AVAILABLE" in block
        assert "ATR could not be measured" in block

    def test_conflicting_horizons_are_shown_as_such(self) -> None:
        view = MultiHorizonView(
            symbol="BTC/USDT",
            views=(
                HorizonView(Horizon.M5, Decision.BUY, 0.82),
                HorizonView(Horizon.H1, Decision.SELL, 0.64),
            ),
        )
        block = format_signal(_signal(), view=view)
        assert "SCOPE:" in block
        assert "Horizons disagree" in block

    def test_a_superseding_signal_names_what_it_replaced(self) -> None:
        block = format_signal(_signal(supersedes="test000000000009"))
        assert "Supersedes test000000000009" in block
        assert "stays on record unchanged" in block
