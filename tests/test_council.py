"""Council: protest, rebuttal, veto, judge (PHASE 6).

The load-bearing tests here are the ones proving SPEC 16's central claim - the
judge weighs evidence, never headcount, so a minority can win - and SPEC 18's
distinction between a protest and a veto.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aruna.agents.analyst import OpinionPool
from aruna.agents.base import AgentOpinion, EvidenceRef
from aruna.agents.context import DecisionContext, MarketState
from aruna.agents.risk import assess_risk
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import CandleSeries
from aruna.core.enums import (
    AgentRole,
    CouncilRound,
    Decision,
    Horizon,
    Market,
    Stance,
    VetoReason,
    VetoReviewOutcome,
)
from aruna.council.judge import MAX_COUNCIL_CONFIDENCE, judge
from aruna.council.protest import HIGH_DISAGREEMENT, measure_disagreement, run_protest
from aruna.council.session import Council
from aruna.council.veto import (
    CRYPTO_VETO_REASONS,
    IDX_VETO_REASONS,
    Veto,
    permitted_reasons,
    review_veto,
    run_veto_stage,
)
from aruna.data.models import Candle, Provenance

NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


def _context(closes: list[float], *, market: Market = Market.CRYPTO, **state) -> DecisionContext:
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
FLAT = [100.0 + (i % 2) * 0.05 for i in range(80)]


def _opinion(role, decision, confidence, keys=(), *, horizon=Horizon.H1, reliable=True):
    return AgentOpinion(
        role=role,
        decision=decision,
        confidence=confidence,
        reasoning=("test reason",),
        evidence=tuple(EvidenceRef(key=k, reliable=reliable, sample_size=50) for k in keys),
        horizon=horizon,
    )


# ---------------------------------------------------------------------------


class TestProtestRounds:
    def test_rounds_one_to_three_always_run(self) -> None:
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.6, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.5, ["b"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        assert CouncilRound.PROTEST in outcome.rounds_run
        assert CouncilRound.REBUTTAL in outcome.rounds_run

    def test_round_four_runs_only_when_split(self) -> None:
        """SPEC 14: additional adversarial review when disagreement is high."""
        agreed = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.9, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.8, ["b"]),
            )
        )
        split = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.SELL, 0.8, ["b"]),
            )
        )
        assert CouncilRound.ADVERSARIAL_REVIEW not in run_protest(
            _context(RISING), agreed
        ).rounds_run
        assert CouncilRound.ADVERSARIAL_REVIEW in run_protest(
            _context(RISING), split
        ).rounds_run

    def test_disagreement_is_confidence_weighted(self) -> None:
        balanced = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.SELL, 0.8, ["b"]),
            )
        )
        lopsided = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.9, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.SELL, 0.1, ["b"]),
            )
        )
        assert measure_disagreement(balanced) > HIGH_DISAGREEMENT
        assert measure_disagreement(lopsided) < measure_disagreement(balanced)

    def test_shared_evidence_is_objected_to(self) -> None:
        """SPEC 17: an agent leaning on evidence others cite is not independent."""
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["reading:rsi"]),
                _opinion(AgentRole.MOMENTUM, Decision.BUY, 0.7, ["reading:rsi"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        assert any(o.ground == "shared_evidence" for o in outcome.objections)

    def test_one_defect_produces_one_objection(self) -> None:
        """Raising the same target-level complaint once per accuser charged the
        target several corrections for a single defect."""
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["reading:rsi"]),
                _opinion(AgentRole.MOMENTUM, Decision.BUY, 0.7, ["reading:rsi"]),
                _opinion(AgentRole.REVERSAL, Decision.BUY, 0.7, ["reading:rsi"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        pairs = [
            (o.target, o.ground)
            for o in outcome.objections
            if o.ground != "opposite_direction"
        ]
        assert len(pairs) == len(set(pairs))

    def test_direction_conflicts_stay_per_pair(self) -> None:
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.SELL, 0.7, ["b"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        conflicts = [o for o in outcome.objections if o.ground == "opposite_direction"]
        assert len(conflicts) == 2  # each accuses the other

    def test_agreement_on_shared_evidence_is_not_corroboration(self) -> None:
        """Agreeing from the same observation is that observation counted
        twice, not independent support."""
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["reading:rsi"]),
                _opinion(AgentRole.MOMENTUM, Decision.BUY, 0.7, ["reading:rsi"]),
            )
        )
        assert run_protest(_context(RISING), pool).supports == ()

    def test_independent_agreement_is_recorded_as_support(self) -> None:
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["reading:macd"]),
                _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.7, ["structure"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        assert outcome.supports
        assert outcome.supports[0].stance is Stance.SUPPORT

    def test_a_valid_objection_is_conceded(self) -> None:
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["reading:rsi"]),
                _opinion(AgentRole.MOMENTUM, Decision.BUY, 0.7, ["reading:rsi"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        assert any(r.conceded for r in outcome.rebuttals)

    def test_a_direction_conflict_is_defended_not_conceded(self) -> None:
        """A disagreement is not a defect - the target holds its view and lets
        the judge weigh both."""
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.7, ["a"]),
                _opinion(AgentRole.STRUCTURE, Decision.SELL, 0.7, ["b"]),
            )
        )
        outcome = run_protest(_context(RISING), pool)
        conflicts = [r for r in outcome.rebuttals if r.ground == "opposite_direction"]
        assert conflicts
        assert all(not r.conceded for r in conflicts)


class TestVeto:
    """SPEC 18: veto is for critical conditions only. PROTEST != VETO."""

    def test_permitted_grounds_differ_by_market(self) -> None:
        assert VetoReason.TRADING_HALT in IDX_VETO_REASONS
        assert VetoReason.TRADING_HALT not in CRYPTO_VETO_REASONS
        assert VetoReason.EXTREME_SPREAD in CRYPTO_VETO_REASONS
        assert permitted_reasons(Market.IDX) is IDX_VETO_REASONS

    def test_no_veto_in_ordinary_conditions(self) -> None:
        context = _context(RISING)
        assert run_veto_stage(context, assess_risk(context)).vetoes == ()

    def test_disagreement_never_raises_a_veto(self) -> None:
        """However strongly an agent disagrees, that is a protest."""
        context = _context(FLAT)
        outcome = run_veto_stage(context, assess_risk(context))
        assert not outcome.blocks

    def test_stale_data_raises_and_upholds(self) -> None:
        context = _context(FLAT, data_quality="STALE", quality_detail="90s old")
        outcome = run_veto_stage(context, assess_risk(context))
        assert outcome.vetoes
        assert outcome.blocks
        assert outcome.decision is Decision.NO_SIGNAL

    def test_closed_idx_market_raises_a_halt_veto(self) -> None:
        context = _context(FLAT, market=Market.IDX, market_open=False)
        outcome = run_veto_stage(context, assess_risk(context))
        assert any(v.reason is VetoReason.TRADING_HALT for v in outcome.vetoes)
        assert outcome.blocks

    def test_a_veto_whose_ground_is_absent_is_rejected(self) -> None:
        """SPEC 19: review overturns an unsupported veto, so a faulty probe
        cannot freeze the system."""
        context = _context(RISING)
        bogus = Veto(
            raised_by=AgentRole.RISK,
            reason=VetoReason.STALE_PRICE,
            detail="claims stale data",
            evidence_key="state.data_quality",
        )
        review = review_veto(bogus, context, assess_risk(context))
        assert review.outcome is VetoReviewOutcome.VETO_REJECTED
        assert not review.upheld

    def test_a_ground_not_permitted_for_the_market_is_rejected(self) -> None:
        context = _context(FLAT, market=Market.CRYPTO)
        halt = Veto(
            raised_by=AgentRole.RISK,
            reason=VetoReason.TRADING_HALT,
            detail="crypto never closes",
        )
        review = review_veto(halt, context, assess_risk(context))
        assert review.outcome is VetoReviewOutcome.VETO_REJECTED
        assert "bukan dasar veto yang diizinkan" in review.rationale

    def test_every_veto_gets_a_review(self) -> None:
        context = _context(FLAT, data_quality="STALE")
        outcome = run_veto_stage(context, assess_risk(context))
        assert len(outcome.reviews) == len(outcome.vetoes)


class TestJudge:
    """SPEC 16: weighed by evidence, never counted by head."""

    def _judge(self, pool: OpinionPool, context=None):
        context = context or _context(RISING)
        protest = run_protest(context, pool)
        return judge(context, pool, protest, assess_risk(context))

    def test_minority_can_beat_majority(self) -> None:
        """Three agents sharing one weak observation lose to one agent with
        strong, independent, well-sampled evidence."""
        pool = OpinionPool(
            opinions=(
                _opinion(AgentRole.TECHNICAL, Decision.SELL, 0.3, ["reading:rsi"], reliable=False),
                _opinion(AgentRole.MOMENTUM, Decision.SELL, 0.3, ["reading:rsi"], reliable=False),
                _opinion(AgentRole.REVERSAL, Decision.SELL, 0.3, ["reading:rsi"], reliable=False),
                _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.9, ["structure"]),
            )
        )
        verdict = self._judge(pool)
        assert verdict.decision is Decision.BUY
        assert verdict.minority_prevailed

    def test_headcount_alone_does_not_decide(self) -> None:
        verdict = self._judge(
            OpinionPool(
                opinions=(
                    _opinion(AgentRole.TECHNICAL, Decision.SELL, 0.1, ["a"]),
                    _opinion(AgentRole.MOMENTUM, Decision.SELL, 0.1, ["b"]),
                    _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.9, ["c"]),
                )
            )
        )
        assert verdict.decision is Decision.BUY

    def test_a_close_call_is_a_wait(self) -> None:
        verdict = self._judge(
            OpinionPool(
                opinions=(
                    _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.5, ["a"]),
                    _opinion(AgentRole.STRUCTURE, Decision.SELL, 0.5, ["b"]),
                )
            )
        )
        assert verdict.decision is Decision.WAIT
        assert verdict.confidence == 0.0

    def test_low_total_weight_is_not_full_confidence(self) -> None:
        """Weighted share alone reached 1.0 whenever only one side had any
        weight - two agents carrying 0.288 between them read as certain."""
        verdict = self._judge(
            OpinionPool(
                opinions=(
                    _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.2, ["structure"]),
                )
            )
        )
        assert verdict.confidence < 1.0

    def test_confidence_stays_a_probability(self) -> None:
        verdict = self._judge(
            OpinionPool(
                opinions=(
                    _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.95, ["a"]),
                    _opinion(AgentRole.STRUCTURE, Decision.BUY, 0.95, ["b"]),
                    _opinion(AgentRole.MOMENTUM, Decision.BUY, 0.95, ["c"]),
                )
            )
        )
        assert 0.0 <= verdict.confidence <= 1.0

    def test_a_unanimous_council_still_stops_short_of_certain(self) -> None:
        """A live run printed CONFIDENCE: 100% on five agreeing agents.

        SPEC 29 will score stated confidence against realised accuracy, so a
        published 100% can only ever be measured as overconfident - and the
        market it describes offers no such guarantee.
        """
        verdict = self._judge(
            OpinionPool(
                opinions=tuple(
                    _opinion(role, Decision.SELL, 0.95, [f"evidence_{i}"])
                    for i, role in enumerate(
                        (
                            AgentRole.TECHNICAL,
                            AgentRole.STRUCTURE,
                            AgentRole.MOMENTUM,
                            AgentRole.VOLUME,
                            AgentRole.REVERSAL,
                        )
                    )
                )
            )
        )
        assert verdict.decision is Decision.SELL
        assert verdict.confidence == MAX_COUNCIL_CONFIDENCE
        assert verdict.confidence < 1.0

    def test_the_council_ceiling_matches_the_agent_ceiling(self) -> None:
        """Weighing more evidence earns a wider range below the cap, not the
        right to exceed the one every agent is held to."""
        from aruna.agents.base import MAX_AGENT_CONFIDENCE

        assert MAX_COUNCIL_CONFIDENCE == MAX_AGENT_CONFIDENCE

    def test_unavailable_factors_are_declared(self) -> None:
        """SPEC 16 lists historical reliability and calibration. With no
        history provider they cannot be measured, and the judge must say so
        rather than quietly treating neutral as measured."""
        verdict = self._judge(
            OpinionPool(opinions=(_opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["a"]),))
        )
        assert "historical_reliability" in verdict.unavailable_factors
        assert "confidence_calibration" in verdict.unavailable_factors
        assert any(
            "outcome yang sudah selesai belum cukup" in r for r in verdict.reasoning
        )
        assert verdict.to_dict()["applied_factors"] == []

    def test_an_empty_history_is_still_unavailable(self) -> None:
        """A provider that has seen nothing must not read as "measured".

        This is the failure that would matter: wiring PHASE 8 in and having
        every decision silently claim both factors were applied, when the
        provider was answering None to every question.
        """
        from aruna.learning.history import empty_history

        pool = OpinionPool(
            opinions=(_opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["a"]),)
        )
        context = _context(RISING)
        verdict = judge(
            context,
            pool,
            run_protest(context, pool),
            assess_risk(context),
            empty_history(),
        )
        assert set(verdict.unavailable_factors) == {
            "historical_reliability",
            "confidence_calibration",
        }

    def test_no_opinions_is_a_wait(self) -> None:
        assert self._judge(OpinionPool()).decision is Decision.WAIT

    def test_every_weight_is_explained(self) -> None:
        verdict = self._judge(
            OpinionPool(
                opinions=(
                    _opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["reading:rsi"]),
                    _opinion(AgentRole.MOMENTUM, Decision.BUY, 0.8, ["reading:rsi"]),
                )
            )
        )
        assert len(verdict.weights) == 2
        assert all(w.to_dict()["weight"] is not None for w in verdict.weights)


class TestCouncilSession:
    def test_a_verdict_is_not_a_locked_signal(self) -> None:
        """Reaching a conclusion and publishing a prediction are separate acts.

        The lock lives in ``aruna.signals`` and can decline the verdict - too
        little confidence, or evidence too old for the horizon. A verdict that
        described itself as a signal would paper over that gap.
        """
        verdict = Council().convene(_context(RISING))
        assert verdict.is_locked_signal is False
        payload = verdict.to_dict()
        assert payload["is_locked_signal"] is False
        assert "bukan signal terkunci" in payload["phase_note"]
        assert "aruna.signals" in payload["phase_note"]

    def test_all_four_stages_are_recorded(self) -> None:
        verdict = Council().convene(_context(RISING))
        assert verdict.protest is not None
        assert verdict.veto is not None
        assert verdict.judgement is not None
        assert verdict.no_trade is not None

    def test_upheld_veto_overrides_the_judge(self) -> None:
        verdict = Council().convene(_context(RISING, data_quality="STALE"))
        assert verdict.veto.blocks
        assert verdict.decision is Decision.NO_SIGNAL
        assert verdict.confidence == 0.0

    def test_confidence_stays_a_probability(self) -> None:
        for series in (RISING, FLAT):
            verdict = Council().convene(_context(series))
            assert 0.0 <= verdict.confidence <= 1.0

    def test_no_trade_gate_runs_after_the_verdict(self) -> None:
        """A well-argued decision on untrustworthy data is still refused."""
        verdict = Council().convene(_context(RISING, data_quality="STALE"))
        assert not verdict.decision.is_directional

    def test_serialisation_round_trips(self) -> None:
        payload = Council().convene(_context(RISING)).to_dict()
        assert payload["decision"] in {d.value for d in Decision}
        assert "judgement" in payload
        assert "protest" in payload
        assert "veto" in payload
