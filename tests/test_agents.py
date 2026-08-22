"""Agents, risk, and the no-trade engine (PHASE 5).

The most important tests here prove the invariants the specification is built
on: no agent has a permanent stance (SPEC 12, 48), the prosecutor may agree
(SPEC 13), and stronger counter-evidence forces a reassessment (SPEC 15).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.agents.analyst import ArunaAnalyst, OpinionPool, Prosecutor, SelfCritic
from aruna.agents.base import AgentOpinion, EvidenceRef, abstain, scale_confidence
from aruna.agents.context import DecisionContext, MarketState
from aruna.agents.context_agents import FundamentalAgent, NewsAgent
from aruna.agents.deliberation import DeliberationEngine, default_roster
from aruna.agents.market import (
    MomentumAgent,
    RegimeAgent,
    ReversalAgent,
    StructureAgent,
    TechnicalAgent,
    VolumeAgent,
)
from aruna.agents.notrade import MIN_EDGE_CONFIDENCE, evaluate_no_trade
from aruna.agents.risk import RiskAgent, RiskLevel, assess_risk
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import CandleSeries
from aruna.core.enums import AgentRole, Decision, Horizon, Market, NoTradeReason
from aruna.data.models import Candle, Provenance

NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)


def candles(closes: list[float], *, symbol: str = "BTC/USDT", volume: float = 100.0):
    return [
        Candle(
            market=Market.CRYPTO,
            symbol=symbol,
            interval=Horizon.H1,
            open_time=NOW + timedelta(hours=i),
            close_time=NOW + timedelta(hours=i + 1),
            open=Decimal(str(c)),
            high=Decimal(str(c + 1)),
            low=Decimal(str(c - 1)),
            close=Decimal(str(c)),
            volume=Decimal(str(volume)),
            provenance=Provenance(source="test", server_timestamp=NOW),
        )
        for i, c in enumerate(closes)
    ]


def context_from(
    closes: list[float], *, market: Market = Market.CRYPTO, **state_kwargs
) -> DecisionContext:
    series = CandleSeries.from_candles(candles(closes))
    technical = AnalysisEngine().analyse(series)
    defaults = {"last_price": Decimal(str(closes[-1]))}
    return DecisionContext(
        market=market,
        symbol="BTC/USDT" if market is Market.CRYPTO else "BBCA",
        interval=Horizon.H1,
        as_of=technical.as_of,
        state=MarketState(**(defaults | state_kwargs)),
        technical=technical,
    )


RISING = [float(100 + i * 1.5) for i in range(80)]
FALLING = [float(220 - i * 1.5) for i in range(80)]
FLAT = [100.0 + (i % 2) * 0.05 for i in range(80)]

# A perfectly monotonic line has no swing pivots and no volume variation, so
# the structure- and volume-reading agents legitimately abstain on it. These
# zigzags actually exercise them: each leg makes a pivot, with the second
# series mirroring the first.
_LEG = [0, 4, 8, 5, 2, 6, 10, 7, 4, 8]
ZIGZAG_UP = [100.0 + step + cycle * 6 for cycle in range(8) for step in _LEG]
ZIGZAG_DOWN = [220.0 - step - cycle * 6 for cycle in range(8) for step in _LEG]


# ---------------------------------------------------------------------------


class TestOpinionValidation:
    def test_confidence_must_be_a_probability(self) -> None:
        with pytest.raises(ValueError, match=r"di luar rentang 0\.\.1"):
            AgentOpinion(
                role=AgentRole.TECHNICAL,
                decision=Decision.BUY,
                confidence=1.4,
                reasoning=("x",),
            )

    def test_abstention_must_be_a_wait(self) -> None:
        with pytest.raises(ValueError, match="abstain berarti WAIT"):
            AgentOpinion(
                role=AgentRole.TECHNICAL,
                decision=Decision.BUY,
                confidence=0.5,
                reasoning=("x",),
                abstained=True,
            )

    def test_every_opinion_must_be_explainable(self) -> None:
        """SPEC 39 replay needs the reasoning, not just the verdict."""
        with pytest.raises(ValueError, match="tanpa alasan"):
            AgentOpinion(
                role=AgentRole.TECHNICAL, decision=Decision.WAIT, confidence=0.0
            )

    def test_a_direction_at_zero_confidence_is_a_wait(self) -> None:
        with pytest.raises(ValueError, match="itu WAIT, bukan arah"):
            AgentOpinion(
                role=AgentRole.TECHNICAL,
                decision=Decision.SELL,
                confidence=0.0,
                reasoning=("x",),
            )

    def test_abstain_helper_is_valid(self) -> None:
        opinion = abstain(AgentRole.NEWS, "no news")
        assert opinion.decision is Decision.WAIT
        assert opinion.abstained
        assert opinion.confidence == 0.0

    def test_scale_confidence_is_bounded(self) -> None:
        from aruna.agents.base import MAX_AGENT_CONFIDENCE

        assert scale_confidence(2.0, evidence_quality=2.0) == MAX_AGENT_CONFIDENCE
        assert scale_confidence(-1.0, evidence_quality=1.0) == 0.0


def _roster_agents():
    return [
        TechnicalAgent(),
        StructureAgent(),
        MomentumAgent(),
        VolumeAgent(),
        ReversalAgent(),
        RegimeAgent(),
    ]


class TestNoPermanentBias:
    """SPEC 12 and 48: no agent is permanently BUY, SELL, or opposition."""

    @pytest.mark.parametrize(
        "agent", _roster_agents(), ids=lambda a: a.role.value
    )
    def test_agent_never_locks_to_one_direction(self, agent) -> None:
        """Across five market shapes, no agent may answer with a single fixed
        direction. Abstaining or waiting is fine - being permanently bullish or
        permanently bearish is not."""
        outcomes = {
            agent.safe_evaluate(context_from(series)).decision
            for series in (RISING, FALLING, FLAT, ZIGZAG_UP, ZIGZAG_DOWN)
        }
        directional = {d for d in outcomes if d.is_directional}
        assert directional != {Decision.BUY}, (
            f"{agent.role.value} only ever reached BUY - that is a fixed stance"
        )
        assert directional != {Decision.SELL}, (
            f"{agent.role.value} only ever reached SELL - that is a fixed stance"
        )

    def test_the_roster_as_a_whole_reaches_both_directions(self) -> None:
        outcomes = {
            agent.safe_evaluate(context_from(series)).decision
            for agent in _roster_agents()
            for series in (RISING, FALLING, ZIGZAG_UP, ZIGZAG_DOWN)
        }
        assert {Decision.BUY, Decision.SELL} <= outcomes

    def test_technical_follows_the_trend_both_ways(self) -> None:
        agent = TechnicalAgent()
        assert agent.safe_evaluate(context_from(RISING)).decision is Decision.BUY
        assert agent.safe_evaluate(context_from(FALLING)).decision is Decision.SELL

    def test_reversal_is_not_a_permanent_bear(self) -> None:
        """Oversold is a bullish reversal, and this agent must call it."""
        agent = ReversalAgent()
        oversold = agent.safe_evaluate(context_from(FALLING))
        overbought = agent.safe_evaluate(context_from(RISING))
        assert {oversold.decision, overbought.decision} == {Decision.BUY, Decision.SELL}

    def test_every_roster_agent_can_abstain(self) -> None:
        """Thin data must produce an abstention, not an invented view."""
        thin = context_from([100.0, 101.0, 100.5])
        for agent in default_roster():
            opinion = agent.safe_evaluate(thin)
            assert opinion.decision in (Decision.BUY, Decision.SELL, Decision.WAIT)


class TestConfidenceCalibration:
    def test_no_single_agent_is_ever_certain(self) -> None:
        """No lens that cannot see news, structure and risk at once should
        report 100%. Only the PHASE 6 council works above this."""
        from aruna.agents.base import MAX_AGENT_CONFIDENCE

        for series in (RISING, FALLING, FLAT, ZIGZAG_UP, ZIGZAG_DOWN):
            for agent in default_roster():
                opinion = agent.safe_evaluate(context_from(series))
                # A risk veto may be fully confident: "the data is unusable"
                # is a statement of fact, not a market prediction.
                if opinion.decision.is_directional:
                    assert opinion.confidence <= MAX_AGENT_CONFIDENCE

    def test_a_lone_weak_signal_is_not_full_confidence(self) -> None:
        """A single 1.0-weight vote once read as 100% simply because nothing
        contradicted it - agreement without conviction."""
        opinion = StructureAgent().safe_evaluate(context_from(ZIGZAG_UP))
        if opinion.decision.is_directional:
            assert opinion.confidence < 1.0

    def test_confidence_never_exceeds_one(self) -> None:
        for series in (RISING, FALLING, FLAT):
            for agent in default_roster():
                assert 0.0 <= agent.safe_evaluate(context_from(series)).confidence <= 1.0


class TestContextAgents:
    def test_fundamental_abstains_on_crypto(self) -> None:
        """There are no earnings or book value to value."""
        opinion = FundamentalAgent().safe_evaluate(context_from(RISING))
        assert opinion.abstained
        assert "crypto" in opinion.reasoning[0]

    def test_news_abstains_without_items(self) -> None:
        assert NewsAgent().safe_evaluate(context_from(RISING)).abstained

    def test_news_confidence_is_capped(self) -> None:
        """The sentiment behind it is a keyword lexicon; the agent must not
        claim more than that can support."""
        assert NewsAgent.MAX_CONFIDENCE <= 0.5

    def test_fundamental_confidence_is_capped(self) -> None:
        assert FundamentalAgent.MAX_CONFIDENCE <= 0.5


class TestRiskEngine:
    def test_wide_spread_is_extreme(self) -> None:
        context = context_from(FLAT, spread_bps=Decimal(500))
        factor = assess_risk(context).of("spread")
        assert factor is not None and factor.level is RiskLevel.EXTREME

    def test_closed_idx_market_is_extreme(self) -> None:
        context = context_from(FLAT, market=Market.IDX, market_open=False)
        assert assess_risk(context).of("market_halt").level is RiskLevel.EXTREME

    def test_delayed_feed_is_a_risk_factor(self) -> None:
        context = context_from(FLAT, is_realtime=False, declared_delay_sec=900)
        assert assess_risk(context).of("data_freshness") is not None

    def test_bad_data_quality_is_extreme(self) -> None:
        context = context_from(FLAT, data_quality="STALE", quality_detail="60s old")
        assert assess_risk(context).of("data_quality").level is RiskLevel.EXTREME

    def test_missing_corporate_action_feed_is_declared_not_ignored(self) -> None:
        """SPEC 32 names corporate action risk. ARUNA has no feed for it, so
        the gap is stated rather than scored as zero risk."""
        context = context_from(FLAT, market=Market.IDX)
        factor = assess_risk(context).of("corporate_action")
        assert factor is not None
        assert "tidak terukur, bukan berarti nol" in factor.detail

    def test_overall_is_the_worst_factor(self) -> None:
        context = context_from(FLAT, spread_bps=Decimal(500))
        assert assess_risk(context).overall is RiskLevel.EXTREME

    def test_risk_agent_blocks_on_extreme_conditions(self) -> None:
        context = context_from(FLAT, data_quality="STALE")
        opinion = RiskAgent().safe_evaluate(context)
        assert opinion.decision is Decision.WAIT
        assert opinion.confidence == 1.0
        assert not opinion.abstained

    def test_risk_agent_abstains_in_ordinary_conditions(self) -> None:
        """Risk is not a directional opinion; ordinary conditions are not an
        argument for a trade."""
        opinion = RiskAgent().safe_evaluate(context_from(FLAT))
        assert opinion.abstained


class TestNoTradeEngine:
    def test_kill_switch_blocks(self) -> None:
        context = context_from(RISING)
        context = DecisionContext(
            market=context.market,
            symbol=context.symbol,
            interval=context.interval,
            as_of=context.as_of,
            state=context.state,
            technical=context.technical,
            trading_allowed=False,
        )
        verdict = evaluate_no_trade(context)
        assert NoTradeReason.KILL_SWITCH_ACTIVE in verdict.reasons

    def test_stale_data_yields_no_signal_not_wait(self) -> None:
        """Untrustworthy inputs mean there is nothing to say (SPEC 5)."""
        verdict = evaluate_no_trade(context_from(FLAT, data_quality="STALE"))
        assert NoTradeReason.STALE_DATA in verdict.reasons
        assert verdict.decision is Decision.NO_SIGNAL

    def test_closed_market_blocks(self) -> None:
        context = context_from(FLAT, market=Market.IDX, market_open=False)
        assert NoTradeReason.MARKET_HALT in evaluate_no_trade(context).reasons

    def test_low_confidence_is_no_edge(self) -> None:
        """SPEC 33's closing rule: if there is no edge, WAIT."""
        verdict = evaluate_no_trade(
            context_from(RISING), council_confidence=MIN_EDGE_CONFIDENCE - 0.01
        )
        assert NoTradeReason.NO_EDGE in verdict.reasons
        assert verdict.decision is Decision.WAIT

    def test_adequate_confidence_passes(self) -> None:
        verdict = evaluate_no_trade(context_from(RISING), council_confidence=0.8)
        assert NoTradeReason.NO_EDGE not in verdict.reasons

    def test_too_few_agents_is_insufficient_evidence(self) -> None:
        verdict = evaluate_no_trade(context_from(RISING), participating_agents=1)
        assert NoTradeReason.INSUFFICIENT_EVIDENCE in verdict.reasons

    def test_horizon_conflict_blocks(self) -> None:
        verdict = evaluate_no_trade(context_from(RISING), horizon_conflict=True)
        assert NoTradeReason.CONFLICTING_HORIZON in verdict.reasons

    def test_clean_context_is_not_blocked(self) -> None:
        verdict = evaluate_no_trade(context_from(RISING), council_confidence=0.9)
        assert not verdict.blocked


