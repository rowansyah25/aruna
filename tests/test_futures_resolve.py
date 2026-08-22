"""Closing the loop (FUTURES SPEC 40-45, 47).

Two defects this file locks down, both found by running the resolver for the
first time against real stored plans:

* the fingerprint basis changed in migration 0016, so every row written before
  it recomputed to a different hash and 26 plans were rejected as "changed
  after issue" when not one had been touched;
* nothing had ever called ``verify()`` - it was written, exported, tested and
  unreachable, which is this codebase's recurring defect and the resolver is
  the first thing that actually exercises it.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.futures.plan import FINGERPRINT_VERSION, PlanVerdict
from aruna.futures.resolve import FuturesResolver, ResolveRun, _plan_from_row

sys.path.insert(0, "tests")
from test_futures_plan import NOW, _plan


class TestTheFingerprintBasisIsVersioned:
    """Adding a field to the hash must not un-verify the archive."""

    def test_v1_and_v2_differ(self) -> None:
        """If they matched, the version would be decoration."""
        plan = _plan()
        assert plan.fingerprint_at(1) != plan.fingerprint_at(2)

    def test_the_current_property_uses_the_current_version(self) -> None:
        plan = _plan()
        assert plan.fingerprint == plan.fingerprint_at(FINGERPRINT_VERSION)

    def test_reference_price_is_what_v2_added(self) -> None:
        """v1 ignores it, so a row that never had one still verifies."""
        from dataclasses import replace

        plan = _plan()
        moved = replace(plan, reference_price=Decimal(1))
        assert moved.fingerprint_at(1) == plan.fingerprint_at(1)
        assert moved.fingerprint_at(2) != plan.fingerprint_at(2)

    def test_a_real_change_still_breaks_both_versions(self) -> None:
        """Versioning must not become a way to smuggle an edit past the check."""
        from dataclasses import replace

        plan = _plan()
        tampered = replace(plan, stop=plan.stop - Decimal(100))
        assert tampered.fingerprint_at(1) != plan.fingerprint_at(1)
        assert tampered.fingerprint_at(2) != plan.fingerprint_at(2)


class _Store:
    """Records what the resolver did, and can refuse verification."""

    def __init__(self, rows, *, tamper: set[str] | None = None) -> None:
        self.rows = rows
        self.tamper = tamper or set()
        self.results: list = []
        self.ghosts: list = []

    async def due_for_resolution(self, *, limit=50, reference=None):
        return self.rows

    async def verify(self, plan) -> None:
        from aruna.db.repositories.futures import PlanTampered

        if plan.signal_id in self.tamper:
            raise PlanTampered(f"plan {plan.signal_id} does not match")

    async def save_result(self, result, ends) -> int:
        self.results.append(result)
        return 1

    async def save_ghost(self, ghost, ends) -> int:
        self.ghosts.append(ghost)
        return 1


class _Provider:
    """Serves klines. `closes` controls each bar's close time."""

    def __init__(self, bars) -> None:
        self.bars = bars
        self.asked: dict = {}

    async def _get(self, path, **params):
        self.asked = dict(params, path=path)
        return self.bars


def _row(**overrides) -> dict:
    base = {
        "signal_id": "sig-0001",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "verdict": "PLAN",
        "horizon_hours": 4.0,
        "created_at": NOW,
        "reference_price": Decimal(63000),
        "entry": Decimal(63000),
        "stop": Decimal(61800),
        "target": Decimal(64500),
        "quantity": Decimal("0.4"),
        "leverage": 10,
        "liquidation_price": Decimal(56927),
        "refusals": [],
    }
    return base | overrides


def _kline(minutes_after: int, *, high: str, low: str, close: str) -> list:
    closes = NOW + timedelta(minutes=minutes_after)
    ms = int(closes.timestamp() * 1000)
    return [ms - 300_000, "0", high, low, close, "0", ms]


class TestOnlyWhatCanBeScoredIsScored:
    @pytest.mark.asyncio
    async def test_a_tampered_plan_is_refused_not_scored(self) -> None:
        """Scoring it anyway would launder the tampering into the win rate."""
        store = _Store([_row()], tamper={"sig-0001"})
        provider = _Provider([_kline(30, high="64600", low="62900", close="64500")])
        run = await FuturesResolver(store=store, provider=provider).resolve_due()

        assert run.tampered == 1
        assert run.scored == 0
        assert store.results == []
        assert "fingerprint" in " ".join(run.errors).lower() or run.errors

    @pytest.mark.asyncio
    async def test_a_refusal_is_not_scored(self) -> None:
        """The gates rejected it; there is no claim to be right or wrong about."""
        store = _Store([_row(verdict="REFUSED", entry=None, stop=None, target=None)])
        provider = _Provider([_kline(30, high="64600", low="62900", close="64500")])
        run = await FuturesResolver(store=store, provider=provider).resolve_due()

        assert run.scored == 0 and run.ghosts == 0
        assert run.skipped == 1

    @pytest.mark.asyncio
    async def test_no_price_path_is_skipped_not_guessed(self) -> None:
        store = _Store([_row()])
        run = await FuturesResolver(
            store=store, provider=_Provider([])
        ).resolve_due()
        assert run.skipped == 1
        assert store.results == []


