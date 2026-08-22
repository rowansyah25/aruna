"""The seams between the upkeep loop and the things it drives.

Every fix in this round was proven on one side of a seam. The loop's shutdown
grace was proven against a *fake* resolver; the resolution write order was
proven against a *fake* store with no loop above it; the ``unavailable_interval``
counter was proven three times, once per file, never once end to end. Each proof
is real and none of them covers the thing the fixes were written for, because
the failure only exists where two files meet.

That is the defect this whole review is about, one level up: code that is
written, exported, unit-tested, and never reached on the live path. A seam that
only fake objects have ever crossed is the same miss as a guard that never held
anything back.

So these tests run the **real** :class:`UpkeepLoop` over the **real**
:class:`SignalService` over the **real** :class:`UpkeepCheck`, and assert on the
state a database would actually be left holding. The fakes here are allowed to
lie about I/O and about *when* it completes - never about rules. ``_GatedStore``
enforces the same constraints the schema does: ``paper_results`` carries
``UNIQUE KEY paper_results_signal`` against a plain ``INSERT`` (SPEC 22 forbids
re-scoring, so it deliberately has no ``ON DUPLICATE KEY UPDATE``), while
``paper_trades`` upserts.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.app import UPKEEP_SHUTDOWN_TIMEOUT_SEC, _safe
from aruna.core.config import UpkeepSettings
from aruna.core.enums import Decision, HealthStatus, Horizon, Market
from aruna.core.errors import DatabaseError
from aruna.health.upkeep import UpkeepCheck
from aruna.signals.models import LockedSignal, SignalStatus
from aruna.signals.service import SignalService
from aruna.upkeep.candles import RefreshResult
from aruna.upkeep.loop import STOP_GRACE_SEC, UpkeepLoop

#: Fixed and in the past; resolution reads ``moment``, never the wall clock.
NOW = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)

#: Long after the horizon closes, so ``due()`` offers the signal.
LATER = NOW + timedelta(days=1, hours=1)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Asset:
    id = 7
    symbol = "BTC/USDT"


class _Deliberation:
    async def find_asset(self, market: Market, symbol: str) -> _Asset:
        return _Asset()


class _MarketData:
    """Stored candles per interval, oldest-first, newest ``limit`` rows."""

    def __init__(self, series: dict[Horizon, list[tuple[datetime, Decimal]]]) -> None:
        self._series = series

    async def candles(
        self,
        asset_id: int,
        interval: Horizon,
        *,
        limit: int = 500,
        closed_only: bool = True,
    ) -> list[dict[str, object]]:
        rows = [
            {"close_time": moment, "close": price}
            for moment, price in self._series.get(interval, [])
        ]
        return rows[-limit:]


class _GatedStore:
    """A signal store whose writes can be suspended mid-pass.

    ``hang_on`` names the write that blocks for ever the first time it is
    reached. That is how a shutdown finds a resolution pass in the real world -
    wedged on a socket - and it is the only way to observe *which* writes had
    already landed when the cancel arrived.
    """

    def __init__(self, signals: list[LockedSignal], *, hang_on: str | None = None) -> None:
        self._signals = {s.signal_id: s for s in signals}
        self.status = {s.signal_id: SignalStatus.LOCKED for s in signals}
        self.outcomes: dict[str, Any] = {}
        self.trades: dict[str, Any] = {}
        self.samples: dict[str, list] = {}
        self.calls: list[str] = []
        self._hang_on = hang_on
        self._hung = False
        #: Set the moment the first write of a pass is reached, so a test can
        #: shut the loop down while the pass is provably in flight.
        self.entered = asyncio.Event()
        #: Released by the test to let a suspended write complete.
        self.release = asyncio.Event()

    async def _gate(self, name: str) -> None:
        self.calls.append(name)
        self.entered.set()
        if self._hang_on == name and not self._hung:
            self._hung = True
            await asyncio.Event().wait()  # never returns; only a cancel ends it
        await asyncio.sleep(0)  # a real write yields, so a cancel can land here

    # -- reads ----------------------------------------------------------

    async def due(self, *, reference: datetime, limit: int = 50) -> list[str]:
        # The real query filters status = 'LOCKED'. A RESOLVED signal is never
        # offered again - which is why the status flip has to be the last write.
        return [
            signal_id
            for signal_id, signal in self._signals.items()
            if self.status[signal_id] is SignalStatus.LOCKED
            and reference >= signal.resolves_at
        ][:limit]

    async def get(self, signal_id: str) -> tuple[LockedSignal, str] | None:
        signal = self._signals.get(signal_id)
        return (signal, signal.fingerprint) if signal else None

    # -- writes ---------------------------------------------------------

    async def record_samples(self, samples: list) -> int:
        await self._gate("record_samples")
        if samples:  # outcome_snapshots upserts
            self.samples[samples[0].signal_id] = list(samples)
        return len(samples)

    async def record_trade(self, trade) -> None:
        await self._gate("record_trade")
        self.trades[trade.signal_id] = trade  # paper_trades upserts

    async def record_outcome(self, outcome) -> None:
        await self._gate("record_outcome")
        if outcome.signal_id in self.outcomes:
            # UNIQUE KEY paper_results_signal against a plain INSERT.
            driver = Exception(
                1062,
                f"Duplicate entry '{outcome.signal_id}' for key "
                "'paper_results.paper_results_signal'",
            )
            error = DatabaseError(
                f"query failed (IntegrityError: {driver}) | sql: INSERT INTO "
                "paper_results (signal_id, original_direction, ...) VALUES (...)"
            )
            error.__cause__ = driver
            raise error
        self.outcomes[outcome.signal_id] = outcome

    async def set_status(
        self,
        signal_id: str,
        status: SignalStatus,
        *,
        resolved_at: datetime | None = None,
        superseded_by: str | None = None,
        # Ditutupnya sebuah prediksi menyimpan alasan terukurnya di baris itu.
        # Ganda uji yang tidak menerimanya akan melempar TypeError dan membuat
        # kegagalan penulisan terbaca seperti kegagalan resolusi.
        withheld_reason: str | None = None,
    ) -> None:
        await self._gate(f"set_status({status.value})")
        self.status[signal_id] = status


class _IdleRefresher:
    """Stands in for ``CandleRefresher``. These tests are about resolution."""

    def __init__(self) -> None:
        self.passes = 0

    async def refresh(self, *, now: datetime) -> RefreshResult:
        self.passes += 1
        return RefreshResult()

    def state(self) -> dict[str, dict[str, Any]]:
        return {}


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _signal(**overrides) -> LockedSignal:
    base = {
        "signal_id": "int0000000000001",
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "horizon": Horizon.D1,
        "direction": Decision.BUY,
        "confidence": 0.7,
        "reference_price": Decimal(1000),
        "entry_price": Decimal(1000),
        "target_price": Decimal(1100),
        "expected_move_pct": 10.0,
        "locked_at": NOW,
        "as_of": NOW - timedelta(minutes=1),
        "resolves_at": NOW + timedelta(days=1),
        "reasoning": ("because the test said so",),
    }
    return LockedSignal(**(base | overrides))


def _scoreable_series() -> _MarketData:
    """1h bars covering the whole horizon and still arriving at ``LATER``."""
    return _MarketData(
        {
            Horizon.H1: [
                (NOW + timedelta(hours=1) + Horizon.H1.duration * i, Decimal(1000 + i))
                for i in range(25)
            ]
        }
    )


def _service(store: _GatedStore, market_data: _MarketData | None = None) -> SignalService:
    return SignalService(
        deliberation=_Deliberation(),
        market_data=market_data or _scoreable_series(),
        store=store,
        model_version="test",
    )


def _loop(resolver: Any, refresher: Any = None, **overrides: object) -> UpkeepLoop:
    return UpkeepLoop(
        refresher=refresher or _IdleRefresher(),  # type: ignore[arg-type]
        resolver=resolver,
        settings=UpkeepSettings(_env_file=None, **overrides),  # type: ignore[arg-type]
    )


@asynccontextmanager
async def _cycled(loop: UpkeepLoop) -> AsyncIterator[UpkeepLoop]:
    """Run the loop as a real task until it has completed exactly one cycle.

    ``UpkeepCheck`` reports DOWN for a loop that is not running, and correctly
    so - which means assertions about what a *running* loop tells the operator
    cannot be made by calling ``cycle()`` directly. The default ``tick_sec`` is
    long enough that the loop parks on the stop event after its first cycle, so
    the counters under test stay still while they are being read.
    """
    await loop.start()
    try:
        for _ in range(2000):
            if loop.stats.cycles:
                break
            await asyncio.sleep(0.005)
        else:  # pragma: no cover - a hung loop is a failure, not a slow test
            raise AssertionError("the loop never completed a cycle")
        yield loop
    finally:
        await loop.stop(grace_sec=5)


# ---------------------------------------------------------------------------
# shutdown vs. the resolution write sequence
# ---------------------------------------------------------------------------


class TestShutdownDoesNotLoseThePaperTrade:
    """Two fixes, two files, one failure - so the proof has to span both.

    ``UpkeepLoop.stop`` no longer cancels the cycle in flight, and
    ``SignalService._resolve_one`` no longer flips the status before the trade
    is written. Either one alone still loses a paper trade: without the grace
    the pass is cut wherever it happens to be, and without the ordering the cut
    lands on a signal that is already RESOLVED and that ``due()`` will never
    offer again. The database that motivated all of this held 31 resolutions
    against a single paper trade.
    """

    async def test_a_shutdown_lets_the_pass_in_flight_finish_its_writes(self) -> None:
        signal = _signal()
        store = _GatedStore([signal])
        loop = _loop(_service(store), tick_sec=0.01, resolve_limit=5)

        await loop.start()
        # The pass is provably mid-write before shutdown is asked for.
        await asyncio.wait_for(store.entered.wait(), timeout=2)
        stopper = asyncio.create_task(loop.stop(grace_sec=5))
        await asyncio.sleep(0)
        store.release.set()
        await asyncio.wait_for(stopper, timeout=5)

        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        # The write the old shutdown lost. RESOLVED without this row is the
        # unrecoverable state, because due() only ever returns LOCKED.
        assert signal.signal_id in store.trades
        assert signal.signal_id in store.outcomes
        assert loop.stats.resolved == 1
        assert not loop.running

    async def test_the_status_flip_is_the_last_write_of_the_pass(self) -> None:
        """Ordering is the only guarantee available - there is no transaction.

        ``SignalRepository`` exposes no connection-scoped variants, so the four
        writes cannot share a ``Database.transaction()``. What replaces it is an
        order in which every possible cut lands somewhere the next pass repairs.
        """
        signal = _signal()
        store = _GatedStore([signal])

        await _service(store).resolve_due(reference=LATER)

        assert store.calls[-1] == "set_status(RESOLVED)"
        assert store.calls.index("record_trade") < store.calls.index(
            "set_status(RESOLVED)"
        )

    async def test_a_forced_cancel_never_strands_a_resolved_signal(self) -> None:
        """The grace can still run out; what must not survive is a dead end.

        A cycle wedged on a socket that never times out is cancelled after the
        grace - a shutdown that hangs for ever is its own failure. The point of
        the write order is that even *that* cut lands in a state the next pass
        can finish, so the two fixes together leave no unrecoverable outcome.
        """
        signal = _signal()
        store = _GatedStore([signal], hang_on="record_trade")
        loop = _loop(_service(store), tick_sec=0.01, resolve_limit=5)

        await loop.start()
        await asyncio.wait_for(store.entered.wait(), timeout=2)
        await asyncio.wait_for(loop.stop(grace_sec=0.05), timeout=5)

        # Cut before the trade landed - so the status must not have moved.
        assert signal.signal_id not in store.trades
        assert store.status[signal.signal_id] is SignalStatus.LOCKED
        assert not loop.running

    async def test_the_next_pass_repairs_what_the_forced_cancel_left(self) -> None:
        """A cut is only survivable if something actually finishes the job.

        Recoverable-in-principle is not recoverable: the repair has to run on
        the live path, through ``due()``, and land the trade the cut lost.
        """
        signal = _signal()
        store = _GatedStore([signal], hang_on="record_outcome")
        loop = _loop(_service(store), tick_sec=0.01, resolve_limit=5)

        await loop.start()
        await asyncio.wait_for(store.entered.wait(), timeout=2)
        await asyncio.wait_for(loop.stop(grace_sec=0.05), timeout=5)

        # The cut has to land where the test says it does, or what follows
        # proves nothing: a shutdown that tore the pass apart at its first write
        # would leave a store with no outcome to collide with, and the repair
        # below would then be exercising the ordinary path.
        assert "record_outcome" in store.calls
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

        # A fresh loop over the same store: the signal is still LOCKED, so
        # `due()` offers it again and the pass runs to completion.
        repaired = _loop(_service(store), tick_sec=0.01, resolve_limit=5)
        await asyncio.wait_for(repaired.cycle(now=LATER), timeout=5)

        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        assert signal.signal_id in store.trades
        assert repaired.stats.resolved == 1

    async def test_a_repair_pass_does_not_rescore_the_stored_outcome(self) -> None:
        """SPEC 22: the first score is the record, even when the pass restarts.

        The repair tolerates the ``paper_results`` collision instead of turning
        the INSERT into an upsert. That distinction is the whole point - an
        upsert would let every retry overwrite a prediction that has already
        been scored, which is exactly what SPEC 22 forbids.
        """
        signal = _signal()
        store = _GatedStore([signal], hang_on="set_status(RESOLVED)")
        loop = _loop(_service(store), tick_sec=0.01, resolve_limit=5)

        await loop.start()
        await asyncio.wait_for(store.entered.wait(), timeout=2)
        await asyncio.wait_for(loop.stop(grace_sec=0.05), timeout=5)

        # Scored but still LOCKED: the one dangerous state the order leaves.
        assert signal.signal_id in store.outcomes, "the pass never reached the score"
        first_outcome = store.outcomes[signal.signal_id]
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

        repaired = _loop(_service(store), tick_sec=0.01, resolve_limit=5)
        await asyncio.wait_for(repaired.cycle(now=LATER), timeout=5)

        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        # Same object: the stored score was never rewritten, only the lifecycle
        # caught up.
        assert store.outcomes[signal.signal_id] is first_outcome
        assert repaired.stats.resolved == 1


class TestTheShutdownGraceSurvivesItsCaller:
    """A grace the caller truncates is a grace that does not exist.

    ``UpkeepLoop.stop`` waits up to ``STOP_GRACE_SEC`` (30s) for the cycle in
    flight. ``ArunaApplication.shutdown`` runs every shutdown step through
    ``_safe``, which cancels a step that outstays ``SHUTDOWN_STEP_TIMEOUT_SEC``
    (10s) - and cancelling ``stop()`` cancels the loop task underneath it,
    which is the exact outcome the grace was written to prevent. The two
    numbers were picked in different files, and nothing made them agree.
    """

    async def test_safe_really_does_cut_a_step_that_outstays_its_timeout(self) -> None:
        """The mechanism, at a scale a test can wait for.

        Without this, the constant below is just arithmetic about itself. This
        is the part that shows a too-small timeout does not merely log - it
        cancels the work.
        """
        cancelled = asyncio.Event()

        async def slow() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        await _safe("upkeep", slow(), timeout=0.02)

        assert cancelled.is_set()

    def test_the_upkeep_step_outlasts_the_grace_it_is_waiting_on(self) -> None:
        # Strictly greater, not equal: the forced-cancel path inside `stop()`
        # still has to deliver the cancellation and log `upkeep.stop_forced`
        # after the grace runs out, and a step killed at exactly the grace
        # would lose that warning as well as the pass.
        assert UPKEEP_SHUTDOWN_TIMEOUT_SEC > STOP_GRACE_SEC


# ---------------------------------------------------------------------------
# service -> loop -> health
# ---------------------------------------------------------------------------


class TestUnscoreableSignalsReachTheOperator:
    """Three files count the same thing; nothing had ever joined them up.

    ``SignalService`` separates ``unavailable_interval`` from
    ``awaiting_candles`` because "the data has not arrived" and "the data will
    never arrive" are opposite conditions. The loop then has to carry that
    number, and health has to say it out loud - and a batch that is 100%
    unavailable produces ``resolved=0, awaiting=0, no_prices=0``, which is
    indistinguishable from an idle system at every layer that drops it.
    """

    async def test_an_unscoreable_horizon_travels_from_service_to_health(self) -> None:
        # A 3m horizon: sampling_intervals(M3) is (M3,) and no provider stores
        # 3m, so this signal can never be scored without an operator acting.
        signal = _signal(
            horizon=Horizon.M3,
            resolves_at=NOW + timedelta(minutes=3),
        )
        store = _GatedStore([signal])
        loop = _loop(_service(store, _MarketData({})))

        async with _cycled(loop):
            # The service classified it...
            assert loop.stats.unavailable_interval == 1
            assert loop.stats.awaiting_candles == 0
            # ...menutupnya alih-alih menawarkannya lagi selamanya. Sebelumnya
            # baris ini menuntut tidak ada yang ditulis, dengan alasan prediksi
            # itu "masih bisa diskor pada prinsipnya" - dan pada prinsipnya
            # memang bisa, kalau ada yang mengubah konfigurasinya. Yang terukur
            # di lapangan adalah tidak ada yang mengubahnya, justru karena
            # keluhannya tenggelam di antara 88 baris yang sama tiap menit.
            assert store.calls == ["set_status(UNSCOREABLE)"]
            assert store.status[signal.signal_id] is SignalStatus.UNSCOREABLE
            # ...and the operator is told, rather than shown an idle-looking
            # zero.
            assert "tanpa interval yang bisa disampel" in loop.stats.summary()

            health = await UpkeepCheck(loop, background=True).check()

        assert health.status is HealthStatus.DEGRADED
        assert "tidak punya interval yang bisa disampel" in health.message
        assert health.details["stats"]["unavailable_interval"] == 1

    async def test_a_clean_pass_says_nothing_about_unavailable_intervals(self) -> None:
        """The counter has to stay quiet when there is nothing to report.

        A warning that is always on is a warning nobody reads, and this one is
        reported on a delta so that a single bad batch cannot make health
        DEGRADED for the rest of the run.
        """
        signal = _signal()
        store = _GatedStore([signal])
        loop = _loop(_service(store))

        async with _cycled(loop):
            assert loop.stats.resolved == 1
            assert loop.stats.unavailable_interval == 0
            health = await UpkeepCheck(loop, background=True).check()

        assert "interval yang bisa disampel" not in health.message
        assert health.status is HealthStatus.UP