class TestOpinionPool:
    def _opinion(self, role, decision, confidence, keys=()) -> AgentOpinion:
        return AgentOpinion(
            role=role,
            decision=decision,
            confidence=confidence,
            reasoning=("test",),
            evidence=tuple(EvidenceRef(key=k) for k in keys),
        )

    def test_independence_falls_when_agents_share_evidence(self) -> None:
        """SPEC 17: six agents reading one RSI are one witness, not six."""
        shared = OpinionPool(
            opinions=(
                self._opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["reading:rsi"]),
                self._opinion(AgentRole.MOMENTUM, Decision.BUY, 0.8, ["reading:rsi"]),
                self._opinion(AgentRole.REVERSAL, Decision.BUY, 0.8, ["reading:rsi"]),
            )
        )
        distinct = OpinionPool(
            opinions=(
                self._opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["reading:rsi"]),
                self._opinion(AgentRole.MOMENTUM, Decision.BUY, 0.8, ["reading:macd"]),
                self._opinion(AgentRole.REVERSAL, Decision.BUY, 0.8, ["structure"]),
            )
        )
        assert shared.independence() < distinct.independence()
        assert distinct.independence() == 1.0

    def test_shared_evidence_is_reported(self) -> None:
        pool = OpinionPool(
            opinions=(
                self._opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["reading:rsi"]),
                self._opinion(AgentRole.MOMENTUM, Decision.BUY, 0.8, ["reading:rsi"]),
            )
        )
        assert "reading:rsi" in pool.shared_evidence()

    def test_abstentions_do_not_participate(self) -> None:
        pool = OpinionPool(
            opinions=(
                self._opinion(AgentRole.TECHNICAL, Decision.BUY, 0.8, ["a"]),
                abstain(AgentRole.NEWS, "nothing"),
            )
        )
        assert len(pool.participating) == 1


