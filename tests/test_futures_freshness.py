"""Evidence freshness and ghost signals (FUTURES SPEC 40-45, 46, 52).

Three defects found by watching a live 24-hour loop, all of which passed every
test that existed at the time:

* the loop re-derived one answer from candles that had not moved in 11 hours;
* the integrity gate policed Binance to 60 seconds while the council's verdict
  came from yesterday afternoon, unchecked;
* 24 WAITs were stored and none were ever judged.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal

import pytest

from aruna.futures.learning import (
    GHOST_MOVE_PCT,
    GhostVerdict,
    PriceBar,
    score_plan,
    score_wait,
)
from aruna.futures.plan import MAX_EVIDENCE_AGE_MULTIPLE, PlanVerdict

sys.path.insert(0, "tests")
from test_futures_plan import ENTRY, NOW, _plan


class TestTheDirectionMustBeAsFreshAsThePrice:
    """The integrity gate holds Binance to sixty seconds. Nothing held the
    council's candles to anything, so a plan could pass "these inputs describe
    one coherent moment" while its LONG/SHORT came from the previous day."""

    def test_evidence_older_than_the_horizon_is_refused(self) -> None:
        plan = _plan(
            horizon_hours=4.0,
            evidence_as_of=NOW - timedelta(hours=11),
        )
        assert plan.verdict is PlanVerdict.NO_SIGNAL
        joined = " ".join(plan.refusals)
        assert "11.0h lalu" in joined
        assert "bukan ramalan atas window itu" in joined

    def test_fresh_evidence_passes(self) -> None:
        plan = _plan(horizon_hours=4.0, evidence_as_of=NOW - timedelta(hours=1))
        assert plan.verdict is PlanVerdict.PLAN, plan.refusals

    def test_the_boundary_is_the_horizon_itself(self) -> None:
        """Guards the constant against being set where nothing reaches it."""
        horizon = 4.0
        just_inside = _plan(
            horizon_hours=horizon,
            evidence_as_of=NOW - timedelta(hours=horizon * MAX_EVIDENCE_AGE_MULTIPLE),
        )
        just_outside = _plan(
            horizon_hours=horizon,
            evidence_as_of=NOW
            - timedelta(hours=horizon * MAX_EVIDENCE_AGE_MULTIPLE + 0.1),
        )
        assert just_inside.verdict is PlanVerdict.PLAN, just_inside.refusals
        assert just_outside.verdict is PlanVerdict.NO_SIGNAL

    def test_the_limit_scales_with_the_horizon(self) -> None:
        """Six hours is stale for a 4h call and fine for a 1d one."""
        stale = _plan(horizon_hours=4.0, evidence_as_of=NOW - timedelta(hours=6))
        fine = _plan(horizon_hours=24.0, evidence_as_of=NOW - timedelta(hours=6))
        assert stale.verdict is PlanVerdict.NO_SIGNAL
        assert fine.verdict is PlanVerdict.PLAN, fine.refusals

    def test_no_evidence_timestamp_does_not_block(self) -> None:
        """A caller that cannot supply it is not thereby refused - the check is
        additive, and every other gate still applies."""
        assert _plan(evidence_as_of=None).verdict is PlanVerdict.PLAN

    def test_a_refusal_carries_no_numbers(self) -> None:
        plan = _plan(horizon_hours=4.0, evidence_as_of=NOW - timedelta(hours=11))
        for field_name in ("entry", "stop", "target", "leverage", "quantity"):
            assert getattr(plan, field_name) is None, field_name


def _bars(*moves: str) -> list[PriceBar]:
    """Bars whose high/low sit `move` percent either side of ENTRY."""
    out = []
    for i, move in enumerate(moves):
        delta = ENTRY * Decimal(move) / 100
        out.append(
            PriceBar(
                as_of=NOW + timedelta(hours=i),
                high=ENTRY + delta,
                low=ENTRY - delta,
                close=ENTRY,
            )
        )
    return out


