"""A backtest run may only feed governance from the regime it was measured in.

The defect these pin down was live, not hypothetical. ``recent_runs`` selected
``ORDER BY ran_at DESC LIMIT 5`` with no filter at all, the only eleven rows in
``backtest_runs`` were pair-IDR runs priced on a withdrawn 0.30%-per-side fee
schedule, and ``GovernanceService.research`` turned them into four OPEN research
questions quoting rupiah figures at a system whose crypto notional is 1,000
USDT. Migration 0021 deleted the rows; the cost-basis marker is what stops the
next fee change from doing it again.

Two things are deliberately tested against a real MySQL rather than a fake: the
filter lives in SQL, so a fake database would only prove that a string was
assembled, and the write/read agreement (``record_backtest`` storing a basis
that ``recent_runs`` will accept) exists only when both halves meet in a real
table. A fake here would be a test that cannot fail.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import asyncmy
import pytest

from aruna.core.config import DatabaseSettings
from aruna.core.enums import Horizon, Market
from aruna.db.migrator import Migrator, discover_migrations
from aruna.db.pool import Database, ensure_database_exists
from aruna.db.repositories.backtest import BacktestRepository, _plain, cost_basis
from aruna.governance.research import questions_from_backtest
from aruna.governance.service import GovernanceService

#: The schedule crypto was priced on before 2026-08-17: 0.30% per side against
#: a 1,000,000 IDR notional. Written out rather than derived from the code, so
#: this stays a fixed historical fact even if the fingerprint format changes.
WITHDRAWN_BASIS = "CRYPTO:fee0.3/0.3:slip10:spread1:cap1000000"

START = datetime(2026, 6, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The fingerprint itself
# ---------------------------------------------------------------------------


class TestCostBasis:
    def test_the_two_markets_do_not_share_a_basis(self) -> None:
        """IDX and crypto differ in fee, in slippage and in currency; a shared
        fingerprint would let one market's runs answer for the other."""
        assert cost_basis(Market.CRYPTO) != cost_basis(Market.IDX)

    def test_the_current_crypto_basis_is_not_the_withdrawn_one(self) -> None:
        assert cost_basis(Market.CRYPTO) != WITHDRAWN_BASIS
        assert "fee0.1/0.1" in cost_basis(Market.CRYPTO)
        assert "cap1000" in cost_basis(Market.CRYPTO)

    def test_a_basis_fits_the_column(self) -> None:
        """VARCHAR(64) in migration 0021. Strict mode would reject an overlong
        value at insert time, losing the run rather than truncating it.

        Iterates ``COST_MODELS`` rather than ``Market``: a market with no
        measured fee schedule has no basis to check, and reading the source of
        truth means a market added later is covered here the moment it gains
        one - without anybody remembering to edit this test."""
        from aruna.signals.paper import COST_MODELS

        assert COST_MODELS, "no market has a cost model; the lookup is empty"
        for market in COST_MODELS:
            assert len(cost_basis(market)) <= 64

    def test_a_market_without_a_schedule_refuses_instead_of_borrowing(self) -> None:
        """FOREX joined in 2026-08-27 with no measured spread.

        The binary branch this replaced would have quoted gold PnL on
        Indonesian sell-side levies in rupiah, and produced a plausible number
        while doing it."""
        with pytest.raises(NotImplementedError, match="spread is not measured"):
            cost_basis(Market.FOREX)

    def test_a_cosmetic_decimal_rewrite_is_not_a_new_regime(self) -> None:
        """0.10 and 0.1 are the same fee. Reading them as different schedules
        would silently discard every stored run over an edit that changed
        nothing."""
        assert _plain(Decimal("0.10")) == _plain(Decimal("0.1")) == "0.1"
        assert _plain(Decimal("1E+3")) == _plain(Decimal("1000")) == "1000"


# ---------------------------------------------------------------------------
# The question text
# ---------------------------------------------------------------------------


def _cost_run(**extra: object) -> dict[str, object]:
    run: dict[str, object] = {
        "interval": "1d",
        "combined": {
            "resolved": 10,
            "direction_correct": 5,
            "direction_accuracy": 0.5,
            "paper_trades": {
                "trades": 10,
                "cost_ratio": 5.0,
                "net_pnl": "-400.00",
                "gross_pnl": "100.00",
            },
        },
    }
    run.update(extra)
    return run


