"""Two writers must never be inside the same table at once (MySQL 1213).

**What broke.** ARUNA runs as two OS processes - ``aruna-run`` and
``futures-loop`` (:func:`aruna.supervisor.default_children`) - and both write
``candles`` and ``council_votes``.  ``INSERT ... ON DUPLICATE KEY UPDATE``
checks for duplicates before inserting, and that check takes a *shared gap
lock* on the record following the insert position.  Two writers whose rows are
neighbours in the unique index therefore each hold a gap lock covering the
other's insert position, and the insert-intention lock each needs next
conflicts with the other's gap lock.  Neither moves; InnoDB kills one with
error 1213 and its rows are lost.

The adjacency is structural, not unlucky.  In ``candles_unique``
``(asset_id, interval_code, open_time)`` the upkeep refresher owns ``1m`` and
the futures loop owns ``4h``, which are neighbours for every asset; across the
CRYPTO/IDX boundary asset 5's ``4h`` sits directly beside asset 6's ``15m``.  In
``council_votes_unique`` the session ids handed out inside one futures tick are
consecutive, so every concurrent pair of savers is adjacent.

**What these tests hold to.** Two ``Database`` objects mean two pools, which
mean two MySQL sessions - and two MySQL sessions are exactly what the two
processes look like from InnoDB's side.  A per-process :class:`asyncio.Lock`
cannot exclude across them; only the server-side ``GET_LOCK`` in
:meth:`aruna.db.pool.Database.write_lock` can.  So if the lock is taken out of
either call site, or ``GET_LOCK`` is taken out of ``write_lock`` itself, the two
writes overlap here and these tests fail.

The write bodies are stubbed on purpose.  What is under test is the queue in
front of them, and stubbing is what makes the overlap observable without
writing rows into the running system's tables.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from aruna.core.config import DatabaseSettings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import DatabaseError
from aruna.data.models import Candle, Provenance
from aruna.db.pool import Database
from aruna.db.repositories.council import CouncilRepository
from aruna.db.repositories.market_data import MarketDataRepository

#: Long enough that an unserialised second writer is certain to arrive while the
#: first is still inside its write, and short enough to stay a unit test.
WRITE_SEC = 0.05

NOW = datetime(2026, 8, 21, tzinfo=UTC)


class Overlap:
    """Counts how many writers were inside the guarded region at once."""

    def __init__(self) -> None:
        self.inflight = 0
        self.peak = 0
        self.writes = 0

    async def write(self) -> None:
        self.inflight += 1
        self.writes += 1
        self.peak = max(self.peak, self.inflight)
        try:
            await asyncio.sleep(WRITE_SEC)
        finally:
            self.inflight -= 1


async def _connected() -> Database:
    """A pool of its own, which is one MySQL session of its own."""
    db = Database(DatabaseSettings(_env_file=None))
    await db.connect()
    return db


async def _two_databases() -> tuple[Database, Database]:
    try:
        first = await _connected()
    except DatabaseError as exc:
        pytest.skip(f"MySQL is not reachable, so cross-session locking cannot be "
                    f"exercised: {exc}")
    try:
        second = await _connected()
    except DatabaseError:  # pragma: no cover - the first one already answered
        await first.close()
        raise
    return first, second


def _bar() -> Candle:
    return Candle(
        market=Market.CRYPTO,
        symbol="BTC/USDT",
        interval=Horizon.M1,
        open_time=NOW,
        close_time=NOW + Horizon.M1.duration,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("10"),
        is_closed=True,
        provenance=Provenance(source="test", server_timestamp=NOW),
    )


async def test_candle_writes_are_serialised_across_processes() -> None:
    """The upkeep refresher and the futures loop cannot upsert at the same time."""
    upkeep, futures = await _two_databases()
    seen = Overlap()

    async def executemany(sql: str, args: Any) -> int:
        await seen.write()
        return len(list(args))

    upkeep.executemany = executemany  # type: ignore[method-assign]
    futures.executemany = executemany  # type: ignore[method-assign]

    try:
        await asyncio.gather(
            MarketDataRepository(upkeep).upsert_candles(1, [_bar()]),
            MarketDataRepository(futures).upsert_candles(2, [_bar()]),
        )
    finally:
        await upkeep.close()
        await futures.close()

    assert seen.writes == 2, "both writers must have run"
    assert seen.peak == 1, (
        "two candle upserts overlapped; neighbouring rows in candles_unique "
        "deadlock each other on the gap between them (MySQL 1213)"
    )


async def test_council_saves_are_serialised_across_processes() -> None:
    """The signal service and the futures loop cannot save a session at once."""
    signals, futures = await _two_databases()
    seen = Overlap()

    async def write(asset_id: int, verdict: Any) -> int:
        await seen.write()
        return asset_id

    left = CouncilRepository(signals, phase=10)
    right = CouncilRepository(futures, phase=10)
    left._write = write  # type: ignore[method-assign]
    right._write = write  # type: ignore[method-assign]

    try:
        await asyncio.gather(
            left.save(1, object()),  # type: ignore[arg-type]
            right.save(2, object()),  # type: ignore[arg-type]
        )
    finally:
        await signals.close()
        await futures.close()

    assert seen.writes == 2, "both savers must have run"
    assert seen.peak == 1, (
        "two council saves overlapped; consecutive session ids put their rows "
        "side by side in council_votes_unique and they deadlock (MySQL 1213)"
    )