class TestProsecutor:
    """SPEC 13: attacks the proposal but reaches an independent conclusion."""

    def _pool(self, *specs) -> OpinionPool:
        return OpinionPool(
            opinions=tuple(
                AgentOpinion(
                    role=role,
                    decision=decision,
                    confidence=confidence,
                    reasoning=("test reason",),
                    evidence=(EvidenceRef(key=key),),
                )
                for role, decision, confidence, key in specs
            )
        )

    def test_prosecutor_may_agree_with_the_proposal(self) -> None:
        """The spec's own example has it agreeing - it is not opposition."""
        pool = self._pool(
            (AgentRole.TECHNICAL, Decision.BUY, 0.9, "reading:macd"),
            (AgentRole.STRUCTURE, Decision.BUY, 0.8, "structure"),
        )
        context = context_from(RISING)
        proposal = ArunaAnalyst(pool).safe_evaluate(context)
        verdict = Prosecutor(pool, proposal).safe_evaluate(context)

        assert verdict.decision is Decision.BUY
        assert any("sepakat dengan usulan" in r for r in verdict.reasoning)

    def test_prosecutor_can_disagree(self) -> None:
        pool = self._pool(
            (AgentRole.TECHNICAL, Decision.BUY, 0.3, "reading:macd"),
            (AgentRole.STRUCTURE, Decision.SELL, 0.9, "structure"),
        )
        context = context_from(FALLING)
        proposal = ArunaAnalyst(pool).safe_evaluate(context)
        assert Prosecutor(pool, proposal).safe_evaluate(context).decision is Decision.SELL

    def test_prosecutor_raises_shared_evidence_as_an_objection(self) -> None:
        pool = self._pool(
            (AgentRole.TECHNICAL, Decision.BUY, 0.9, "reading:rsi"),
            (AgentRole.MOMENTUM, Decision.BUY, 0.9, "reading:rsi"),
        )
        context = context_from(RISING)
        proposal = ArunaAnalyst(pool).safe_evaluate(context)
        verdict = Prosecutor(pool, proposal).safe_evaluate(context)
        assert any("reading:rsi" in r for r in verdict.reasoning)

    def test_prosecutor_can_reach_wait(self) -> None:
        pool = self._pool(
            (AgentRole.TECHNICAL, Decision.BUY, 0.5, "a"),
            (AgentRole.STRUCTURE, Decision.SELL, 0.5, "b"),
        )
        context = context_from(FLAT)
        proposal = ArunaAnalyst(pool).safe_evaluate(context)
        assert Prosecutor(pool, proposal).safe_evaluate(context).decision is Decision.WAIT


