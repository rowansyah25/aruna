"""Backtest, walk-forward, out-of-sample and replay (PHASE 9).

Two families of test here matter more than the rest.

**Leakage.** A backtest that can see the future produces a number people act on,
and the number looks entirely plausible. The window must refuse to hand out a
bar that had not closed, and the guard must raise rather than warn.

**The harness is not silently broken.** The first version of this engine
published zero signals across ninety-four decisions, because the reconstructed
market state carried a quality flag that tripped the no-trade engine every time.
It reported as a cautious strategy. A backtest that structurally cannot produce
a signal is indistinguishable from one that finds no opportunities, so there is
a test that pins the difference.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.agents.notrade import evaluate_no_trade
from aruna.agents.risk import RiskLevel, assess_risk
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import InsufficientData
from aruna.backtest.engine import KNOWN_OPTIMISM, BacktestEngine, combine
from aruna.backtest.replay import CONFIDENCE_TOLERANCE, compare, summarise
from aruna.backtest.walkforward import (
    MIN_FOLD_SAMPLE,
    MIN_FOLDS,
    Fold,
    FoldResult,
    HoldoutViolation,
    WalkForwardReport,
    split_period,
)
from aruna.backtest.window import (
    MIN_BARS,
    Bar,
    LeakageError,
    Window,
    assert_no_leakage,
)
from aruna.core.enums import Decision, Horizon, Market

START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _bars(count: int, *, shape: str = "rising") -> list[Bar]:
    """Synthetic bars.

    ``swinging`` matters: a ruler-straight series has no swing highs or lows, so
    the structure agent reads nothing and the council correctly says WAIT at
    every step. Testing the engine against a straight line would prove only that
    it can decline. The sine term gives genuine pullbacks inside a trend.
    """
    out = []
    for i in range(count):
        if shape == "rising":
            price = 100.0 + i * 1.5
        elif shape == "falling":
            price = 100.0 + (count - i) * 1.5
        elif shape == "swinging":
            price = 100.0 + i * 0.9 + 7.0 * math.sin(i / 6.5)
        else:
            price = 100.0 + (i % 5) * 0.8
        out.append(
            Bar(
                open_time=START + timedelta(hours=i),
                close_time=START + timedelta(hours=i + 1),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=100.0 + i,
                close_price=Decimal(str(round(price, 6))),
            )
        )
    return out


def _window(count: int = 120, **kwargs) -> Window:
    return Window(
        _bars(count, **kwargs),
        market=Market.CRYPTO,
        symbol="BTC/USDT",
        interval=Horizon.H1,
    )


# ---------------------------------------------------------------------------
# SPEC 24, 36 - the future must be unreachable
# ---------------------------------------------------------------------------


class TestLeakage:
    def test_the_window_hands_out_nothing_that_had_not_closed(self) -> None:
        window = _window(100)
        moment = START + timedelta(hours=50)
        visible = window.bars_through(moment)

        assert visible
        assert all(bar.close_time <= moment for bar in visible)
        assert len(visible) < 100

    def test_a_series_is_dated_no_later_than_the_instant(self) -> None:
        window = _window(100)
        for hours in (20, 50, 99):
            moment = START + timedelta(hours=hours)
            assert window.series_at(moment).data_through <= moment

    def test_the_guard_raises_rather_than_warns(self) -> None:
        """A leaked backtest is worse than none: it produces a number people
        act on, and it looks entirely plausible."""
        with pytest.raises(LeakageError) as exc:
            assert_no_leakage(START + timedelta(hours=2), START, "BTC/USDT")
        assert "saw the future" in str(exc.value)
        assert "void" in str(exc.value)

    def test_a_guard_that_passes_is_silent(self) -> None:
        assert_no_leakage(START, START, "BTC/USDT")
        assert_no_leakage(START - timedelta(hours=1), START, "BTC/USDT")

    def test_too_early_in_the_history_is_refused_not_padded(self) -> None:
        window = _window(100)
        with pytest.raises(InsufficientData) as exc:
            window.series_at(START + timedelta(hours=3))
        assert f"{MIN_BARS} needed" in str(exc.value)

    def test_the_outcome_window_starts_after_the_decision(self) -> None:
        # A price at the decision instant would score the prediction against
        # the bar it was made from.
        window = _window(100)
        moment = START + timedelta(hours=50)
        prices = window.prices_after(moment, moment + timedelta(hours=5))
        assert all(when > moment for when, _ in prices)

    def test_steps_are_bar_closes_not_wall_clock(self) -> None:
        window = _window(100)
        moments = window.steps(
            start=START, end=START + timedelta(hours=100), every=1
        )
        closes = {b.close_time for b in _bars(100)}
        assert set(moments) <= closes


# ---------------------------------------------------------------------------
# The harness must be capable of producing a signal
# ---------------------------------------------------------------------------


class TestHarnessIsNotBroken:
    """The regression that reads as a result rather than a fault."""

    def test_the_reconstructed_state_does_not_trip_the_no_trade_engine(self) -> None:
        """Zero published across every step looked like caution. It was a
        quality flag on the reconstructed state setting risk to EXTREME and
        blocking every decision."""
        from aruna.agents.context import DecisionContext

        window = _window(120)
        moment = START + timedelta(hours=100)
        series = window.series_at(moment)
        context = DecisionContext(
            market=Market.CRYPTO,
            symbol="BTC/USDT",
            interval=Horizon.H1,
            as_of=series.data_through,
            state=window.state_at(moment),
            technical=AnalysisEngine().analyse(series),
        )

        risk = assess_risk(context)
        assert risk.overall is not RiskLevel.EXTREME
        assert evaluate_no_trade(context, risk=risk).blocked is False

    def test_the_backtest_publishes_on_data_that_should_produce_signals(self) -> None:
        result = BacktestEngine().run(
            _window(300, shape="swinging"),
            start=START,
            end=START + timedelta(hours=300),
        )
        assert result.steps > 0
        # The exact count depends on the council; that it is non-zero is the
        # invariant - a harness that can never publish is broken, not cautious.
        assert result.published > 0, (
            "the backtest published nothing across every step, which means the "
            "harness cannot produce a signal at all"
        )

    def test_no_order_book_means_no_spread_charged(self) -> None:
        """Not a flaw to hide - a caveat to carry."""
        state = _window(100).state_at(START + timedelta(hours=50))
        assert state.bid is None
        assert state.ask is None
        assert state.spread_bps is None
        assert "no spread cost is charged" in (state.quality_detail or "")


# ---------------------------------------------------------------------------
# SPEC 35 - the run itself
# ---------------------------------------------------------------------------


class TestBacktestRun:
    def test_an_empty_period_reports_why_rather_than_zero(self) -> None:
        result = BacktestEngine().run(
            _window(50),
            start=START + timedelta(days=400),
            end=START + timedelta(days=401),
        )
        assert result.steps == 0
        assert result.failures
        assert "no settled bars" in result.failures[0]

    def test_every_result_carries_its_caveats(self) -> None:
        result = BacktestEngine().run(
            _window(120), start=START, end=START + timedelta(hours=120)
        )
        payload = result.to_dict()
        assert payload["known_optimism"] == list(KNOWN_OPTIMISM)
        assert "flattering direction" in payload["note"]
        assert any("spread" in c for c in payload["known_optimism"])

    def test_accuracy_is_none_when_nothing_resolved(self) -> None:
        result = BacktestEngine().run(
            _window(60), start=START, end=START + timedelta(hours=20)
        )
        if result.resolved == 0:
            assert result.to_dict()["direction_accuracy"] is None

    def test_withheld_verdicts_are_counted_by_reason(self) -> None:
        result = BacktestEngine().run(
            _window(200, shape="flat"), start=START, end=START + timedelta(hours=200)
        )
        # Whatever the mix, nothing may be silently dropped.
        assert result.published + sum(result.withheld.values()) + result.waits <= (
            result.steps
        )

    def test_combining_assets_keeps_the_caveats(self) -> None:
        results = [
            BacktestEngine().run(
                _window(120), start=START, end=START + timedelta(hours=120)
            )
        ]
        combined = combine(results)
        assert combined["assets"] == 1
        assert combined["known_optimism"] == list(KNOWN_OPTIMISM)


# ---------------------------------------------------------------------------
# SPEC 37, 38 - walk-forward and the holdout
# ---------------------------------------------------------------------------


class TestSplit:
    def test_the_holdout_is_the_most_recent_slice(self) -> None:
        """Not a random one: market regimes are serially correlated, so a
        random holdout leaks through its neighbours."""
        split = split_period(START, START + timedelta(days=100), folds=4)
        assert split.holdout_end == START + timedelta(days=100)
        assert split.holdout_start > split.folds[-1].start
        assert split.folds[-1].end <= split.holdout_start

    def test_folds_tile_the_evaluated_period_without_gaps(self) -> None:
        split = split_period(START, START + timedelta(days=100), folds=4)
        assert split.folds[0].start == START
        for earlier, later in zip(split.folds, split.folds[1:], strict=False):
            assert earlier.end == later.start
        assert split.folds[-1].end == split.holdout_start

    def test_too_few_folds_is_refused(self) -> None:
        with pytest.raises(ValueError, match="cannot show consistency"):
            split_period(START, START + timedelta(days=100), folds=MIN_FOLDS - 1)

    def test_reaching_into_the_holdout_raises(self) -> None:
        split = split_period(START, START + timedelta(days=100), folds=4)
        with pytest.raises(HoldoutViolation) as exc:
            split.check_within_evaluation(split.holdout_start + timedelta(days=1))
        assert "only untouched data" in str(exc.value)

    def test_evaluating_inside_the_folds_is_allowed(self) -> None:
        split = split_period(START, START + timedelta(days=100), folds=4)
        split.check_within_evaluation(split.folds[0].end)

    def test_the_engine_calls_the_guard_on_every_decision(self) -> None:
        """The guard has to be *called* to guard anything.

        It was defined, exported and unit-tested, and nothing invoked it - so
        SPEC 38's holdout was protected only by the caller's date arithmetic
        happening to be right.
        """
        seen: list[datetime] = []
        result = BacktestEngine().run(
            _window(60),
            start=START,
            end=START + timedelta(hours=60),
            guard=seen.append,
        )
        assert seen
        assert len(seen) == result.steps

    def test_a_guard_violation_stops_the_run_rather_than_being_counted(
        self,
    ) -> None:
        """A holdout breach is not one more failed step; it voids the run."""

        def refuse(_moment: datetime) -> None:
            raise HoldoutViolation("reserved")

        with pytest.raises(HoldoutViolation):
            BacktestEngine().run(
                _window(60),
                start=START,
                end=START + timedelta(hours=60),
                guard=refuse,
            )

    def test_the_holdout_states_what_it_is_reserved_for(self) -> None:
        split = split_period(START, START + timedelta(days=100), folds=4)
        assert "PHASE 10" in split.to_dict()["note"]

    def test_a_run_records_whether_it_saw_the_holdout(self) -> None:
        """The stored flag was read by the repository and never set, so every
        run claimed it had stayed out of the reserved data - including the ones
        that had not."""
        from aruna.backtest.service import BacktestRun

        clean = BacktestRun(market=Market.CRYPTO, interval=Horizon.H1)
        spent = BacktestRun(
            market=Market.CRYPTO, interval=Horizon.H1, holdout_included=True
        )
        assert clean.to_dict()["holdout_included"] is False
        assert spent.to_dict()["holdout_included"] is True


class TestWalkForward:
    def _fold(self, index: int, resolved: int, correct: int) -> FoldResult:
        return FoldResult(
            fold=Fold(index, START, START + timedelta(days=10)),
            resolved=resolved,
            correct=correct,
        )

    def test_a_thin_fold_reports_no_accuracy(self) -> None:
        fold = self._fold(1, MIN_FOLD_SAMPLE - 1, 5)
        assert fold.accuracy is None
        assert fold.to_dict()["status"] == "INSUFFICIENT_SAMPLE"

    def test_consistency_needs_enough_measured_folds(self) -> None:
        report = WalkForwardReport(results=[self._fold(1, 50, 30), self._fold(2, 3, 1)])
        assert "INSUFFICIENT SAMPLE" in report.verdict

    def test_a_wide_spread_across_folds_is_called_inconsistent(self) -> None:
        report = WalkForwardReport(
            results=[
                self._fold(1, 50, 45),  # 90%
                self._fold(2, 50, 10),  # 20%
                self._fold(3, 50, 30),  # 60%
            ]
        )
        assert "INCONSISTENT" in report.verdict

    def test_a_narrow_spread_is_called_consistent(self) -> None:
        report = WalkForwardReport(
            results=[
                self._fold(1, 50, 30),
                self._fold(2, 50, 28),
                self._fold(3, 50, 31),
            ]
        )
        assert "CONSISTENT" in report.verdict

    def test_the_report_says_what_walk_forward_means_here(self) -> None:
        """ARUNA fits no parameters, so this is not an overfitting guard yet."""
        note = WalkForwardReport().to_dict()["note"]
        assert "fits no parameters" in note
        assert "PHASE 10" in note


# ---------------------------------------------------------------------------
# SPEC 39 - decision replay
# ---------------------------------------------------------------------------


class TestReplay:
    def _compare(self, **overrides):
        base = {
            "signal_id": "replay0000000001",
            "symbol": "BTC/USDT",
            "stored_decision": Decision.BUY,
            "stored_confidence": 0.62,
            "stored_as_of": START,
            "replayed_decision": Decision.BUY,
            "replayed_confidence": 0.62,
            "replayed_as_of": START,
        }
        return compare(**(base | overrides))

    def test_an_identical_replay_reproduces(self) -> None:
        result = self._compare()
        assert result.reproduced is True
        assert result.status == "REPRODUCED"
        assert result.divergences == []

    def test_a_changed_decision_is_reported_with_both_values(self) -> None:
        result = self._compare(replayed_decision=Decision.SELL)
        assert result.reproduced is False
        assert result.divergences[0].field == "decision"
        assert "BUY" in result.summary()
        assert "SELL" in result.summary()

    def test_storage_rounding_is_not_a_divergence(self) -> None:
        # Confidence is stored to three decimals.
        result = self._compare(replayed_confidence=0.62 + CONFIDENCE_TOLERANCE / 2)
        assert result.reproduced is True

    def test_a_real_confidence_change_is_a_divergence(self) -> None:
        result = self._compare(replayed_confidence=0.58)
        assert result.reproduced is False
        assert result.divergences[0].field == "confidence"

    def test_a_moved_evidence_cutoff_is_reported(self) -> None:
        result = self._compare(replayed_as_of=START + timedelta(hours=1))
        assert any(d.field == "as_of" for d in result.divergences)

    def test_summary_names_the_fields_not_just_the_count(self) -> None:
        """A hundred decisions differing in one field point at one cause."""
        results = [
            self._compare(replayed_confidence=0.5),
            self._compare(replayed_confidence=0.4),
            self._compare(replayed_decision=Decision.SELL),
        ]
        summary = summarise(results)
        assert summary["diverged"] == 3
        assert summary["diverging_fields"]["confidence"] == 2
        assert summary["diverging_fields"]["decision"] == 1
        assert "NON-DETERMINISTIC" in summary["verdict"]

    def test_all_reproducing_is_stated_as_determinism(self) -> None:
        summary = summarise([self._compare(), self._compare()])
        assert "DETERMINISTIC" in summary["verdict"]
        assert summary["reproduction_rate"] == 1.0

    def test_an_unreplayable_decision_is_not_counted_as_reproduced(self) -> None:
        """The dangerous version silently scores missing data as a pass."""
        from aruna.backtest.replay import ReplayResult

        results = [
            self._compare(),
            ReplayResult(
                signal_id="replay0000000002",
                symbol="ETH/USDT",
                unavailable="the bars behind this decision are gone",
            ),
        ]
        summary = summarise(results)
        assert summary["examined"] == 2
        assert summary["replayable"] == 1
        assert summary["not_replayable"] == 1
        assert summary["reproduction_rate"] == 1.0  # of what could be replayed

    def test_nothing_replayable_says_nothing_about_determinism(self) -> None:
        from aruna.backtest.replay import ReplayResult

        summary = summarise(
            [ReplayResult(signal_id="x", symbol="ETH/USDT", unavailable="gone")]
        )
        assert "NO DECISIONS WERE REPLAYABLE" in summary["verdict"]
        assert summary["reproduction_rate"] is None