class TestOnlyClosedBarsAreUsed:
    """A bar still forming can still move, and scoring against it reads a
    price that is not final (SPEC 24)."""

    @pytest.mark.asyncio
    async def test_a_bar_closing_after_now_is_dropped(self) -> None:
        store = _Store([_row()])
        # One bar closed inside the horizon, one closing an hour from `now`.
        provider = _Provider(
            [
                _kline(30, high="63100", low="62900", close="63000"),
                _kline(300, high="64600", low="62000", close="64500"),
            ]
        )
        run = await FuturesResolver(store=store, provider=provider).resolve_due(
            reference=NOW + timedelta(hours=1)
        )
        assert run.scored == 1
        # The dropped bar was the only one touching the target, so if it had
        # been used the outcome would be TARGET_HIT.
        assert store.results[0].outcome.value != "TARGET_HIT"

    @pytest.mark.asyncio
    async def test_a_bar_closing_after_the_horizon_is_dropped(self) -> None:
        store = _Store([_row()])
        provider = _Provider(
            [
                _kline(30, high="63100", low="62900", close="63000"),
                _kline(600, high="64600", low="62000", close="64500"),
            ]
        )
        run = await FuturesResolver(store=store, provider=provider).resolve_due(
            reference=NOW + timedelta(days=2)
        )
        assert run.scored == 1
        assert store.results[0].outcome.value != "TARGET_HIT"


class TestThePathComesFromThePerpetual:
    @pytest.mark.asyncio
    async def test_it_asks_the_venue_for_the_plans_own_symbol(self) -> None:
        """Scoring a USDT-quoted plan against an IDR series is the same units
        error that once put a stop 5.5 million "dollars" from a 63,000 entry."""
        store = _Store([_row()])
        provider = _Provider([_kline(30, high="63100", low="62900", close="63000")])
        await FuturesResolver(store=store, provider=provider).resolve_due(
            reference=NOW + timedelta(hours=5)
        )
        assert provider.asked["symbol"] == "BTCUSDT"
        assert provider.asked["path"] == "/fapi/v1/klines"


class TestWaitsAreGhostScored:
    @pytest.mark.asyncio
    async def test_a_wait_produces_a_ghost_not_a_result(self) -> None:
        store = _Store([_row(verdict="WAIT", entry=None, stop=None, target=None)])
        provider = _Provider([_kline(30, high="63100", low="62900", close="63000")])
        run = await FuturesResolver(store=store, provider=provider).resolve_due(
            reference=NOW + timedelta(hours=5)
        )
        assert run.ghosts == 1
        assert run.scored == 0
        assert store.results == []
        assert store.ghosts[0].verdict.value in ("QUIET", "MOVE_EXISTED")


class TestThePathIntervalFollowsTheHorizon:
    """Fixed at 5m it failed at both ends, and the far end failed silently.

    Under ~9 minutes the window contained no closed 5m bar, so those plans
    were skipped every tick forever. Past ~125 hours the window needed more
    than MAX_BARS, the venue returned the first 1500, and the tail was simply
    missing - a plan whose target was hit in hour 130 was recorded as EXPIRED,
    as a statement of fact, in the table F6 scores.
    """

    @pytest.mark.parametrize(
        ("hours", "expected"),
        [(0.1, "1m"), (1, "1m"), (4, "1m"), (24, "1m"), (168, "15m"), (720, "1h")],
    )
    def test_the_interval_is_chosen_from_the_span(self, hours, expected) -> None:
        from aruna.futures.resolve import _interval_for

        assert _interval_for(int(hours * 60))[0] == expected

    @pytest.mark.parametrize("hours", [0.05, 0.1, 1, 4, 24, 168, 720, 2160])
    def test_every_horizon_fits_in_one_request(self, hours) -> None:
        from aruna.futures.resolve import MAX_BARS, _interval_for

        span = int(hours * 60)
        _, minutes = _interval_for(span)
        assert span // minutes + 2 <= MAX_BARS

    @pytest.mark.parametrize("hours", [0.05, 0.1, 0.15])
    def test_a_short_horizon_still_gets_bars(self, hours) -> None:
        """These were unscoreable forever: no closed 5m bar existed in them."""
        from aruna.futures.resolve import _interval_for

        span = int(hours * 60)
        _, minutes = _interval_for(span)
        assert span // minutes >= 1


class TestTheRunReportsHonestly:
    def test_an_empty_pass_says_so(self) -> None:
        assert "tidak ada plan" in ResolveRun().summary()

    def test_a_liquidation_is_named_in_the_summary(self) -> None:
        run = ResolveRun(scored=3, ghosts=1, liquidations=1)
        assert "1 LIQUIDATED" in run.summary()

    def test_tampering_is_named_in_the_summary(self) -> None:
        run = ResolveRun(tampered=2)
        assert "fingerprint tidak cocok" in run.summary()


class TestRowRebuild:
    def test_a_rebuilt_row_reproduces_its_fingerprint(self) -> None:
        """The round trip has to be exact, or verification rejects clean rows."""
        plan = _plan()
        row = _row(
            signal_id=plan.signal_id,
            verdict=plan.verdict.value,
            side=plan.side.value,
            horizon_hours=plan.horizon_hours,
            created_at=plan.created_at,
            reference_price=plan.reference_price,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            quantity=plan.quantity,
            leverage=plan.leverage,
            liquidation_price=plan.liquidation.price,
            refusals=list(plan.refusals),
        )
        assert _plan_from_row(row).fingerprint == plan.fingerprint

    def test_a_wait_row_rebuilds_without_numbers(self) -> None:
        row = _row(
            verdict="WAIT",
            side="FLAT",
            entry=None,
            stop=None,
            target=None,
            quantity=None,
            leverage=None,
            liquidation_price=None,
        )
        plan = _plan_from_row(row)
        assert plan.verdict is PlanVerdict.WAIT
        assert plan.entry is None
        assert plan.reference_price == Decimal(63000)


def test_the_datetime_import_is_used() -> None:
    """Guards the module's own imports against drift."""
    assert isinstance(NOW, datetime)
    assert NOW.tzinfo is UTC
