"""asyncmy connection pool and query helpers (MySQL 8.0.16+).

Raw SQL, no ORM.  ARUNA's later phases store immutable evidence snapshots and
run analytical queries over them; hand-written SQL keeps those queries visible
and auditable, which matters more here than mapping convenience.

Every driver failure is re-raised as :class:`DatabaseError`, so no caller has
to import asyncmy to handle an outage.  Rows come back as plain dicts.

**Session settings are not left to the server.** Every connection is opened
with ``time_zone='+00:00'`` and a strict ``sql_mode`` - see
:meth:`aruna.core.config.DatabaseSettings.connect_kwargs` for why both are
correctness requirements rather than preferences.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from typing import Any

import asyncmy
from asyncmy import errors as mysql_errors
from asyncmy.cursors import DictCursor

from aruna.core.clock import elapsed_ms, monotonic
from aruna.core.config import DatabaseSettings
from aruna.core.errors import DatabaseError
from aruna.core.logging import get_logger

log = get_logger("aruna.db")

#: MySQL server error numbers ARUNA can explain better than the driver does.
ERR_ACCESS_DENIED = 1045
ERR_UNKNOWN_DATABASE = 1049
ERR_CANNOT_CONNECT = 2003
ERR_SIGNAL_RAISED = 1644

#: Errors that mean "the pooled connection is unusable", not "the query was bad".
_CONNECTION_ERRORS = (
    mysql_errors.OperationalError,
    mysql_errors.InterfaceError,
)

#: How long a writer waits its turn at a serialised table before giving up.
#:
#: Generous next to the write it guards - a candle upsert is ~20 ms measured -
#: and deliberately shorter than ``innodb_lock_wait_timeout`` (50s), so a queue
#: that has genuinely stopped moving is reported as a queue rather than
#: resurfacing as a row-lock timeout with no hint of who was ahead of it.
WRITE_LOCK_TIMEOUT_SEC = 30


def error_hint(exc: BaseException) -> str:
    """Guidance appended to a connection failure.

    The driver's own message names a socket problem even when the real cause is
    a wrong password, so operators end up debugging the network. Say what it
    usually is instead.
    """
    errno = _errno(exc)
    if errno == ERR_ACCESS_DENIED:
        return "\n  -> check ARUNA_DB_USER and ARUNA_DB_PASSWORD in .env"
    if errno == ERR_UNKNOWN_DATABASE:
        return "\n  -> the database does not exist. Run: python -m aruna createdb"
    if errno == ERR_CANNOT_CONNECT or isinstance(exc, (TimeoutError, OSError)):
        return (
            "\n  -> nothing answered on that host and port. Check that MySQL is "
            "running (Laragon: Start All) and that ARUNA_DB_HOST/ARUNA_DB_PORT match."
        )
    return ""


def _errno(exc: BaseException) -> int | None:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


def is_append_only_violation(exc: BaseException) -> bool:
    """True when a SIGNAL from an append-only trigger rejected the write."""
    return _errno(exc) == ERR_SIGNAL_RAISED


class Database:
    """Owns the connection pool for one MySQL schema."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._pool: Any | None = None
        self._connect_lock = asyncio.Lock()
        #: One per serialised table - see :meth:`write_lock`.  Held before the
        #: server-side lock so that a process with twenty tasks queueing for the
        #: same table occupies one pooled connection rather than twenty.
        self._write_locks: dict[str, asyncio.Lock] = {}

    # ---- lifecycle -----------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise DatabaseError("database pool is not connected; call connect() first")
        return self._pool

    @property
    def settings(self) -> DatabaseSettings:
        return self._settings

    async def connect(self) -> None:
        """Open the pool.  Safe to call more than once."""
        async with self._connect_lock:
            if self.is_connected:
                return
            started = monotonic()
            try:
                self._pool = await asyncmy.create_pool(
                    minsize=self._settings.min_pool,
                    maxsize=self._settings.max_pool,
                    **self._settings.connect_kwargs(),
                )
            except (TimeoutError, OSError, mysql_errors.MySQLError) as exc:
                # safe_dsn, not dsn: the password must not reach the log.
                raise DatabaseError(
                    f"cannot connect to MySQL at {self._settings.safe_dsn}: "
                    f"{exc}{error_hint(exc)}"
                ) from exc
            log.info(
                "database.connected",
                target=self._settings.safe_dsn,
                min_pool=self._settings.min_pool,
                max_pool=self._settings.max_pool,
                connect_ms=elapsed_ms(started),
            )

    async def close(self) -> None:
        if self._pool is None:
            return
        pool, self._pool = self._pool, None
        pool.close()
        await pool.wait_closed()
        log.info("database.closed")

    # ---- health --------------------------------------------------------

    async def ping(self, timeout: float = 5.0) -> float:
        """Round-trip a trivial query.  Returns latency in milliseconds."""
        started = monotonic()
        try:
            async with asyncio.timeout(timeout):
                await self.fetchval("SELECT 1")
        except TimeoutError as exc:
            raise DatabaseError(f"database ping timed out after {timeout}s") from exc
        return elapsed_ms(started)

    async def server_version(self) -> str:
        return await self.fetchval("SELECT VERSION()") or "unknown"

    async def session_timezone(self) -> str:
        """The session offset actually in force.

        Health surfaces this because everything ARUNA stores assumes UTC, and a
        connection that somehow opened without the pin would corrupt timestamps
        quietly rather than fail.
        """
        return await self.fetchval("SELECT @@session.time_zone") or "unknown"

    def pool_stats(self) -> dict[str, int]:
        if self._pool is None:
            return {"size": 0, "idle": 0, "min": 0, "max": 0}
        return {
            "size": self._pool.size,
            "idle": self._pool.freesize,
            "min": self._pool.minsize,
            "max": self._pool.maxsize,
        }

    # ---- connections ---------------------------------------------------

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[Any]:
        async with self.pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Any]:
        """A connection inside a transaction; rolls back on any exception.

        DML only.  MySQL implicitly commits before and after every DDL
        statement, so wrapping schema changes in this buys nothing - see
        :mod:`aruna.db.migrator`, which does not pretend otherwise.
        """
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                yield conn
            except BaseException:
                await conn.rollback()
                raise
            else:
                await conn.commit()

    @asynccontextmanager
    async def write_lock(
        self, table: str, *, timeout: float = WRITE_LOCK_TIMEOUT_SEC
    ) -> AsyncIterator[None]:
        """One writer at a time for ``table``, across every ARUNA process.

        **What this is for, and why an asyncio lock alone is not enough.**
        ARUNA runs as two processes - ``aruna-run`` and ``futures-loop``, see
        :func:`aruna.supervisor.default_children` - and both write ``candles``
        and ``council_votes``.  Two writers whose rows land in *neighbouring*
        regions of a unique index deadlock each other in InnoDB, and the
        adjacency is structural rather than unlucky:

        * ``candles_unique`` is ``(asset_id, interval_code, open_time)``.  The
          upkeep refresher owns ``1m/15m/1h/1d`` and the futures loop owns
          ``4h``, so per asset ``1m`` and ``4h`` are neighbours; at the
          CRYPTO/IDX asset boundary (asset 5 ``4h``, asset 6 ``15m``) the two
          processes' regions touch directly.
        * ``council_votes_unique`` is ``(council_session_id, role)``, and the
          session ids allocated inside one futures tick are consecutive
          (measured: 13814-13825), so every concurrent pair is adjacent.

        ``INSERT ... ON DUPLICATE KEY UPDATE`` checks for duplicates before it
        inserts, and that check takes a **shared gap lock** on the record
        following the insert position.  Shared gap locks do not conflict with
        each other, so both writers get one covering the same boundary gap; the
        insert-intention lock each of them needs next *does* conflict with the
        other's gap lock, and neither can move.  MySQL's own record of it -
        ``lock mode S locks gap before rec`` held, ``lock_mode X ... insert
        intention waiting`` wanted, both on the same record - is what identified
        this.  Note that dropping to READ COMMITTED does not help: duplicate-key
        checking is one of the two cases where MySQL keeps gap locking on.

        So the contention is removed by removing the concurrency, which is what
        :mod:`aruna.data.ingest` already claims to do ("penulisan tetap satu per
        satu, jadi tidak ada dua transaksi yang bisa saling mengunci") and only
        ever achieved *inside* a single ``backfill()`` call.  This makes the
        same promise hold between callers and between processes.

        Cheap on purpose: only the write is serialised, never the fetch that
        precedes it - fetching is ninety percent of a refresh and is safe to run
        concurrently.  The cost is two round trips against a ~20 ms write.

        ``GET_LOCK`` is held on the connection that takes it and released on the
        same one, so the lock is a property of a live session: if a process is
        killed mid-write, MySQL drops the lock when the connection dies rather
        than leaving the other process blocked for ever.
        """
        local = self._write_locks.get(table)
        if local is None:
            local = self._write_locks[table] = asyncio.Lock()
        # Namespaced by schema: GET_LOCK names are global to the MySQL server,
        # so a second ARUNA schema on the same server must not queue behind this
        # one for a table it does not share.
        name = f"aruna:{self._settings.name}:{table}"
        async with local, self.pool.acquire() as conn, conn.cursor(DictCursor) as cur:
            try:
                await cur.execute("SELECT GET_LOCK(%s, %s)", (name, int(timeout)))
                row = await cur.fetchone()
            except (OSError, mysql_errors.MySQLError) as exc:
                raise DatabaseError(
                    f"could not take the {table} write lock: {exc}"
                ) from exc
            if not row or next(iter(row.values())) != 1:
                raise DatabaseError(
                    f"timed out after {timeout:g}s waiting for the {table} "
                    "write lock; another ARUNA process is still writing"
                )
            try:
                yield
            finally:
                # Fetched, not just executed: an unread result set left on a
                # pooled connection is inherited by whoever borrows it next.
                with suppress(OSError, mysql_errors.MySQLError):
                    await cur.execute("SELECT RELEASE_LOCK(%s)", (name,))
                    await cur.fetchone()

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[Any]:
        async with self.pool.acquire() as conn, conn.cursor(DictCursor) as cur:
            yield cur

    # ---- queries -------------------------------------------------------

    async def execute(self, sql: str, *args: Any) -> int:
        """Run a statement.  Returns the number of affected rows."""
        try:
            async with self._cursor() as cur:
                await cur.execute(sql, args or None)
                return cur.rowcount
        except (OSError, mysql_errors.MySQLError) as exc:
            raise DatabaseError(_describe(sql, exc)) from exc

    async def insert(self, sql: str, *args: Any) -> int:
        """Run an INSERT.  Returns the generated id.

        MySQL has no ``RETURNING``; ``LAST_INSERT_ID()`` is the equivalent, and
        it is per-connection so the value cannot be raced by another client.
        """
        try:
            async with self._cursor() as cur:
                await cur.execute(sql, args or None)
                return cur.lastrowid
        except (OSError, mysql_errors.MySQLError) as exc:
            raise DatabaseError(_describe(sql, exc)) from exc

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        try:
            async with self._cursor() as cur:
                await cur.execute(sql, args or None)
                return list(await cur.fetchall())
        except (OSError, mysql_errors.MySQLError) as exc:
            raise DatabaseError(_describe(sql, exc)) from exc

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        try:
            async with self._cursor() as cur:
                await cur.execute(sql, args or None)
                return await cur.fetchone()
        except (OSError, mysql_errors.MySQLError) as exc:
            raise DatabaseError(_describe(sql, exc)) from exc

    async def fetchval(self, sql: str, *args: Any) -> Any:
        row = await self.fetchrow(sql, *args)
        if not row:
            return None
        return next(iter(row.values()))

    async def executemany(self, sql: str, args: Sequence[Sequence[Any]]) -> int:
        try:
            async with self._cursor() as cur:
                await cur.executemany(sql, list(args))
                return cur.rowcount
        except (OSError, mysql_errors.MySQLError) as exc:
            raise DatabaseError(_describe(sql, exc)) from exc