class TestQuestionProvenance:
    def test_a_cost_question_names_the_currency_and_the_fee_schedule(self) -> None:
        basis = cost_basis(Market.CRYPTO)
        questions = questions_from_backtest(
            _cost_run(market="CRYPTO", cost_basis=basis)
        )
        question = next(q for q in questions if q.key == "costs_exceed_edge_1d")
        assert any(basis in line for line in question.evidence)
        assert any("quote currency" in line for line in question.evidence)

    def test_a_run_with_no_recorded_basis_says_so_rather_than_guessing(self) -> None:
        """SPEC 4: an unrecorded schedule is reported as unrecorded. Inventing
        one is how a rupiah figure came to be read as USDT."""
        questions = questions_from_backtest(_cost_run(market="CRYPTO"))
        question = next(q for q in questions if q.key == "costs_exceed_edge_1d")
        assert any("was not recorded" in line for line in question.evidence)

    def test_a_run_with_neither_market_nor_basis_says_both_are_missing(self) -> None:
        questions = questions_from_backtest(_cost_run())
        question = next(q for q in questions if q.key == "costs_exceed_edge_1d")
        assert any(
            "neither market nor cost basis" in line for line in question.evidence
        )


# ---------------------------------------------------------------------------
# Migration 0021 explains what it deleted
# ---------------------------------------------------------------------------


class TestMigration0021:
    def _sql(self) -> str:
        migration = next(m for m in discover_migrations() if m.version == "0021")
        return migration.sql

    def test_it_is_discovered(self) -> None:
        assert Path("migrations/0021_backtest_cost_basis.sql").is_file()
        assert any(m.version == "0021" for m in discover_migrations())

    def test_it_adds_the_marker_and_removes_the_idr_era_rows(self) -> None:
        sql = self._sql()
        assert "ADD COLUMN cost_basis VARCHAR(64) NOT NULL DEFAULT ''" in sql
        assert "DELETE FROM backtest_runs" in sql
        assert "DELETE FROM research_questions" in sql

    def test_it_states_that_crypto_backtest_starts_from_zero_samples(self) -> None:
        """A migration that deletes data without explaining itself is data lost
        without a trace. Zero here means 'not yet', not 'clean'."""
        sql = self._sql()
        assert "NOL SAMPEL" in sql
        assert "BELUM ADA" in sql

    def test_it_names_the_proposals_left_pointing_at_a_deleted_question(self) -> None:
        """Three model_proposals carry question_key='no_measurable_edge_1d',
        one of them approved by a named human. The dangling link is documented
        rather than nulled, because a null cannot be told apart from 'this
        proposal never had a question'."""
        sql = self._sql()
        assert "exit-at-target" in sql
        assert "proposal_decisions" in sql


# ---------------------------------------------------------------------------
# Live path, real MySQL
# ---------------------------------------------------------------------------


def _test_settings() -> DatabaseSettings:
    base = DatabaseSettings()
    return base.model_copy(
        update={"name": f"{base.name}_test_costbasis", "max_pool": 5}
    )


async def _reachable(settings: DatabaseSettings) -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(**settings.connect_kwargs(database=None)), timeout=5
        )
    except Exception:  # noqa: BLE001 - any failure at all means "skip these"
        return False
    conn.close()
    return True


async def _drop_database(settings: DatabaseSettings) -> None:
    conn = await asyncmy.connect(**settings.connect_kwargs(database=None))
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = %s",
                (settings.name,),
            )
            if await cur.fetchone():
                await cur.execute(f"DROP DATABASE `{settings.name}`")
    finally:
        conn.close()


@pytest.fixture(scope="session")
def db_settings() -> DatabaseSettings:
    settings = _test_settings()
    if not asyncio.run(_reachable(settings)):
        pytest.skip(
            f"MySQL not reachable at {settings.host}:{settings.port} "
            f"as {settings.user!r} - integration tests skipped"
        )
    return settings


@pytest.fixture
async def db(db_settings: DatabaseSettings) -> AsyncIterator[Database]:
    """A migrated, empty throwaway schema, dropped afterwards."""
    await _drop_database(db_settings)
    await ensure_database_exists(db_settings)
    database = Database(db_settings)
    await database.connect()
    await Migrator(database).upgrade()
    try:
        yield database
    finally:
        await database.close()
        await _drop_database(db_settings)


