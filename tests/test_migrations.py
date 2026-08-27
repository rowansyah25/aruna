"""Migration discovery, statement splitting, and checksum guarding.

No database required - these exercise the file-level contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aruna.core.errors import MigrationError
from aruna.db.migrator import (
    AppliedMigration,
    Migration,
    Migrator,
    discover_migrations,
    split_statements,
)


def _strip_comments(sql: str) -> str:
    """Drop ``--`` line comments so assertions look at executable SQL only."""
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


class TestDiscovery:
    def test_repository_migrations_load(self) -> None:
        migrations = discover_migrations()
        assert migrations
        assert migrations[0].version == "0001"
        assert [m.version for m in migrations] == sorted(m.version for m in migrations)

    def test_core_schema_creates_the_phase_1_tables(self) -> None:
        sql = discover_migrations()[0].sql
        for table in (
            "markets",
            "assets",
            "app_state",
            "system_events",
            "audit_logs",
            "telegram_subscribers",
        ):
            assert f"CREATE TABLE {table}" in sql

    def test_the_original_guard_is_still_on_the_record(self) -> None:
        """0001 is history and must not be rewritten.

        FOREX was reopened in 0044, not by editing this file.  A migration
        already applied on a live database cannot be changed retroactively -
        rewriting it would leave production and source disagreeing silently.
        """
        sql = discover_migrations()[0].sql
        assert "code IN ('CRYPTO', 'IDX')" in sql

    def test_storage_admits_exactly_three_markets(self) -> None:
        """The guard was narrowed for XAUUSD, not opened wide.

        Read across ALL migrations, not just 0001: the constraint that is
        actually in force is the last one applied.  Asserting against 0001
        alone would stay green no matter what a later migration permitted.
        """
        semua = "\n".join(m.sql for m in discover_migrations())
        executable = _strip_comments(semua).upper()

        assert "CHECK (CODE IN ('CRYPTO', 'IDX', 'FOREX'))" in executable

        # The ambiguous aliases must never reach executable SQL - one legal
        # spelling means WHERE market_code = 'FOREX' cannot miss a row.
        for alias in ("'FX'", "'CURRENCY'", "'FOREIGN_EXCHANGE'"):
            assert alias not in executable

    def test_audit_log_is_append_only(self) -> None:
        sql = discover_migrations()[0].sql
        assert "audit_logs_no_update" in sql
        assert "audit_logs_no_delete" in sql
        assert sql.count("SIGNAL SQLSTATE '45000'") == 2

    def test_timestamps_are_datetime_not_timestamp(self) -> None:
        """TIMESTAMP would be re-interpreted per session timezone and cannot
        represent a date past 2038."""
        sql = _strip_comments(discover_migrations()[0].sql)
        assert "DATETIME(6)" in sql
        assert "TIMESTAMP(6) NOT NULL" not in sql
        # CURRENT_TIMESTAMP(6) as a default is fine; a TIMESTAMP column is not.
        assert " TIMESTAMP " not in sql

    def test_indexed_string_columns_are_varchar(self) -> None:
        """MySQL cannot index TEXT without a prefix length, and a prefix index
        would weaken the uniqueness this schema relies on."""
        sql = _strip_comments(discover_migrations()[0].sql)
        assert "symbol           VARCHAR(64)" in sql
        assert "code            VARCHAR(16)" in sql

    def test_tables_are_innodb_utf8mb4(self) -> None:
        sql = discover_migrations()[0].sql
        assert sql.count("ENGINE=InnoDB") == 6
        assert "CHARSET=utf8mb4" in sql

    def test_no_migration_seeds_the_idx_universe(self) -> None:
        """SPEC 3: the universe is configurable, so it must not be baked into
        a migration."""
        joined = _strip_comments("\n".join(m.sql for m in discover_migrations()))
        assert "BBCA" not in joined
        assert "BTC/USDT" not in joined

    def test_missing_directory_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationError, match="not found"):
            discover_migrations(tmp_path / "nope")

    def test_empty_directory_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(MigrationError, match="no migration files"):
            discover_migrations(tmp_path)

    @pytest.mark.parametrize(
        "name", ["1_core.sql", "0001-core.sql", "core.sql", "0001_Core.sql"]
    )
    def test_malformed_filenames_are_rejected(self, tmp_path: Path, name: str) -> None:
        (tmp_path / name).write_text("SELECT 1;", encoding="utf-8")
        with pytest.raises(MigrationError, match="malformed"):
            discover_migrations(tmp_path)

    def test_duplicate_versions_are_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "0001_first.sql").write_text("SELECT 1;", encoding="utf-8")
        (tmp_path / "0001_second.sql").write_text("SELECT 2;", encoding="utf-8")
        with pytest.raises(MigrationError, match="duplicate migration version"):
            discover_migrations(tmp_path)

    def test_empty_file_is_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "0001_blank.sql").write_text("   \n", encoding="utf-8")
        with pytest.raises(MigrationError, match="is empty"):
            discover_migrations(tmp_path)

    def test_comment_only_file_is_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "0001_notes.sql").write_text("-- just a note\n", encoding="utf-8")
        with pytest.raises(MigrationError, match="no executable statements"):
            discover_migrations(tmp_path)


class TestStatementSplitting:
    """MySQL takes one statement per round trip, so the file must be cut up
    before it can run - and splitting on a naive ``;`` corrupts SQL."""

    def test_simple_split(self) -> None:
        assert split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_trailing_semicolon_is_optional(self) -> None:
        assert split_statements("SELECT 1") == ["SELECT 1"]

    def test_blank_statements_are_dropped(self) -> None:
        assert split_statements("SELECT 1;;\n\n;SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_semicolon_inside_a_string_literal_is_kept(self) -> None:
        statements = split_statements("INSERT INTO t VALUES ('a;b'); SELECT 1;")
        assert statements[0] == "INSERT INTO t VALUES ('a;b')"
        assert len(statements) == 2

    def test_semicolon_inside_double_quotes_is_kept(self) -> None:
        assert len(split_statements('SELECT "a;b"; SELECT 1;')) == 2

    def test_semicolon_inside_a_backquoted_identifier_is_kept(self) -> None:
        assert len(split_statements("SELECT `weird;name` FROM t; SELECT 1;")) == 2

    def test_escaped_quote_does_not_end_the_literal(self) -> None:
        statements = split_statements(r"SELECT 'it\'s; fine'; SELECT 2;")
        assert len(statements) == 2

    def test_doubled_quote_does_not_end_the_literal(self) -> None:
        statements = split_statements("SELECT 'it''s; fine'; SELECT 2;")
        assert len(statements) == 2

    def test_line_comments_are_removed(self) -> None:
        assert split_statements("-- a comment; with semicolon\nSELECT 1;") == ["SELECT 1"]

    def test_hash_comments_are_removed(self) -> None:
        assert split_statements("# note; here\nSELECT 1;") == ["SELECT 1"]

    def test_block_comments_are_removed(self) -> None:
        assert split_statements("/* note; here */ SELECT 1;") == ["SELECT 1"]

    def test_double_dash_without_space_is_not_a_comment(self) -> None:
        """MySQL requires whitespace after -- ; ``a--b`` is arithmetic."""
        assert split_statements("SELECT 1--2;") == ["SELECT 1--2"]

    def test_unterminated_block_comment_does_not_hang(self) -> None:
        assert split_statements("SELECT 1; /* never closed") == ["SELECT 1"]

    def test_real_migrations_split_into_statements(self) -> None:
        statements = discover_migrations()[0].statements()
        assert len(statements) == 8  # 6 tables + 2 triggers
        assert all(not s.lstrip().startswith("--") for s in statements)

    def test_trigger_bodies_stay_whole(self) -> None:
        """Single-statement trigger bodies are what let ARUNA skip DELIMITER
        handling; a split inside one would produce invalid SQL."""
        triggers = [
            s for s in discover_migrations()[0].statements() if "CREATE TRIGGER" in s
        ]
        assert len(triggers) == 2
        for trigger in triggers:
            assert "SIGNAL SQLSTATE" in trigger
            assert "MESSAGE_TEXT" in trigger


class TestChecksums:
    def _migration(self, sql: str) -> Migration:
        return Migration(version="0001", name="core", path=Path("0001_core.sql"), sql=sql)

    def test_checksum_is_stable_and_content_dependent(self) -> None:
        first = self._migration("SELECT 1;")
        assert first.checksum == self._migration("SELECT 1;").checksum
        assert first.checksum != self._migration("SELECT 2;").checksum

    def test_editing_an_applied_migration_is_refused(self) -> None:
        """A schema that silently disagrees with the code reading it is the
        failure this guard exists for."""
        on_disk = (self._migration("SELECT 2;"),)
        applied = (
            AppliedMigration(
                version="0001",
                name="core",
                checksum=self._migration("SELECT 1;").checksum,
                applied_at=None,
                execution_ms=1,
            ),
        )
        with pytest.raises(MigrationError, match="was modified after it was applied"):
            Migrator._verify_checksums(on_disk, applied)

    def test_a_recorded_migration_with_no_file_is_refused(self) -> None:
        applied = (
            AppliedMigration(
                version="0009",
                name="future",
                checksum="deadbeef",
                applied_at=None,
                execution_ms=1,
            ),
        )
        with pytest.raises(MigrationError, match="database is ahead"):
            Migrator._verify_checksums((), applied)

    def test_matching_checksums_pass(self) -> None:
        migration = self._migration("SELECT 1;")
        applied = (
            AppliedMigration(
                version="0001",
                name="core",
                checksum=migration.checksum,
                applied_at=None,
                execution_ms=1,
            ),
        )
        Migrator._verify_checksums((migration,), applied)
