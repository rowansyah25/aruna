"""Futures learning and the daily report (FUTURES SPEC 40-45, 48).

The asymmetry this file exists to protect: a liquidation is reportable at n=1,
and a sample threshold that swallowed it would hide the single most serious
thing the leverage engine can get wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.futures.learning import (
    MIN_SAMPLE,
    FuturesLearningReport,
    PlanOutcome,
    PlanResult,
    PriceBar,
    daily_report,
    score_plan,
)
from aruna.futures.models import PositionSide
from aruna.futures.plan import PlanVerdict
from test_futures_plan import ENTRY, _plan

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _bars(*levels: tuple[str, str]) -> list[PriceBar]:
    """Bars from (low, high) pairs, one hour apart."""
    return [
        PriceBar(
            as_of=NOW + timedelta(hours=i),
            low=Decimal(low),
            high=Decimal(high),
            close=Decimal(high),
        )
        for i, (low, high) in enumerate(levels)
    ]


class TestOnlyPositionsAreScored:
    """A plan ARUNA declined to make must not enter the win rate of the ones
    it did."""

    @pytest.mark.parametrize("decision", ["WAIT"])
    def test_a_wait_is_not_a_result(self, decision: str) -> None:
        assert score_plan(_plan(decision=decision), _bars(("1", "2"))) is None

    def test_a_refusal_is_not_a_result(self) -> None:
        refused = _plan(structure_levels=(ENTRY + Decimal(50),))
        assert refused.verdict is PlanVerdict.REFUSED
        assert score_plan(refused, _bars(("1", "2"))) is None

    def test_no_path_is_open_not_a_loss(self) -> None:
        result = score_plan(_plan(), [])
        assert result is not None
        assert result.outcome is PlanOutcome.OPEN
        assert result.resolved is False


class TestTheExchangeActsFirst:
    """Within one bar the adverse level is assumed reached before the
    favourable one, and liquidation before the stop. Any other ordering
    flatters the record exactly on the bars where the outcome was in doubt."""

    def test_a_bar_through_both_stop_and_target_is_a_loss(self) -> None:
        plan = _plan()
        assert plan.verdict is PlanVerdict.PLAN
        # Low pierces the stop, high pierces the target, in the same bar.
        result = score_plan(
            plan, [PriceBar(NOW, high=Decimal(65000), low=Decimal(61000), close=ENTRY)]
        )
        assert result is not None
        assert result.outcome is PlanOutcome.STOPPED_OUT

    def test_a_bar_through_both_stop_and_liquidation_is_a_liquidation(self) -> None:
        plan = _plan()
        liquidation = plan.liquidation.price
        result = score_plan(
            plan,
            [
                PriceBar(
                    NOW,
                    high=ENTRY,
                    low=liquidation - Decimal(10),
                    close=liquidation,
                )
            ],
        )
        assert result is not None
        assert result.outcome is PlanOutcome.LIQUIDATED
        assert result.touched_liquidation is True

    def test_a_clean_target_hit_is_a_win(self) -> None:
        plan = _plan()
        result = score_plan(
            plan,
            [PriceBar(NOW, high=plan.target + Decimal(1), low=ENTRY, close=plan.target)],
        )
        assert result is not None
        assert result.outcome is PlanOutcome.TARGET_HIT
        assert result.won is True

    def test_neither_level_reached_expires(self) -> None:
        plan = _plan()
        result = score_plan(
            plan,
            [PriceBar(NOW, high=ENTRY + Decimal(10), low=ENTRY - Decimal(10), close=ENTRY)],
        )
        assert result is not None
        assert result.outcome is PlanOutcome.EXPIRED
        assert any(
            "tidak mengatakan apa pun soal arah" in f for f in result.findings
        )


class TestLiquidationIsExemptFromTheSampleThreshold:
    """The core asymmetry. Thresholds exist to stop a system over-reading
    noise; a liquidation is not noise."""

    @staticmethod
    def _result(outcome: PlanOutcome, *, touched: bool = False) -> PlanResult:
        return PlanResult(
            signal_id="sig-x",
            symbol="BTCUSDT",
            side=PositionSide.LONG,
            outcome=outcome,
            entry=ENTRY,
            exit_price=ENTRY,
            touched_liquidation=touched,
            findings=("the exchange closed this position",),
        )

    def test_one_liquidation_is_reported_immediately(self) -> None:
        report = FuturesLearningReport(results=(self._result(PlanOutcome.LIQUIDATED),))
        assert report.sufficient is False  # far below MIN_SAMPLE
        assert "LIQUIDATION" in report.verdict
        assert "tidak ada ukuran sample yang membuatnya jadi soal win rate" in (
            report.verdict
        )

    def test_a_small_sample_without_liquidations_says_insufficient(self) -> None:
        report = FuturesLearningReport(
            results=tuple(self._result(PlanOutcome.TARGET_HIT) for _ in range(3))
        )
        assert "SAMPLE TIDAK CUKUP" in report.verdict
        assert report.win_rate is None, "a percentage of three is not a win rate"

    def test_a_liquidation_outranks_the_insufficient_sample_message(self) -> None:
        """Both conditions hold at once; the liquidation must win."""
        results = tuple(self._result(PlanOutcome.TARGET_HIT) for _ in range(3))
        report = FuturesLearningReport(
            results=(*results, self._result(PlanOutcome.LIQUIDATED))
        )
        assert "SAMPLE TIDAK CUKUP" not in report.verdict
        assert "LIQUIDATION" in report.verdict

    def test_a_win_that_touched_liquidation_is_not_laundered_into_a_clean_win(
        self,
    ) -> None:
        report = FuturesLearningReport(
            results=(self._result(PlanOutcome.TARGET_HIT, touched=True),)
        )
        assert len(report.near_liquidations) == 1
        assert not report.liquidations

    def test_a_win_rate_appears_only_above_the_threshold(self) -> None:
        results = tuple(self._result(PlanOutcome.TARGET_HIT) for _ in range(MIN_SAMPLE))
        report = FuturesLearningReport(results=results)
        assert report.sufficient is True
        assert report.win_rate == 1.0
        assert "SAMPLE TIDAK CUKUP" not in report.verdict


class TestDailyReport:
    def test_refusals_are_on_the_first_screen(self) -> None:
        """A day of two plans and forty refusals is a day of mostly saying no."""
        text = daily_report(
            FuturesLearningReport(),
            plans_made=2,
            refusals=40,
            waits=5,
            no_signals=1,
            as_of=NOW,
        )
        head = "\n".join(text.splitlines()[:12])
        assert "ditolak:      40" in head
        assert "DITINJAU:       48" in head

    def test_a_day_with_no_plan_says_that_is_an_output(self) -> None:
        text = daily_report(
            FuturesLearningReport(),
            plans_made=0,
            refusals=12,
            waits=3,
            no_signals=0,
            as_of=NOW,
        )
        assert "bukan kegagalan menghasilkan output" in text

    def test_a_liquidation_is_named_in_the_daily_report(self) -> None:
        result = TestLiquidationIsExemptFromTheSampleThreshold._result(
            PlanOutcome.LIQUIDATED
        )
        text = daily_report(
            FuturesLearningReport(results=(result,)),
            plans_made=1,
            refusals=0,
            waits=0,
            no_signals=0,
            as_of=NOW,
        )
        assert "LIQUIDATED:" in text
        assert "sig-x" in text

    def test_every_report_states_that_aruna_executed_nothing(self) -> None:
        text = daily_report(
            FuturesLearningReport(),
            plans_made=0,
            refusals=0,
            waits=0,
            no_signals=0,
            as_of=NOW,
        )
        assert "tidak menempatkan order" in text


class TestNothingHasBeenMeasuredYet:
    def test_an_empty_report_says_so_rather_than_reporting_zero_percent(self) -> None:
        """0% and "nothing measured" are different claims."""
        report = FuturesLearningReport()
        assert report.win_rate is None
        assert "SAMPLE TIDAK CUKUP" in report.verdict
        assert report.to_dict()["win_rate"] is None
