"""Per-horizon leverage (FUTURES SPEC 32).

The defect this file exists to prevent is the one every prior phase produced: a
check that is written, exported and unit-tested but that cannot fire. So the
central test here does not assert that the machinery *runs* - it asserts that a
realistic position, at a realistic funding rate, over a realistic hold, is
actually rejected for a horizon it would have passed at entry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.futures.horizon import (
    MAX_PROJECTED_SETTLEMENTS,
    project_to_horizon,
    settlements_in,
)
from aruna.futures.leverage import MIN_SAFE_BUFFER, recommend_leverage, stress_test
from aruna.futures.models import (
    ContractSpec,
    FundingRate,
    InstrumentType,
    MarginMode,
    PositionSide,
    Provenance,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _provenance() -> Provenance:
    return Provenance(source="test", server_timestamp=NOW)


def _contract() -> ContractSpec:
    return ContractSpec(
        symbol="BTCUSDT",
        instrument=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_notional=Decimal(5),
        max_leverage=20,
        maintenance_margin_rate=Decimal("0.004"),
        provenance=_provenance(),
    )


def _funding(
    rate: str,
    *,
    next_at: datetime | None = None,
    settled: bool = False,
    interval: int = 8,
) -> FundingRate:
    return FundingRate(
        symbol="BTCUSDT",
        rate=Decimal(rate),
        funding_time=NOW,
        next_funding_time=next_at,
        interval_hours=interval,
        provenance=_provenance(),
        settled=settled,
    )


class TestSettlementCount:
    def test_a_hold_that_ends_before_the_next_settlement_pays_nothing(self) -> None:
        """The effect that a fractional-interval estimate would invent.

        A one-hour scalp entered seven hours before settlement crosses no
        payment at all. Prorating the rate would charge it an eighth of a
        funding fee it never pays.
        """
        funding = _funding("0.0001", next_at=NOW + timedelta(hours=7))
        count, exact = settlements_in(funding, 1.0, reference=NOW)
        assert count == 0
        assert exact is True

    def test_a_hold_that_crosses_one_settlement_pays_once(self) -> None:
        funding = _funding("0.0001", next_at=NOW + timedelta(minutes=10))
        count, exact = settlements_in(funding, 1.0, reference=NOW)
        assert count == 1
        assert exact is True

    def test_three_days_crosses_nine_settlements(self) -> None:
        funding = _funding("0.0001", next_at=NOW + timedelta(hours=1))
        count, exact = settlements_in(funding, 72.0, reference=NOW)
        # First at +1h, then every 8h: 1, 9, 17 ... 65 -> nine of them by +72h.
        assert count == 9
        assert exact is True

    def test_without_a_published_schedule_the_count_rounds_up_and_says_so(
        self,
    ) -> None:
        """An upper bound must never be presented as a measurement."""
        funding = _funding("0.0001", next_at=None, settled=True)
        count, exact = settlements_in(funding, 24.0, reference=NOW)
        assert count == 4  # floor(24/8) + 1
        assert exact is False

    def test_a_broken_interval_does_not_divide_by_zero(self) -> None:
        funding = _funding("0.0001", next_at=None, interval=0)
        assert settlements_in(funding, 24.0, reference=NOW) == (0, True)


class TestDirection:
    """A short receives what a long pays. Reporting decay for both would be a
    fabricated conservatism, not a projection."""

    def test_a_long_pays_positive_funding_out_of_its_margin(self) -> None:
        projection = project_to_horizon(
            entry=Decimal(63000),
            stop=Decimal(61740),
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(630),
            contract=_contract(),
            funding=_funding("0.001", next_at=NOW + timedelta(hours=1)),
            horizon_hours=72.0,
            reference=NOW,
        )
        assert projection.funding_cost is not None
        assert projection.funding_cost > 0
        assert projection.projected_margin is not None
        assert projection.projected_margin < projection.entry_margin
        assert projection.margin_erosion_pct is not None
        assert projection.margin_erosion_pct > 0

    def test_a_short_receives_positive_funding_and_its_margin_grows(self) -> None:
        projection = project_to_horizon(
            entry=Decimal(63000),
            stop=Decimal(64260),
            quantity=Decimal("0.1"),
            side=PositionSide.SHORT,
            margin=Decimal(630),
            contract=_contract(),
            funding=_funding("0.001", next_at=NOW + timedelta(hours=1)),
            horizon_hours=72.0,
            reference=NOW,
        )
        assert projection.funding_cost is not None
        assert projection.funding_cost < 0
        assert projection.projected_margin is not None
        assert projection.projected_margin > projection.entry_margin
        assert any(
            "menerima funding pada rate saat ini" in f for f in projection.findings
        )


class TestUnknownIsNotZero:
    def test_missing_funding_reports_the_decay_as_unknown(self) -> None:
        """The exact defect family: absence of data reading as absence of cost."""
        projection = project_to_horizon(
            entry=Decimal(63000),
            stop=Decimal(61740),
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(630),
            contract=_contract(),
            funding=None,
            horizon_hours=72.0,
            reference=NOW,
        )
        assert projection.decay_known is False
        assert projection.projected_margin is None
        assert projection.funding_cost is None
        assert projection.material is False
        assert any("tidak sama dengan nol" in f for f in projection.findings)

    def test_an_absurd_horizon_is_refused_rather_than_projected(self) -> None:
        projection = project_to_horizon(
            entry=Decimal(63000),
            stop=Decimal(61740),
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(630),
            contract=_contract(),
            funding=_funding("0.001", next_at=NOW + timedelta(hours=1)),
            horizon_hours=24 * 365.0,
            reference=NOW,
        )
        assert projection.refused is True
        assert projection.decay_known is False

    def test_the_refusal_threshold_can_be_reached(self) -> None:
        """Guards against the constant being set where nothing reaches it."""
        funding = _funding("0.001", next_at=NOW + timedelta(hours=1))
        hours = (MAX_PROJECTED_SETTLEMENTS + 2) * 8.0
        count, _ = settlements_in(funding, hours, reference=NOW)
        assert count > MAX_PROJECTED_SETTLEMENTS


class TestTheCheckActuallyFires:
    """The point of the whole module.

    Concrete position: 0.1 BTC at 63,000 (6,300 notional), stop 5% away at
    59,850, 10x leverage so 630 margin, 0.4% maintenance rate. Funding at
    0.1% per settlement - elevated, and a rate this venue does reach.

    At entry the liquidation buffer is roughly 1.9x the stop distance. Over a
    seven-day hold the position pays 21 times, which is 132 of its 630 margin,
    and the buffer falls to roughly 1.5x. That crosses a band.
    """

    ENTRY = Decimal(63000)
    STOP = Decimal(59850)
    QTY = Decimal("0.1")
    MARGIN = Decimal(630)

    def _project(self, hours: float):
        return project_to_horizon(
            entry=self.ENTRY,
            stop=self.STOP,
            quantity=self.QTY,
            side=PositionSide.LONG,
            margin=self.MARGIN,
            contract=_contract(),
            funding=_funding("0.001", next_at=NOW + timedelta(hours=1)),
            horizon_hours=hours,
            reference=NOW,
        )

    def test_a_long_hold_degrades_the_band(self) -> None:
        projection = self._project(24 * 7.0)
        assert projection.decay_known is True
        assert projection.settlements == 21
        assert projection.band_changed is True
        assert projection.material is True
        assert projection.projected_buffer.score < projection.entry_buffer.score

    def test_a_short_hold_does_not(self) -> None:
        """The other half of "can fire": it must also be able to NOT fire.

        A check that always trips is as useless as one that never does.
        """
        projection = self._project(4.0)
        assert projection.decay_known is True
        assert projection.material is False

    def test_the_effective_buffer_takes_the_worse_state(self) -> None:
        projection = self._project(24 * 7.0)
        assert projection.effective_buffer.score == min(
            projection.entry_buffer.score, projection.projected_buffer.score
        )

    def test_funding_that_eats_the_whole_margin_is_named_as_such(self) -> None:
        projection = project_to_horizon(
            entry=self.ENTRY,
            stop=self.STOP,
            quantity=self.QTY,
            side=PositionSide.LONG,
            margin=Decimal(50),
            contract=_contract(),
            funding=_funding("0.003", next_at=NOW + timedelta(hours=1)),
            horizon_hours=24 * 7.0,
            reference=NOW,
        )
        assert projection.projected_margin is not None
        assert projection.projected_margin <= 0
        assert projection.projected_buffer.score == 0
        assert any("menghabiskan seluruh margin" in f for f in projection.findings)


class TestTheLadderUsesIt:
    """SPEC 32 has to reach the recommendation, not sit beside it.

    Written-but-never-called is the defect this codebase produced in ten
    consecutive phases; these tests assert the live path.
    """

    ENTRY = Decimal(63000)
    STOP = Decimal(59850)
    QTY = Decimal("0.1")

    def _ladder(self, hours: float | None):
        return stress_test(
            entry=self.ENTRY,
            stop=self.STOP,
            quantity=self.QTY,
            side=PositionSide.LONG,
            contract=_contract(),
            margin_mode=MarginMode.ISOLATED,
            funding=_funding("0.001", next_at=NOW + timedelta(hours=1)),
            horizon_hours=hours,
            reference=NOW,
        )

    def test_a_long_horizon_lowers_the_recommended_leverage(self) -> None:
        at_entry = recommend_leverage(self._ladder(None))
        over_a_week = recommend_leverage(self._ladder(24 * 7.0))

        assert at_entry.recommended is not None
        assert (
            over_a_week.recommended is None
            or over_a_week.recommended < at_entry.recommended
        ), "the horizon changed nothing, so SPEC 32 is decorative"

    def test_every_rung_carries_its_projection(self) -> None:
        options = self._ladder(24 * 7.0)
        assert options
        assert all(o.projection is not None for o in options)

    def test_no_horizon_means_no_projection_and_no_invented_one(self) -> None:
        options = self._ladder(None)
        assert options
        assert all(o.projection is None for o in options)

    def test_the_reasoning_says_the_horizon_was_applied(self) -> None:
        decision = recommend_leverage(self._ladder(24 * 7.0))
        assert any(
            "funding settlement" in r for r in decision.reasoning
        ), "a horizon-scored recommendation must not read like an entry-scored one"

    def test_an_unknown_decay_is_declared_in_the_reasoning(self) -> None:
        options = stress_test(
            entry=self.ENTRY,
            stop=self.STOP,
            quantity=self.QTY,
            side=PositionSide.LONG,
            contract=_contract(),
            funding=None,
            horizon_hours=24 * 7.0,
            reference=NOW,
        )
        decision = recommend_leverage(options)
        assert any("tidak diketahui, bukan nol" in r for r in decision.reasoning)

    @pytest.mark.parametrize("hours", [1.0, 4.0, 24.0, 24 * 7.0])
    def test_the_safety_bar_is_never_lowered_by_a_horizon(self, hours: float) -> None:
        """A horizon may only make a rung look worse, never better."""
        entry_only = {o.leverage: o.safety_score for o in self._ladder(None)}
        with_horizon = {o.leverage: o.safety_score for o in self._ladder(hours)}
        for leverage, score in with_horizon.items():
            assert score <= entry_only[leverage], (
                f"{leverage}x scored higher over {hours}h than at entry"
            )

    def test_an_accepted_rung_still_clears_the_bar_at_the_end_of_the_hold(
        self,
    ) -> None:
        for option in self._ladder(24 * 7.0):
            if option.acceptable:
                assert option.projection is not None
                assert option.projection.effective_buffer.score >= MIN_SAFE_BUFFER