def _describe(sql: str, exc: Exception) -> str:
    """One-line error text carrying enough SQL to locate the statement."""
    snippet = " ".join(sql.split())[:160]
    return f"query failed ({type(exc).__name__}: {exc}) | sql: {snippet}"


async def ensure_database_exists(settings: DatabaseSettings) -> bool:
    """Create the ARUNA schema if it is missing.

    Returns True when it created the database, False when it already existed.
    """
    try:
        conn = await asyncmy.connect(**settings.connect_kwargs(database=None))
    except (TimeoutError, OSError, mysql_errors.MySQLError) as exc:
        raise DatabaseError(
            f"cannot reach MySQL at {settings.host}:{settings.port} "
            f"as user {settings.user!r}: {exc}{error_hint(exc)}"
        ) from exc

    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                "WHERE SCHEMA_NAME = %s",
                (settings.name,),
            )
            if await cur.fetchone():
                return False
            # Identifiers cannot be parameterised; the name is validated below.
            await cur.execute(
                f"CREATE DATABASE `{_safe_identifier(settings.name)}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
        log.info("database.created", name=settings.name)
        return True
    except mysql_errors.MySQLError as exc:
        raise DatabaseError(f"could not create database {settings.name!r}: {exc}") from exc
    finally:
        conn.close()


def _safe_identifier(name: str) -> str:
    """Reject anything that could break out of a backquoted identifier."""
    if not name or not all(ch.isalnum() or ch in "_$" for ch in name):
        raise DatabaseError(
            f"invalid database name {name!r}: use letters, digits, underscore, or $"
        )
    return name


__all__ = [
    "WRITE_LOCK_TIMEOUT_SEC",
    "Database",
    "ensure_database_exists",
    "error_hint",
    "is_append_only_violation",
]