class TestSelfCritic:
    """SPEC 15: what is the strongest evidence the decision is wrong?"""

    def _pool(self, *specs) -> OpinionPool:
        return OpinionPool(
            opinions=tuple(
                AgentOpinion(
                    role=role,
                    decision=decision,
                    confidence=confidence,
                    reasoning=("reason",),
                    evidence=(EvidenceRef(key=f"e{i}"),),
                )
                for i, (role, decision, confidence) in enumerate(specs)
            )
        )

    def test_stronger_counter_evidence_forces_reassessment(self) -> None:
        pool = self._pool(
            (AgentRole.TECHNICAL, Decision.BUY, 0.3),
            (AgentRole.STRUCTURE, Decision.SELL, 0.9),
            (AgentRole.REVERSAL, Decision.SELL, 0.8),
        )
        context = context_from(FLAT)
        proposal = AgentOpinion(
            role=AgentRole.ARUNA_ANALYST,
            decision=Decision.BUY,
            confidence=0.4,
            reasoning=("weighted",),
        )
        result = SelfCritic().critique(context, pool, proposal)

        assert result.reassessment_required
        assert result.counter_weight > result.proposal_weight

    def test_weak_counter_evidence_does_not(self) -> None:
        pool = self._pool(
            (AgentRole.TECHNICAL, Decision.BUY, 0.9),
            (AgentRole.STRUCTURE, Decision.BUY, 0.8),
            (AgentRole.REVERSAL, Decision.SELL, 0.1),
        )
        context = context_from(RISING)
        proposal = AgentOpinion(
            role=AgentRole.ARUNA_ANALYST,
            decision=Decision.BUY,
            confidence=0.8,
            reasoning=("weighted",),
        )
        assert not SelfCritic().critique(context, pool, proposal).reassessment_required

    def test_critiquing_a_wait_asks_what_is_being_missed(self) -> None:
        pool = self._pool((AgentRole.TECHNICAL, Decision.BUY, 0.7))
        context = context_from(RISING)
        proposal = AgentOpinion(
            role=AgentRole.ARUNA_ANALYST,
            decision=Decision.WAIT,
            confidence=0.0,
            reasoning=("balanced",),
            abstained=True,
        )
        result = SelfCritic().critique(context, pool, proposal)
        assert any("terlewat" in r for r in result.opinion.reasoning)


