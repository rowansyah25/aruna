"""Forward-only SQL migration runner (MySQL).

Files live in ``migrations/`` and are named ``NNNN_description.sql``.  Each is
applied exactly once and its SHA-256 is recorded.  If a file that has already
been applied is later edited, the checksum stops matching and the runner
refuses to continue - a schema that silently disagrees with the code that reads
it is the failure mode this exists to prevent.

There is no down-migration.  Rolling a schema back on a system whose premise is
immutable prediction records (SPEC 20) would mean destroying evidence;
corrections go forward as new migrations.

**Migrations are not atomic on MySQL.** Every DDL statement implicitly commits,
so a file that fails halfway leaves the statements before the failure applied.
The runner does not pretend otherwise: it reports exactly which statement
failed, refuses to record the migration, and tells the operator what to inspect.
Keeping each migration small is the mitigation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from aruna.core.clock import elapsed_ms, monotonic
from aruna.core.config import PROJECT_ROOT
from aruna.core.errors import DatabaseError, MigrationError
from aruna.core.logging import get_logger
from aruna.db.pool import Database
from aruna.db.types import as_utc

log = get_logger("aruna.db.migrate")

DEFAULT_MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

_FILENAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

_MIGRATIONS_TABLE_SQL = """
CREATE TABLE schema_migrations (
    version       VARCHAR(16)  NOT NULL,
    name          VARCHAR(128) NOT NULL,
    checksum      CHAR(64)     NOT NULL,
    applied_at    DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    execution_ms  INT          NOT NULL,
    PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
"""


# ---------------------------------------------------------------------------
# Statement splitting
# ---------------------------------------------------------------------------


def split_statements(sql: str) -> list[str]:
    """Split a migration file into executable statements.

    MySQL's client protocol takes one statement per round trip, so the file has
    to be cut up before it can run.  Splitting naively on ``;`` would break on a
    semicolon inside a string literal or a comment, so this walks the text
    tracking quoting state, and strips comments on the way through.

    Compound bodies (``BEGIN ... END``) are deliberately unsupported: they need
    ``DELIMITER`` handling, which is a client-side convention rather than SQL.
    ARUNA's migrations use single-statement trigger bodies instead.
    """
    statements: list[str] = []
    current: list[str] = []
    index = 0
    length = len(sql)
    quote: str | None = None

    while index < length:
        char = sql[index]
        nxt = sql[index + 1] if index + 1 < length else ""

        if quote is not None:
            current.append(char)
            if char == "\\" and quote in ("'", '"'):
                # Backslash escape: consume the next character verbatim.
                if nxt:
                    current.append(nxt)
                    index += 2
                    continue
            elif char == quote:
                if nxt == quote:  # doubled quote is a literal quote
                    current.append(nxt)
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if char in ("'", '"', "`"):
            quote = char
            current.append(char)
            index += 1
            continue

        if char == "-" and nxt == "-" and (index + 2 >= length or sql[index + 2] in " \t\r\n"):
            index = _skip_to_newline(sql, index)
            continue

        if char == "#":
            index = _skip_to_newline(sql, index)
            continue

        if char == "/" and nxt == "*":
            end = sql.find("*/", index + 2)
            index = length if end == -1 else end + 2
            current.append(" ")
            continue

        if char == ";":
            statements.append("".join(current))
            current = []
            index += 1
            continue

        current.append(char)
        index += 1

    statements.append("".join(current))
    return [cleaned for statement in statements if (cleaned := statement.strip())]


def _skip_to_newline(sql: str, index: int) -> int:
    end = sql.find("\n", index)
    return len(sql) if end == -1 else end + 1


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        return f"{self.version}_{self.name}"

    def statements(self) -> list[str]:
        return split_statements(self.sql)


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: str
    name: str
    checksum: str
    applied_at: datetime | None
    execution_ms: int


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    applied: tuple[AppliedMigration, ...]
    pending: tuple[Migration, ...]
    current_version: str | None

    @property
    def is_up_to_date(self) -> bool:
        return not self.pending


def discover_migrations(directory: Path | None = None) -> tuple[Migration, ...]:
    """Read and validate every migration file, ordered by version."""
    target = directory or DEFAULT_MIGRATIONS_DIR
    if not target.is_dir():
        raise MigrationError(f"migrations directory not found: {target}")

    migrations: list[Migration] = []
    seen: dict[str, Path] = {}

    for path in sorted(target.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if not match:
            raise MigrationError(
                f"migration filename {path.name!r} is malformed; "
                "expected NNNN_lower_snake_case.sql"
            )
        version = match.group("version")
        if version in seen:
            raise MigrationError(
                f"duplicate migration version {version}: "
                f"{seen[version].name} and {path.name}"
            )
        seen[version] = path

        sql = path.read_text(encoding="utf-8")
        if not sql.strip():
            raise MigrationError(f"migration {path.name} is empty")

        migration = Migration(
            version=version, name=match.group("name"), path=path, sql=sql
        )
        if not migration.statements():
            raise MigrationError(
                f"migration {path.name} contains no executable statements "
                "(only comments?)"
            )
        migrations.append(migration)

    if not migrations:
        raise MigrationError(f"no migration files found in {target}")
    return tuple(migrations)


class Migrator:
    def __init__(self, db: Database, directory: Path | None = None) -> None:
        self._db = db
        self._dir = directory or DEFAULT_MIGRATIONS_DIR

    async def ensure_tracking_table(self) -> None:
        """Create ``schema_migrations`` if it is missing.

        Checks ``information_schema`` rather than using ``IF NOT EXISTS``:
        MySQL answers that with a Note, which the driver logs as a warning on
        every single startup.  A real check is both quieter and clearer.
        """
        exists = await self._db.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = 'schema_migrations'"
        )
        if not exists:
            await self._db.execute(_MIGRATIONS_TABLE_SQL)

    async def applied(self) -> tuple[AppliedMigration, ...]:
        await self.ensure_tracking_table()
        rows = await self._db.fetch(
            "SELECT version, name, checksum, applied_at, execution_ms "
            "FROM schema_migrations ORDER BY version"
        )
        return tuple(
            AppliedMigration(
                version=row["version"],
                name=row["name"],
                checksum=row["checksum"],
                applied_at=as_utc(row["applied_at"]),
                execution_ms=row["execution_ms"],
            )
            for row in rows
        )

    async def status(self) -> MigrationStatus:
        on_disk = discover_migrations(self._dir)
        applied = await self.applied()
        self._verify_checksums(on_disk, applied)

        applied_versions = {row.version for row in applied}
        pending = tuple(m for m in on_disk if m.version not in applied_versions)
        current = max(applied_versions) if applied_versions else None
        return MigrationStatus(applied=applied, pending=pending, current_version=current)

    async def upgrade(self, *, dry_run: bool = False) -> tuple[Migration, ...]:
        """Apply every pending migration.  Returns what was (or would be) applied."""
        status = await self.status()
        if not status.pending:
            log.info("migrate.up_to_date", version=status.current_version)
            return ()

        if dry_run:
            log.info("migrate.dry_run", pending=[m.label for m in status.pending])
            return status.pending

        for migration in status.pending:
            await self._apply(migration)
        return status.pending

    async def _apply(self, migration: Migration) -> None:
        statements = migration.statements()
        started = monotonic()

        for position, statement in enumerate(statements, start=1):
            try:
                await self._db.execute(statement)
            except DatabaseError as exc:
                raise MigrationError(
                    f"migration {migration.label} failed at statement "
                    f"{position} of {len(statements)}.\n"
                    f"  {exc}\n"
                    f"  MySQL commits DDL implicitly, so statements 1-{position - 1} "
                    "of this file ARE already applied. The migration has not been "
                    "recorded, so re-running would replay them and fail again - "
                    "inspect the schema and undo the partial change first."
                ) from exc

        await self._db.execute(
            "INSERT INTO schema_migrations (version, name, checksum, execution_ms) "
            "VALUES (%s, %s, %s, %s)",
            migration.version,
            migration.name,
            migration.checksum,
            int(elapsed_ms(started)),
        )
        log.info(
            "migrate.applied",
            version=migration.version,
            name=migration.name,
            statements=len(statements),
            duration_ms=elapsed_ms(started),
        )

    @staticmethod
    def _verify_checksums(
        on_disk: tuple[Migration, ...], applied: tuple[AppliedMigration, ...]
    ) -> None:
        by_version = {m.version: m for m in on_disk}
        for record in applied:
            migration = by_version.get(record.version)
            if migration is None:
                raise MigrationError(
                    f"migration {record.version}_{record.name} is recorded as applied "
                    "but its file is missing; the database is ahead of this checkout"
                )
            if migration.checksum != record.checksum:
                raise MigrationError(
                    f"migration {migration.label} was modified after it was applied "
                    f"(expected checksum {record.checksum[:12]}..., file is "
                    f"{migration.checksum[:12]}...). Add a new migration instead of "
                    "editing an applied one."
                )


__all__ = [
    "DEFAULT_MIGRATIONS_DIR",
    "AppliedMigration",
    "Migration",
    "MigrationStatus",
    "Migrator",
    "discover_migrations",
    "split_statements",
]
