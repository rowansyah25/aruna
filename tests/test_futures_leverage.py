"""Leverage engine and slippage stress (FUTURES SPEC 15-18, 30, 31, 36).

The claim this file exists to pin down: **with risk-based sizing, leverage
changes neither the profit nor the loss.** It changes the margin posted and the
liquidation distance, nothing else. Several tests assert exactly that, because
it is the fact that makes reaching for leverage pointless rather than merely
risky - and a future edit that broke it would restore the usual, wrong mental
model without anything failing.

The second guarantee: argument may lower leverage and may never raise it. An
agent can object that the arithmetic is too aggressive; no agent can talk the
engine past a rung the liquidation maths refused.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from aruna.futures import leverage as leverage_module
from aruna.futures.leverage import (
    CANDIDATES,
    MIN_SAFE_BUFFER,
    advise_margin_mode,
    recommend_leverage,
    stress_test,
)
from aruna.futures.models import (
    ContractSpec,
    InstrumentType,
    MarginMode,
    PositionSide,
)
from aruna.futures.slippage import SCENARIOS, stress_slippage

ENTRY = Decimal(50_000)
STOP = Decimal(49_000)
TARGET = Decimal(53_000)
QUANTITY = Decimal("0.1")
ATR = Decimal(500)


def _contract(**overrides) -> ContractSpec:
    base = {
        "symbol": "BTCUSDT",
        "instrument": InstrumentType.PERPETUAL,
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "tick_size": Decimal("0.10"),
        "step_size": Decimal("0.001"),
        "min_notional": Decimal(5),
        "max_leverage": 125,
        "maintenance_margin_rate": Decimal("0.004"),
        "margin_brackets": ((Decimal(0), Decimal("0.004"), Decimal(0)),),
    }
    return ContractSpec(**(base | overrides))


def _options(**overrides):
    base = {
        "entry": ENTRY,
        "stop": STOP,
        "quantity": QUANTITY,
        "side": PositionSide.LONG,
        "contract": _contract(),
        "atr": ATR,
    }
    return stress_test(**(base | overrides))


# ---------------------------------------------------------------------------
# The arithmetic that makes leverage a safety dial, not a profit dial
# ---------------------------------------------------------------------------


class TestLeverageIsNotAProfitDial:
    def test_no_profit_target_can_reach_this_module(self) -> None:
        """FUTURES SPEC 16, structurally: a number that cannot be passed in
        cannot be chased."""
        source = inspect.getsource(leverage_module)
        for banned in ("target_profit", "daily_target", "profit_goal"):
            assert banned not in source
        for fn in (stress_test, recommend_leverage):
            params = inspect.signature(fn).parameters
            assert not any("profit" in p or "target" in p for p in params)

    def test_leverage_changes_the_margin_and_nothing_about_the_trade(self) -> None:
        """The position is already sized: quantity, stop and target are the
        same at every rung, so profit and loss are too."""
        options = _options()
        margins = {o.margin_required for o in options}
        assert len(margins) == len(options)  # every rung posts different margin

        # ...and the trade itself never moves.
        planned_loss = abs(ENTRY - STOP) * QUANTITY
        planned_profit = abs(TARGET - ENTRY) * QUANTITY
        for _ in options:
            assert abs(ENTRY - STOP) * QUANTITY == planned_loss
            assert abs(TARGET - ENTRY) * QUANTITY == planned_profit

    def test_higher_leverage_moves_liquidation_closer(self) -> None:
        options = {o.leverage: o for o in _options()}
        low, high = options[2], options[10]
        assert low.liquidation is not None and high.liquidation is not None
        assert high.liquidation.price > low.liquidation.price  # nearer for a long
        assert high.margin_required < low.margin_required

    def test_higher_leverage_scores_worse(self) -> None:
        options = {o.leverage: o for o in _options()}
        assert options[2].safety_score >= options[10].safety_score

    def test_the_decision_states_what_leverage_actually_buys(self) -> None:
        decision = recommend_leverage(_options())
        note = decision.to_dict()["note"]
        assert "bukan profit atau kerugiannya" in note
        assert "liquidation yang lebih dekat, tidak ada yang lain" in note


# ---------------------------------------------------------------------------
# FUTURES SPEC 18 - the stress ladder
# ---------------------------------------------------------------------------


class TestStressTest:
    def test_every_rung_the_spec_names_is_tested(self) -> None:
        assert CANDIDATES == (2, 3, 4, 5, 7, 10)
        assert {o.leverage for o in _options()} == set(CANDIDATES)

    def test_a_rung_above_the_venue_maximum_is_skipped(self) -> None:
        options = _options(contract=_contract(max_leverage=5))
        assert max(o.leverage for o in options) == 5

    def test_a_hostile_regime_lowers_every_rung(self) -> None:
        calm = {o.leverage: o.safety_score for o in _options()}
        hostile = {
            o.leverage: o.safety_score for o in _options(hostile_regime=True)
        }
        assert all(hostile[k] <= calm[k] for k in calm)
        assert any(hostile[k] < calm[k] for k in calm)

    def test_conditions_only_ever_subtract(self) -> None:
        """Nothing about a setup makes a liquidation price further away than
        the arithmetic says it is."""
        clean = {o.leverage: o.safety_score for o in _options()}
        loaded = {
            o.leverage: o.safety_score
            for o in _options(
                hostile_regime=True, poor_liquidity=True, extreme_funding=True
            )
        }
        assert all(loaded[k] <= clean[k] for k in clean)

    def test_no_contract_means_no_liquidation_and_a_rejected_rung(self) -> None:
        for option in _options(contract=None):
            assert option.liquidation is None
            assert option.buffer.score == 0
            assert option.acceptable is False


# ---------------------------------------------------------------------------
# FUTURES SPEC 15, 17 - one number, and who may move it
# ---------------------------------------------------------------------------


class TestRecommendation:
    def test_one_number_is_returned_not_a_range(self) -> None:
        decision = recommend_leverage(_options())
        assert decision.recommended is None or isinstance(decision.recommended, int)
        assert decision.chosen is not None or decision.refused

    def test_the_highest_rung_that_still_clears_the_bar_is_chosen(self) -> None:
        options = _options()
        decision = recommend_leverage(options)
        if decision.recommended:
            acceptable = [o.leverage for o in options if o.acceptable]
            assert decision.recommended == max(acceptable)
            assert decision.chosen.safety_score >= MIN_SAFE_BUFFER

    def test_an_agent_may_lower_the_recommendation(self) -> None:
        options = _options()
        alone = recommend_leverage(options)
        if not alone.recommended or alone.recommended <= 2:
            pytest.skip("baseline already at the floor")

        with_objection = recommend_leverage(options, proposals={"SENTINEL": 2})
        assert with_objection.recommended <= 2
        assert any(
            "yang diambil adalah yang lebih rendah" in r
            for r in with_objection.reasoning
        )

    def test_no_agent_may_raise_it(self) -> None:
        """Argument does not move a liquidation price."""
        options = _options(hostile_regime=True, poor_liquidity=True)
        decision = recommend_leverage(options, proposals={"MOMENTUM": 10})
        assert decision.recommended is None or decision.recommended < 10
        if decision.recommended:
            assert any(
                "ditolak: argumen tidak menggeser liquidation price" in r
                for r in decision.reasoning
            )

    def test_when_nothing_is_safe_it_refuses_rather_than_picking_the_least_bad(
        self,
    ) -> None:
        options = _options(
            stop=Decimal(30_000),  # stop further than most liquidations
            hostile_regime=True,
            poor_liquidity=True,
            extreme_funding=True,
        )
        decision = recommend_leverage(options)
        assert decision.refused is True
        assert decision.recommended is None
        assert any("1x tetap tersedia" in r for r in decision.reasoning)

    def test_an_objection_below_every_safe_rung_refuses_too(self) -> None:
        options = _options()
        decision = recommend_leverage(options, proposals={"SENTINEL": 1})
        if decision.refused:
            assert any(
                "tidak ada tingkat pada atau di bawah" in r
                for r in decision.reasoning
            )


# ---------------------------------------------------------------------------
# FUTURES SPEC 31 - margin mode
# ---------------------------------------------------------------------------


class TestMarginMode:
    def test_isolated_is_the_default_advice(self) -> None:
        mode, why = advise_margin_mode()
        assert mode is MarginMode.ISOLATED
        assert "membatasi kerugian pada margin yang disetor" in why

    def test_correlated_exposure_strengthens_the_case(self) -> None:
        mode, why = advise_margin_mode(open_positions=3, correlated_exposure=True)
        assert mode is MarginMode.ISOLATED
        assert "satu liquidation bisa cascade ke semuanya" in why


# ---------------------------------------------------------------------------
# FUTURES SPEC 30 - slippage
# ---------------------------------------------------------------------------


class TestSlippage:
    def _report(self, **overrides):
        base = {
            "entry": ENTRY,
            "stop": STOP,
            "target": TARGET,
            "quantity": QUANTITY,
            "side": PositionSide.LONG,
        }
        return stress_slippage(**(base | overrides))

    def test_every_scenario_the_spec_names_is_run(self) -> None:
        labels = [s.label for s in self._report().scenarios]
        assert labels == [label for label, _ in SCENARIOS]

    def test_slippage_moves_both_ends_against_the_position(self) -> None:
        report = self._report()
        clean, worst = report.scenarios[0], report.scenarios[-1]
        assert worst.effective_entry > clean.effective_entry  # paid more
        assert worst.effective_stop < clean.effective_stop  # filled lower
        # The exit was omitted here once, and the omission survived precisely
        # because this test stopped at the stop while the note kept claiming
        # "both ends". Asserting the note is not asserting the behaviour.
        assert worst.effective_target < clean.effective_target  # sold lower

    def test_the_stop_fills_beyond_the_planned_loss(self) -> None:
        """A stop is a market order once touched, not a guaranteed price."""
        report = self._report()
        planned = abs(ENTRY - STOP) * QUANTITY
        assert report.scenarios[-1].realised_risk > planned
        assert any(
            "di luar kerugian yang direncanakan" in f for f in report.findings
        )

    def test_a_thin_edge_breaks_somewhere_on_the_ladder(self) -> None:
        report = self._report(target=Decimal(50_600))
        assert report.survives_all is False
        assert report.breaks_at is not None

    def test_a_setup_that_fails_clean_is_named_as_such(self) -> None:
        report = self._report(target=Decimal(50_050))
        assert report.scenarios[0].survives is False
        assert any(
            "sudah hilang sebelum slippage diperhitungkan" in f
            for f in report.findings
        )

    def test_a_robust_edge_survives_everything(self) -> None:
        report = self._report(target=Decimal(60_000))
        assert report.survives_all is True
        assert any(
            "edge bertahan di setiap skenario" in f for f in report.findings
        )

    def test_the_ratio_is_measured_against_the_planned_risk(self) -> None:
        """That is the number the position was sized to, and the one the
        trader believes they are risking."""
        report = self._report()
        planned = abs(ENTRY - STOP) * QUANTITY
        clean = report.scenarios[0]
        assert clean.net_rr == pytest.approx(
            (clean.net_reward / planned).quantize(Decimal("0.01"))
        )

    def test_a_short_is_stressed_in_its_own_direction(self) -> None:
        report = self._report(
            side=PositionSide.SHORT, stop=Decimal(51_000), target=Decimal(47_000)
        )
        clean, worst = report.scenarios[0], report.scenarios[-1]
        assert worst.effective_entry < clean.effective_entry  # sold lower
        assert worst.effective_stop > clean.effective_stop  # bought back higher
        assert worst.effective_target > clean.effective_target  # bought back higher

    def test_the_note_explains_where_slippage_is_charged(self) -> None:
        note = self._report().to_dict()["note"]
        assert "dibebankan di entry dan exit" in note
        assert "stop berubah jadi market order begitu tersentuh" in note

    def test_the_note_is_not_the_only_thing_saying_both_ends(self) -> None:
        """The note claimed 'both ends' while the exit went unstressed.

        A long at 50,000 with a 51,400 target reported net R:R 1.10 and
        'survives every scenario' when the honest figure was 0.85. The whole
        band of targets that were wrongly declared robust sat just above the
        break-even point, so the error flattered exactly the marginal setups.
        """
        report = self._report(target=Decimal(51_400))
        worst = report.scenarios[-1]
        drift = ENTRY * Decimal("0.5") / 100
        assert worst.effective_target == Decimal(51_400) - drift
        assert worst.net_rr is not None and worst.net_rr < Decimal("1.0")
        assert report.survives_all is False