class TestStandingAsideIsScored:
    """A system that never scores its refusals cannot tell diligence from
    timidity. Futures stored 24 WAITs and judged none."""

    def test_a_quiet_market_vindicates_the_wait(self) -> None:
        result = score_wait(_plan(decision="WAIT"), _bars("0.1", "0.2"))
        assert result is not None
        assert result.verdict is GhostVerdict.QUIET
        assert result.stood_aside_well is True
        assert "tidak merugikan apa pun" in result.findings[0]

    def test_a_tradeable_move_is_named(self) -> None:
        result = score_wait(_plan(decision="WAIT"), _bars("0.2", "2.0"))
        assert result is not None
        assert result.verdict is GhostVerdict.MOVE_EXISTED
        assert result.stood_aside_well is False

    def test_it_never_claims_a_win_was_missed(self) -> None:
        """A WAIT states no direction, so no outcome can vindicate one."""
        result = score_wait(_plan(decision="WAIT"), _bars("3.0"))
        assert result is not None
        joined = " ".join(result.findings) + result.to_dict()["note"]
        assert "bukan kemenangan yang terlewat" in joined
        for claim in (
            "seharusnya menang",
            "kemenangan yang hilang",
            "profit yang terlewat",
            "would have won",
            "missed a win",
            "profit missed",
        ):
            assert claim not in joined.lower()

    def test_the_threshold_is_the_round_trip_cost(self) -> None:
        """Below what a trade costs, the opportunity never existed."""
        below = score_wait(
            _plan(decision="WAIT"), _bars(str(GHOST_MOVE_PCT - Decimal("0.1")))
        )
        above = score_wait(
            _plan(decision="WAIT"), _bars(str(GHOST_MOVE_PCT + Decimal("0.1")))
        )
        assert below is not None and below.verdict is GhostVerdict.QUIET
        assert above is not None and above.verdict is GhostVerdict.MOVE_EXISTED

    def test_no_path_is_unknown_not_quiet(self) -> None:
        """Absence of data is not evidence the market was calm."""
        result = score_wait(_plan(decision="WAIT"), [])
        assert result is not None
        assert result.verdict is GhostVerdict.UNKNOWN

    @pytest.mark.parametrize("decision", ["BUY", "SELL"])
    def test_only_waits_are_ghost_scored(self, decision: str) -> None:
        assert score_wait(_plan(decision=decision), _bars("2.0")) is None

    def test_a_refusal_is_not_ghost_scored(self) -> None:
        """A refusal is a different question - the gates rejected it."""
        refused = _plan(structure_levels=(ENTRY + Decimal(50),))
        assert refused.verdict is PlanVerdict.REFUSED
        assert score_wait(refused, _bars("2.0")) is None

    def test_a_wait_is_still_not_a_plan_result(self) -> None:
        """The two scorers stay separate: a WAIT must never enter the win rate
        of the plans ARUNA actually made."""
        assert score_plan(_plan(decision="WAIT"), _bars("2.0")) is None


class TestTheLoopRefreshesItsEvidence:
    @pytest.mark.asyncio
    async def test_a_missing_ingestor_is_reported_not_silently_skipped(self) -> None:
        from aruna.futures.service import FuturesPlanService

        service = FuturesPlanService(
            deliberation=None, council=None, store=None, universe=None, ingest=None
        )
        problems = await service.refresh_evidence("4h", ["BTCUSDT"])
        assert problems
        assert "not being refreshed" in problems[0]

    @pytest.mark.asyncio
    async def test_the_crypto_ingestor_is_asked_for_the_planning_horizon(
        self,
    ) -> None:
        from aruna.core.enums import Horizon, Market
        from aruna.futures.service import FuturesPlanService

        asked: dict = {}

        class _Ingestor:
            async def backfill(self, intervals, *, symbols=None):
                asked["intervals"] = intervals
                asked["symbols"] = symbols
                return type("R", (), {"failures": []})()

        class _Ingest:
            def ingestor(self, market):
                return _Ingestor() if market is Market.CRYPTO else None

        service = FuturesPlanService(
            deliberation=None,
            council=None,
            store=None,
            universe=None,
            ingest=_Ingest(),
        )
        assert await service.refresh_evidence(Horizon.H4, ["BTCUSDT", "ETHUSDT"]) == []
        assert asked["intervals"] == (Horizon.H4,)
        # Mapped to the spot symbols the universe actually holds.
        assert asked["symbols"] == ("BTC/USDT", "ETH/USDT")
