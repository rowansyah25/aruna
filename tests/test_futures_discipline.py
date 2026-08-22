"""Behavioural discipline (FUTURES SPEC 34).

Every earlier gate judges one setup in isolation and is blind to the sequence.
These tests exist to prove this module is not blind to it - and, just as
importantly, that it stays quiet when the sequence is ordinary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.futures.discipline import (
    REVENGE_LOSS_STREAK,
    STREAK_WINDOW_HOURS,
    Pattern,
    review,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _row(
    *,
    hours_ago: float,
    outcome: str | None = None,
    risk: str | None = None,
    leverage: int | None = None,
    verdict: str = "PLAN",
):
    return {
        "created_at": NOW - timedelta(hours=hours_ago),
        "outcome": outcome,
        "risk_amount": Decimal(risk) if risk else None,
        "leverage": leverage,
        "verdict": verdict,
    }


def _losses(n: int, *, risk: str = "100", leverage: int = 5):
    """n losses, newest first, one hour apart."""
    return [
        _row(hours_ago=i + 1, outcome="STOPPED_OUT", risk=risk, leverage=leverage)
        for i in range(n)
    ]


class TestUnknownIsNotClean:
    """The defect that made four of five patterns unreachable in production.

    `recent()` did not join `futures_plan_results`, so live history rows had no
    `outcome` KEY at all. Every streak was 0, `clean` was True, and the report
    said "no losing streak" about a question it had never asked. Overtrading
    kept firing - it reads created_at and verdict - so the reports looked
    plausible, which is exactly why the hole stayed invisible.
    """

    def test_history_without_an_outcome_column_is_not_reported_clean(self) -> None:
        blind = [{"created_at": NOW - timedelta(hours=i + 1)} for i in range(5)]
        report = review(history=blind, reference=NOW)
        assert report.outcomes_known is False
        assert report.clean is False, "unknown was reported as clean"

    def test_an_unknown_streak_is_none_not_zero(self) -> None:
        """Zero is a measurement. None is the absence of one."""
        blind = [{"created_at": NOW - timedelta(hours=i + 1)} for i in range(5)]
        assert review(history=blind, reference=NOW).to_dict()["loss_streak"] is None

    def test_a_joined_but_unresolved_row_is_known(self) -> None:
        """`outcome=None` means joined and not yet resolved - a real answer."""
        joined = [
            {"created_at": NOW - timedelta(hours=i + 1), "outcome": None}
            for i in range(5)
        ]
        report = review(history=joined, reference=NOW)
        assert report.outcomes_known is True
        assert report.clean is True

    def test_an_empty_record_is_still_clean(self) -> None:
        """Nothing to read is not the same as failing to read."""
        assert review(history=[], reference=NOW).clean is True


class TestItStaysQuietWhenNothingIsWrong:
    """A discipline engine that always warns is noise, and noise gets ignored
    exactly when the real warning arrives."""

    def test_an_empty_record_is_clean(self) -> None:
        report = review(history=[], reference=NOW)
        assert report.clean is True
        assert report.loss_streak == 0

    def test_two_losses_are_not_a_streak(self) -> None:
        """Two in a row is the most common outcome for a coin flip, and this
        codebase measured exactly 50% on its spot rules."""
        report = review(history=_losses(2), reference=NOW)
        assert Pattern.LOSING_STREAK not in report.patterns

    def test_same_size_after_a_loss_is_not_escalation(self) -> None:
        report = review(
            history=_losses(3), proposed_risk=Decimal(100), reference=NOW
        )
        assert Pattern.ESCALATION_AFTER_LOSS not in report.patterns
        assert Pattern.REVENGE not in report.patterns

    def test_a_win_breaks_the_streak(self) -> None:
        history = [
            _row(hours_ago=1, outcome="TARGET_HIT"),
            *_losses(5),
        ]
        report = review(history=history, reference=NOW)
        assert report.loss_streak == 0

    def test_old_losses_are_a_sample_not_a_run(self) -> None:
        """Otherwise the warning fires forever on a system that trades rarely."""
        stale = [
            _row(hours_ago=STREAK_WINDOW_HOURS + i + 1, outcome="STOPPED_OUT")
            for i in range(5)
        ]
        assert review(history=stale, reference=NOW).loss_streak == 0


class TestRevenge:
    def test_a_streak_is_named_even_without_escalation(self) -> None:
        report = review(history=_losses(REVENGE_LOSS_STREAK), reference=NOW)
        assert Pattern.LOSING_STREAK in report.patterns
        assert report.loss_streak == REVENGE_LOSS_STREAK

    def test_sizing_up_during_a_streak_is_revenge(self) -> None:
        report = review(
            history=_losses(3, risk="100"),
            proposed_risk=Decimal(200),
            reference=NOW,
        )
        assert Pattern.REVENGE in report.patterns
        assert any(
            "tidak mengembalikan kerugian itu" in c for c in report.as_caveats()
        )

    def test_sizing_up_after_one_loss_is_escalation_not_revenge(self) -> None:
        """Different names because they are different mistakes."""
        report = review(
            history=_losses(1, risk="100"),
            proposed_risk=Decimal(200),
            reference=NOW,
        )
        assert Pattern.ESCALATION_AFTER_LOSS in report.patterns
        assert Pattern.REVENGE not in report.patterns

    def test_a_rounding_sized_increase_is_not_escalation(self) -> None:
        """Step-size rounding moves the risk a little; that is not a decision."""
        report = review(
            history=_losses(1, risk="100"),
            proposed_risk=Decimal("105"),
            reference=NOW,
        )
        assert Pattern.ESCALATION_AFTER_LOSS not in report.patterns


class TestLeverageEscalation:
    def test_raising_leverage_after_a_loss_is_named(self) -> None:
        report = review(
            history=_losses(1, leverage=5), proposed_leverage=10, reference=NOW
        )
        assert Pattern.LEVERAGE_ESCALATION in report.patterns
        assert any("ini tidak menambah profit" in c for c in report.as_caveats())

    def test_lowering_leverage_after_a_loss_is_not(self) -> None:
        report = review(
            history=_losses(1, leverage=10), proposed_leverage=5, reference=NOW
        )
        assert Pattern.LEVERAGE_ESCALATION not in report.patterns

    def test_the_same_leverage_is_not(self) -> None:
        report = review(
            history=_losses(1, leverage=5), proposed_leverage=5, reference=NOW
        )
        assert Pattern.LEVERAGE_ESCALATION not in report.patterns


class TestOvertrading:
    def test_more_plans_than_the_horizon_offers_is_flagged(self) -> None:
        history = [_row(hours_ago=i * 0.5) for i in range(20)]
        report = review(history=history, reference=NOW, horizon_hours=4.0)
        assert Pattern.OVERTRADING in report.patterns
        assert report.plans_today == 20

    def test_the_limit_follows_the_horizon(self) -> None:
        """Six plans is fine at 4h and far too many at 1d."""
        history = [_row(hours_ago=i * 2) for i in range(6)]
        assert Pattern.OVERTRADING not in review(
            history=history, reference=NOW, horizon_hours=4.0
        ).patterns
        assert Pattern.OVERTRADING in review(
            history=history, reference=NOW, horizon_hours=24.0
        ).patterns

    def test_refusals_do_not_count_toward_the_tally(self) -> None:
        """Overtrading is about positions taken, not analyses run.

        Refusals were counted here at first - "hunting is hunting whether or
        not the gates let anything through" - which holds for a person running
        the command twenty times and fails for a scheduler. The loop planning
        every fifteen minutes flagged itself, and a warning that fires on
        ARUNA's own diligence is noise. A refusal is the gates working.
        """
        history = [
            _row(hours_ago=i * 0.5, verdict="REFUSED") for i in range(20)
        ]
        report = review(history=history, reference=NOW)
        assert report.plans_today == 0
        assert Pattern.OVERTRADING not in report.patterns

    def test_issued_plans_do_count(self) -> None:
        history = [_row(hours_ago=i * 0.5, verdict="PLAN") for i in range(20)]
        report = review(history=history, reference=NOW)
        assert report.plans_today == 20
        assert Pattern.OVERTRADING in report.patterns

    def test_yesterdays_plans_do_not_count(self) -> None:
        history = [_row(hours_ago=25 + i) for i in range(20)]
        assert review(history=history, reference=NOW).plans_today == 0


class TestItAdvisesAndNeverEncourages:
    """FUTURES SPEC 34, 50, 51. The asymmetry is the point: there is no
    counterpart to these warnings, no "you are due", no "conditions favour
    size". A discipline engine that can talk you into a position is not one."""

    def test_no_finding_ever_suggests_trading_more(self) -> None:
        reports = [
            review(history=_losses(5), proposed_risk=Decimal(500), reference=NOW),
            review(history=_losses(1, leverage=2), proposed_leverage=10, reference=NOW),
            review(
                history=[_row(hours_ago=i * 0.5) for i in range(20)], reference=NOW
            ),
        ]
        encouraging = (
            "sudah saatnya",
            "menguntungkan",
            "perbesar posisi",
            "tambah posisi",
            "you are due",
            "favour",
            "increase size",
            "add to",
            "press",
        )
        for report in reports:
            for caveat in report.as_caveats():
                lowered = caveat.lower()
                for phrase in encouraging:
                    assert phrase not in lowered, caveat

    def test_the_report_states_it_is_not_a_veto(self) -> None:
        note = review(history=_losses(5), reference=NOW).to_dict()["note"]
        assert "saran, bukan veto" in note
        assert "ada di tangan Anda" in note

    def test_findings_never_contain_a_forbidden_claim(self) -> None:
        """The plan renders these, and render() would raise on one."""
        from aruna.futures.plan import FORBIDDEN_CLAIMS

        report = review(
            history=_losses(5, risk="100", leverage=2),
            proposed_risk=Decimal(500),
            proposed_leverage=10,
            reference=NOW,
        )
        joined = " ".join(report.as_caveats()).lower()
        for claim in FORBIDDEN_CLAIMS:
            assert claim not in joined