_INSERT = (
    "INSERT INTO backtest_runs "
    "(model_version, market_code, interval_code, period_start, period_end, "
    " resolved, direction_correct, net_pnl, gross_pnl, total_costs, "
    " per_asset, known_optimism, cost_basis) "
    "VALUES ('test', 'CRYPTO', %s, %s, %s, 10, 5, -400.00, 100.00, 500.00, "
    "        '[]', '[]', %s)"
)


async def _insert_run(db: Database, *, basis: str, interval: str = "1d") -> None:
    await db.execute(
        _INSERT,
        interval,
        START.replace(tzinfo=None),
        (START + timedelta(days=30)).replace(tzinfo=None),
        basis,
    )


class _Result:
    def __init__(self) -> None:
        self.start = START
        self.end = START + timedelta(days=30)


class _Run:
    """The shape ``record_backtest`` reads, no more."""

    market = Market.CRYPTO
    interval = Horizon.D1
    split = None

    def __init__(self) -> None:
        self.results = [_Result()]

    def to_dict(self) -> dict[str, object]:
        return {
            "holdout_included": False,
            "walk_forward": None,
            "per_asset": [],
            "combined": {
                "decisions_simulated": 40,
                "published": 20,
                "resolved": 10,
                "direction_correct": 5,
                "known_optimism": [],
                "paper_trades": {
                    "net_pnl": "-400.00",
                    "gross_pnl": "100.00",
                    "total_costs": "500.00",
                },
            },
        }


class _Store:
    """Governance needs a store object even when nothing is persisted."""

    async def record_question(self, question: object) -> None:  # pragma: no cover
        raise AssertionError("persist=False must not write")

    async def close_absent(self, keys: list[str]) -> int:  # pragma: no cover
        raise AssertionError("persist=False must not write")


@pytest.mark.integration
class TestRecentRunsFiltersByRegime:
    async def test_a_run_from_a_withdrawn_fee_schedule_is_not_returned(
        self, db: Database
    ) -> None:
        """The whole finding in one assertion: without the filter these three
        rows are 'the three most recent runs' and governance reads all of
        them."""
        await _insert_run(db, basis=WITHDRAWN_BASIS)
        await _insert_run(db, basis="")
        await _insert_run(db, basis=cost_basis(Market.CRYPTO))

        runs = await BacktestRepository(db).recent_runs(limit=5)

        assert len(runs) == 1
        assert runs[0]["cost_basis"] == cost_basis(Market.CRYPTO)

    async def test_a_run_written_before_the_marker_existed_stays_unread(
        self, db: Database
    ) -> None:
        """DEFAULT '' is the safe direction: 'we cannot tell what this was
        measured under' has to resolve to silence, not to a guess."""
        await _insert_run(db, basis="")
        assert await BacktestRepository(db).recent_runs(limit=5) == []

    async def test_what_the_repository_writes_is_what_it_will_read_back(
        self, db: Database
    ) -> None:
        """Writer and reader agree only if both use the same fingerprint. Stop
        writing the basis and this run becomes invisible to governance - a
        backtest that ran, stored, and then silently stopped counting."""
        store = BacktestRepository(db, model_version="test")
        await store.record_backtest(_Run())

        runs = await store.recent_runs(limit=5)
        assert len(runs) == 1
        assert runs[0]["cost_basis"] == cost_basis(Market.CRYPTO)
        assert runs[0]["market"] == "CRYPTO"

    async def test_governance_raises_no_question_from_a_withdrawn_regime(
        self, db: Database
    ) -> None:
        """The live path, end to end: repository -> GovernanceService.research
        -> research question. Unit-testing the SQL string would prove nothing
        about what governance actually receives."""
        await _insert_run(db, basis=WITHDRAWN_BASIS)
        service = GovernanceService(
            store=_Store(), backtest=BacktestRepository(db)
        )

        result = await service.research(persist=False)
        assert result.questions == []

    async def test_governance_does_raise_one_from_the_current_regime(
        self, db: Database
    ) -> None:
        """The other half: the filter must not be a blanket refusal. Same
        numbers, current basis - the question appears, and it carries the basis
        so nobody has to guess what currency the PnL is in."""
        basis = cost_basis(Market.CRYPTO)
        await _insert_run(db, basis=basis)
        service = GovernanceService(
            store=_Store(), backtest=BacktestRepository(db)
        )

        result = await service.research(persist=False)
        keys = {q.key for q in result.questions}
        assert "costs_exceed_edge_1d" in keys
        question = next(q for q in result.questions if q.key == "costs_exceed_edge_1d")
        assert any(basis in line for line in question.evidence)
