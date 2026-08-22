"""Model governance (PHASE 10).

The load-bearing test in this file is the one asserting that **ARUNA cannot
approve a change to itself**. Everything else here is a supporting argument for
why that gate is worth having: the validation bar, the multiple-comparisons
correction, the shadow model that admits when it has learned nothing.

A governance layer whose gate opens for a sufficiently good number is not a
gate, so there is a test that goes looking for a way through and asserts there
isn't one.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aruna.core.enums import Decision
from aruna.governance.approval import (
    FORBIDDEN_ACTORS,
    approve,
    reject,
    submit_for_approval,
)
from aruna.governance.drift import (
    ACCURACY_DRIFT_POINTS,
    MIN_WINDOW_SAMPLE,
    Window,
    detect,
)
from aruna.governance.proposal import (
    MIN_EFFECT_POINTS,
    MIN_VALIDATION_SAMPLE,
    ApprovalError,
    Arm,
    ModelProposal,
    ProposalStatus,
    Verdict,
    ready_for_approval,
    required_sigma,
    validate,
)
from aruna.governance.research import (
    QuestionSource,
    questions_from_backtest,
    questions_from_calibration,
    questions_from_objections,
    rank,
)
from aruna.governance.shadow import (
    MIN_DISAGREEMENT_RATE,
    ShadowDecision,
    compare,
)

NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _proposal(**overrides) -> ModelProposal:
    base = {
        "key": "exit-at-target",
        "title": "Exit at the target instead of holding to horizon end",
        "hypothesis": "Targets are set at 3.6% and realised at 0.19%",
        "change": "close the position when the target is touched",
    }
    return ModelProposal(**(base | overrides))


def _validated(**overrides) -> ModelProposal:
    """A proposal whose evidence cleared every bar."""
    validation = validate(
        Arm("baseline", resolved=500, correct=250),
        Arm("variant", resolved=500, correct=300),
        variants_tested=1,
        out_of_sample=True,
    )
    assert validation.verdict is Verdict.IMPROVED
    return _proposal(
        validation=validation, status=ProposalStatus.VALIDATED, **overrides
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class TestHumanApprovalGate:
    """SPEC 44. These must never be relaxed."""

    def test_the_system_cannot_approve_itself(self) -> None:
        proposal = _validated()
        for actor in FORBIDDEN_ACTORS:
            with pytest.raises(ApprovalError) as exc:
                approve(proposal, actor=actor)
            assert "not a person" in str(exc.value)

    def test_no_function_approves_without_an_actor(self) -> None:
        """There is no overload, default or flag that supplies one."""
        with pytest.raises(TypeError):
            approve(_validated())  # type: ignore[call-arg]

    def test_a_named_person_can_approve_validated_evidence(self) -> None:
        decided = approve(_validated(), actor="rowan", note="agreed", at=NOW)
        assert decided.status is ProposalStatus.APPROVED
        assert decided.decided_by == "rowan"
        assert decided.decided_at == NOW
        assert decided.is_active

    def test_approval_is_refused_when_the_evidence_did_not_clear(self) -> None:
        """A person may still be wrong, but not by accident: they cannot
        approve on a number the system has already called noise."""
        weak = validate(
            Arm("baseline", resolved=500, correct=250),
            Arm("variant", resolved=500, correct=256),
            out_of_sample=True,
        )
        assert weak.verdict is not Verdict.IMPROVED
        with pytest.raises(ApprovalError) as exc:
            approve(_proposal(validation=weak), actor="rowan")
        assert "insufficient" in str(exc.value).lower()

    def test_an_unvalidated_proposal_cannot_be_approved(self) -> None:
        with pytest.raises(ApprovalError, match="not validated"):
            approve(_proposal(), actor="rowan")

    def test_rejection_needs_a_person_but_no_statistics(self) -> None:
        """Stopping a change must never be harder than making one."""
        decided = reject(_proposal(), actor="rowan", note="not convinced", at=NOW)
        assert decided.status is ProposalStatus.REJECTED
        assert decided.decided_by == "rowan"

    def test_rejection_still_refuses_an_anonymous_actor(self) -> None:
        with pytest.raises(ApprovalError, match="not a person"):
            reject(_proposal(), actor="system")

    def test_an_approved_change_is_reversed_by_a_new_proposal(self) -> None:
        approved = approve(_validated(), actor="rowan", at=NOW)
        with pytest.raises(ApprovalError) as exc:
            reject(approved, actor="rowan")
        assert "new proposal" in str(exc.value)

    def test_submitting_for_approval_is_not_approving(self) -> None:
        submitted = submit_for_approval(_validated())
        assert submitted.status is ProposalStatus.AWAITING_APPROVAL
        assert submitted.is_active is False
        assert submitted.decided_by is None


# ---------------------------------------------------------------------------
# SPEC 44 - the validation bar
# ---------------------------------------------------------------------------


class TestValidation:
    def test_a_thin_comparison_reports_nothing(self) -> None:
        result = validate(
            Arm("baseline", resolved=30, correct=15),
            Arm("variant", resolved=30, correct=22),
            out_of_sample=True,
        )
        assert result.verdict is Verdict.INSUFFICIENT_SAMPLE
        assert result.supports_approval is False
        assert f"{MIN_VALIDATION_SAMPLE} needed" in result.reasons[-1]

    def test_a_tiny_improvement_is_not_worth_the_risk(self) -> None:
        result = validate(
            Arm("baseline", resolved=1000, correct=500),
            Arm("variant", resolved=1000, correct=510),  # +1 point
            out_of_sample=True,
        )
        assert result.verdict is Verdict.NO_IMPROVEMENT
        assert result.effect_points < MIN_EFFECT_POINTS

    def test_an_effect_inside_the_noise_is_named_as_such(self) -> None:
        result = validate(
            Arm("baseline", resolved=150, correct=75),
            Arm("variant", resolved=150, correct=86),  # +7 points, small n
            out_of_sample=True,
        )
        assert result.verdict is Verdict.WITHIN_NOISE
        assert "standard errors" in result.reasons[-1]

    def test_a_real_effect_out_of_sample_clears(self) -> None:
        result = validate(
            Arm("baseline", resolved=500, correct=250),
            Arm("variant", resolved=500, correct=300),
            out_of_sample=True,
        )
        assert result.verdict is Verdict.IMPROVED
        assert result.supports_approval is True

    def test_an_in_sample_result_never_clears(self) -> None:
        """However good it looks, it cannot rule out having been fitted."""
        result = validate(
            Arm("baseline", resolved=500, correct=250),
            Arm("variant", resolved=500, correct=350),
            out_of_sample=False,
        )
        assert result.verdict is not Verdict.IMPROVED
        assert any("reserved from tuning" in r for r in result.reasons)

    def test_the_bar_rises_with_the_number_of_variants_tried(self) -> None:
        """Test twenty variants and one will look good. That is the most
        common way a backtest lies to the person running it."""
        assert required_sigma(1) < required_sigma(10) < required_sigma(100)
        assert required_sigma(1) == pytest.approx(2.0)

    def test_the_same_effect_fails_once_enough_variants_were_tried(self) -> None:
        baseline = Arm("baseline", resolved=400, correct=200)
        variant = Arm("variant", resolved=400, correct=232)  # +8 points

        first = validate(baseline, variant, variants_tested=1, out_of_sample=True)
        fiftieth = validate(baseline, variant, variants_tested=50, out_of_sample=True)

        assert first.verdict is Verdict.IMPROVED
        assert fiftieth.verdict is Verdict.WITHIN_NOISE
        assert fiftieth.required_sigma > first.required_sigma

    def test_a_pnl_change_the_accuracy_test_cannot_see_is_reported(self) -> None:
        """The first real proposal tested scored identically on accuracy and
        lost an extra 463,540 IDR. A verdict of "no effect" would have been
        true about the wrong quantity."""
        result = validate(
            Arm("baseline", resolved=580, correct=291, net_pnl="-2953814"),
            Arm("variant", resolved=580, correct=291, net_pnl="-3417354"),
            out_of_sample=True,
        )
        assert result.verdict is Verdict.NO_IMPROVEMENT
        assert any("WORSE" in r for r in result.reasons)
        assert any("463,540" in r for r in result.reasons)

    def test_an_identical_pnl_adds_no_note(self) -> None:
        result = validate(
            Arm("baseline", resolved=500, correct=250, net_pnl="100"),
            Arm("variant", resolved=500, correct=300, net_pnl="100"),
            out_of_sample=True,
        )
        assert not any("net PnL" in r for r in result.reasons)

    def test_the_verdict_never_recommends(self) -> None:
        result = validate(
            Arm("baseline", resolved=500, correct=250),
            Arm("variant", resolved=500, correct=300),
            out_of_sample=True,
        )
        note = result.to_dict()["note"]
        assert "not a recommendation" in note
        assert "no change activates without an explicit human decision" in note

    def test_ready_for_approval_explains_a_refusal(self) -> None:
        allowed, reason = ready_for_approval(_proposal())
        assert allowed is False
        assert "not validated" in reason


# ---------------------------------------------------------------------------
# SPEC 31 - research questions
# ---------------------------------------------------------------------------


class TestResearch:
    def test_costs_exceeding_the_edge_raises_a_question(self) -> None:
        questions = questions_from_backtest(
            {
                "interval": "1h",
                "combined": {
                    "resolved": 218,
                    "direction_correct": 89,
                    "direction_accuracy": 0.41,
                    "paper_trades": {
                        "trades": 218,
                        "cost_ratio": 132.7,
                        "net_pnl": "-1514560.13",
                        "gross_pnl": "11502.89",
                    },
                },
            }
        )
        keys = {q.key for q in questions}
        assert "costs_exceed_edge_1h" in keys
        cost_question = next(q for q in questions if q.key == "costs_exceed_edge_1h")
        assert cost_question.source is QuestionSource.BACKTEST
        assert any("rasio ongkos" in e for e in cost_question.evidence)

    def test_accuracy_at_chance_is_the_most_severe_question(self) -> None:
        questions = questions_from_backtest(
            {
                "interval": "1d",
                "combined": {
                    "resolved": 580,
                    "direction_correct": 290,
                    "direction_accuracy": 0.50,
                    "paper_trades": {"trades": 580, "cost_ratio": 3.67},
                },
            }
        )
        question = next(q for q in questions if "no_measurable_edge" in q.key)
        assert question.severity == 1.0
        assert any("edge-nya memang nol" in e for e in question.evidence)

    def test_a_small_backtest_raises_no_accuracy_question(self) -> None:
        questions = questions_from_backtest(
            {
                "interval": "1h",
                "combined": {
                    "resolved": 20,
                    "direction_correct": 6,
                    "direction_accuracy": 0.30,
                    "paper_trades": {"trades": 20},
                },
            }
        )
        assert not any("accuracy_below_chance" in q.key for q in questions)

    def test_an_empty_backtest_raises_nothing(self) -> None:
        assert questions_from_backtest({"combined": {"resolved": 0}}) == []

    def test_a_calibration_gap_raises_a_question(self) -> None:
        questions = questions_from_calibration(
            {
                "buckets": [
                    {
                        "bucket": "80-96%",
                        "predictions": 40,
                        "accuracy": 0.45,
                        "mean_confidence": 0.86,
                        "gap": 0.41,
                    },
                    {
                        "bucket": "50-65%",
                        "predictions": 40,
                        "accuracy": 0.56,
                        "mean_confidence": 0.57,
                        "gap": 0.01,
                    },
                ]
            }
        )
        assert len(questions) == 1
        assert "terlalu percaya diri" in questions[0].question

    def test_an_unmeasured_bucket_raises_nothing(self) -> None:
        assert (
            questions_from_calibration(
                {"buckets": [{"bucket": "50-65%", "predictions": 4, "gap": None}]}
            )
            == []
        )

    def test_a_repeatedly_vindicated_objection_raises_a_question(self) -> None:
        questions = questions_from_objections(
            [
                {
                    "accuser": "REVERSAL",
                    "ground": "overbought",
                    "raised_and_overruled": 30,
                    "vindicated": 22,
                    "vindication_rate": 0.73,
                },
                {
                    "accuser": "NEWS",
                    "ground": "stale_news",
                    "raised_and_overruled": 4,
                    "vindicated": 4,
                    "vindication_rate": 1.0,
                },
            ]
        )
        # The second is 100% right but on 4 samples: not a pattern.
        assert len(questions) == 1
        assert "REVERSAL" in questions[0].question

    def test_questions_are_ranked_and_deduplicated(self) -> None:
        questions = questions_from_backtest(
            {
                "interval": "1h",
                "combined": {
                    "resolved": 200,
                    "direction_correct": 80,
                    "direction_accuracy": 0.40,
                    "paper_trades": {"trades": 200, "cost_ratio": 50.0},
                },
            }
        )
        ordered = rank(questions + questions)
        assert len(ordered) == len(set(q.key for q in ordered))
        assert ordered == sorted(ordered, key=lambda q: q.severity, reverse=True)

    async def test_objection_patterns_reach_the_research_run(self) -> None:
        """SPEC 26 was reachable but never reached.

        `questions_from_objections` existed, was exported and was tested, and
        the service never called it - so the most specific signal the record
        produces (this agent, this ground, repeatedly right) never became a
        question.
        """
        from aruna.governance.service import GovernanceService

        class Learning:
            async def latest_calibration(self):
                return None

            async def autopsies(self, limit=200):
                return []

            async def overruled_objections(self):
                return [
                    {
                        "accuser": "REVERSAL",
                        "ground": "overbought",
                        "direction_correct": False,
                    }
                ] * 20 + [
                    {
                        "accuser": "REVERSAL",
                        "ground": "overbought",
                        "direction_correct": True,
                    }
                ] * 5

        class Store:
            def __init__(self):
                self.recorded = []
                self.closed_with = None

            async def record_question(self, question):
                self.recorded.append(question)

            async def close_absent(self, keys):
                self.closed_with = keys
                return 0

        store = Store()
        service = GovernanceService(store=store, learning=Learning())
        result = await service.research()

        keys = {q.key for q in result.questions}
        assert "objection_reversal_overbought" in keys
        assert store.recorded

    async def test_a_question_the_record_stopped_raising_is_closed(self) -> None:
        """Otherwise a solved problem is reported as outstanding forever."""
        from aruna.governance.service import GovernanceService

        class Learning:
            async def latest_calibration(self):
                return None

            async def autopsies(self, limit=200):
                return []

            async def overruled_objections(self):
                return []

        class Store:
            def __init__(self):
                self.closed_with = None

            async def record_question(self, question):
                pass

            async def close_absent(self, keys):
                self.closed_with = keys
                return 3

        store = Store()
        await GovernanceService(store=store, learning=Learning()).research()
        assert store.closed_with == []

    def test_a_question_says_it_is_not_a_finding(self) -> None:
        questions = questions_from_backtest(
            {
                "interval": "1h",
                "combined": {
                    "resolved": 200,
                    "direction_correct": 80,
                    "direction_accuracy": 0.40,
                    "paper_trades": {"trades": 200, "cost_ratio": 50.0},
                },
            }
        )
        assert "not a finding" in questions[0].to_dict()["note"]


# ---------------------------------------------------------------------------
# Shadow models
# ---------------------------------------------------------------------------


class TestShadow:
    def _decision(self, i: int, *, agree: bool, shadow_right: bool = True):
        return ShadowDecision(
            signal_id=f"shadow{i:010d}",
            symbol="BTC/USDT",
            live=Decision.BUY,
            shadow=Decision.BUY if agree else Decision.SELL,
            live_correct=not shadow_right if not agree else True,
            shadow_correct=shadow_right if not agree else True,
        )

    def test_a_shadow_that_mostly_agrees_has_shown_nothing(self) -> None:
        """The trap: 497 agreements read as reassurance while the comparison
        rests on 3 observations."""
        decisions = [self._decision(i, agree=True) for i in range(497)]
        decisions += [self._decision(i, agree=False) for i in range(3)]
        result = compare("variant-a", decisions)

        assert result.disagreement_rate < MIN_DISAGREEMENT_RATE
        assert "INDISTINGUISHABLE" in result.verdict
        assert "rests on those few cases alone" in result.verdict

    def test_a_meaningful_divergence_is_compared(self) -> None:
        decisions = [self._decision(i, agree=True) for i in range(70)]
        decisions += [
            self._decision(100 + i, agree=False, shadow_right=True) for i in range(30)
        ]
        result = compare("variant-a", decisions)

        assert result.disagreement_rate >= MIN_DISAGREEMENT_RATE
        assert result.shadow_wins == 30
        assert result.live_wins == 0

    def test_unresolved_disagreements_are_not_counted_as_wins(self) -> None:
        decisions = [
            ShadowDecision(
                signal_id=f"s{i}",
                symbol="BTC/USDT",
                live=Decision.BUY,
                shadow=Decision.SELL,
            )
            for i in range(20)
        ]
        result = compare("variant-a", decisions)
        assert result.shadow_wins == 0
        assert "PENDING" in result.verdict

    def test_no_shadow_run_says_so(self) -> None:
        assert "NO SHADOW DECISIONS" in compare("variant-a", []).verdict

    def test_the_note_says_only_disagreements_carry_information(self) -> None:
        result = compare("variant-a", [self._decision(1, agree=True)])
        assert "only the disagreements carry information" in result.to_dict()["note"]


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


class TestDrift:
    def test_a_short_window_makes_no_claim(self) -> None:
        report = detect(
            Window("before", resolved=20, correct=12),
            Window("recent", resolved=10, correct=3),
        )
        assert not report.sufficient
        assert "INSUFFICIENT SAMPLE" in report.verdict
        assert "cries wolf gets ignored" in report.verdict

    def test_stable_behaviour_reports_no_drift(self) -> None:
        report = detect(
            Window("before", resolved=200, correct=110),
            Window("recent", resolved=100, correct=54),
        )
        assert report.sufficient
        assert "NO DRIFT DETECTED" in report.verdict

    def test_a_real_accuracy_fall_is_performance_drift(self) -> None:
        report = detect(
            Window("before", resolved=200, correct=120),  # 60%
            Window("recent", resolved=100, correct=40),  # 40%
        )
        assert "PERFORMANCE DRIFT" in report.verdict
        assert report.performance_drift <= -ACCURACY_DRIFT_POINTS

    def test_a_changed_market_is_condition_drift_not_a_rules_problem(self) -> None:
        report = detect(
            Window(
                "before",
                resolved=200,
                correct=110,
                regimes={"TRENDING": 180, "RANGING": 20},
            ),
            Window(
                "recent",
                resolved=100,
                correct=54,
                regimes={"TRENDING": 20, "RANGING": 80},
            ),
        )
        assert "CONDITION DRIFT" in report.verdict
        assert "PERFORMANCE DRIFT" not in report.verdict

    def test_the_two_kinds_are_kept_apart(self) -> None:
        note = detect(
            Window("before", resolved=MIN_WINDOW_SAMPLE, correct=30),
            Window("recent", resolved=MIN_WINDOW_SAMPLE, correct=30),
        ).to_dict()["note"]
        assert "points at the rules" in note
        assert "points at the market" in note