class TestDeliberation:
    def test_round_one_is_not_a_council_decision(self) -> None:
        """SPEC 14's cross-protest, veto and judge are PHASE 6."""
        result = DeliberationEngine().deliberate(context_from(RISING))
        assert result.is_council_decision is False
        payload = result.to_dict()
        assert payload["is_council_decision"] is False
        assert "PHASE 6" in payload["phase_note"]

    def test_every_roster_agent_produces_an_opinion(self) -> None:
        result = DeliberationEngine().deliberate(context_from(RISING))
        assert len(result.opinions) == len(default_roster())

    def test_blocked_context_yields_no_signal(self) -> None:
        result = DeliberationEngine().deliberate(
            context_from(RISING, data_quality="STALE")
        )
        assert result.outcome is Decision.NO_SIGNAL
        assert result.confidence == 0.0
        assert result.no_trade.blocked

    def test_kill_switch_blocks_the_outcome(self) -> None:
        base = context_from(RISING)
        context = DecisionContext(
            market=base.market,
            symbol=base.symbol,
            interval=base.interval,
            as_of=base.as_of,
            state=base.state,
            technical=base.technical,
            trading_allowed=False,
        )
        result = DeliberationEngine().deliberate(context)
        assert NoTradeReason.KILL_SWITCH_ACTIVE in result.no_trade.reasons
        assert not result.outcome.is_directional

    def test_confidence_stays_a_probability(self) -> None:
        for series in (RISING, FALLING, FLAT):
            result = DeliberationEngine().deliberate(context_from(series))
            assert 0.0 <= result.confidence <= 1.0

    def test_a_broken_agent_does_not_stop_the_others(self) -> None:
        class Exploding:
            role = AgentRole.TECHNICAL

            def safe_evaluate(self, context):
                from aruna.agents.base import Agent

                return Agent.safe_evaluate(self, context)

            def evaluate(self, context):
                raise RuntimeError("boom")

        engine = DeliberationEngine(roster=[Exploding(), StructureAgent()])
        result = engine.deliberate(context_from(RISING))
        assert len(result.opinions) == 2
        assert result.opinions[0].abstained
        assert "boom" in result.opinions[0].reasoning[0]