class TestItReachesThePlan:
    """Written-but-never-called is this codebase's recurring defect."""

    def test_discipline_caveats_appear_in_the_rendered_plan(self) -> None:
        import sys

        sys.path.insert(0, "tests")
        from aruna.futures.plan import PlanVerdict, render
        from test_futures_plan import _plan

        report = review(
            history=_losses(3, risk="100"),
            proposed_risk=Decimal(500),
            reference=NOW,
        )
        assert report.findings

        plan = _plan(discipline=report)
        assert plan.verdict is PlanVerdict.PLAN, plan.refusals
        text = render(plan)
        for caveat in report.as_caveats():
            assert caveat in text

    def test_a_plan_without_a_review_is_unchanged(self) -> None:
        import sys

        sys.path.insert(0, "tests")
        from test_futures_plan import _plan

        assert _plan(discipline=None).verdict is not None


@pytest.mark.parametrize("streak", [0, 1, 2, 3, 4, 5])
def test_the_streak_threshold_can_be_crossed_in_both_directions(streak: int) -> None:
    """Guards against a constant set where nothing reaches it, and against one
    set so low it always fires."""
    report = review(history=_losses(streak), reference=NOW)
    assert report.loss_streak == streak
    assert (Pattern.LOSING_STREAK in report.patterns) == (
        streak >= REVENGE_LOSS_STREAK
    )
