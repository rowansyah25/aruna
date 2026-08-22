"""Live MySQL tests.

Skipped automatically when no database is reachable, so the suite still runs on
a machine without MySQL - but everything here is a real round trip when it is
available.

Runs against ``<ARUNA_DB_NAME>_test``, created and dropped by the fixtures, so
a developer's working database is never touched.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncmy
import pytest

from aruna.core.config import DatabaseSettings
from aruna.core.enums import EventSeverity, HealthStatus, Market
from aruna.core.errors import ArunaError, DatabaseError
from aruna.core.runtime_state import KILL_SWITCH_KEY, KillSwitchState
from aruna.db.migrator import Migrator
from aruna.db.pool import Database, ensure_database_exists
from aruna.db.repositories import (
    AppStateRepository,
    AuditRepository,
    SystemEventRepository,
    TelegramSubscriberRepository,
    UniverseRepository,
)
from aruna.db.repositories.universe import AssetSpec
from aruna.health.checks import DatabaseCheck
from aruna.seed.universe import default_universe

pytestmark = pytest.mark.integration


def _test_settings() -> DatabaseSettings:
    """Database settings pointed at a throwaway schema, milik proses ini saja.

    Nama schema-nya membawa PID, dan itu bukan hiasan. Dengan nama tetap
    ``aruna_test``, dua sesi pytest yang berjalan bersamaan - dua terminal,
    satu suite di latar sambil satu test dijalankan di depan, ``pytest-xdist``
    - saling menghapus dan membuat ulang database yang sama. Yang terlihat di
    lapangan persis itu:

    * ``(1008, "Can't drop database; database doesn't exist")`` - sesi lain
      sudah menghapusnya di antara SELECT dan DROP;
    * ``(1007, "Can't create database; database exists")`` - sesi lain sudah
      membuatnya;
    * dan setiap kali test yang sama dijalankan sendirian, semuanya lulus.

    Berjam-jam terbuang mengira ini keanehan ``information_schema``. Bukan.
    Dua sesi, satu nama.
    """
    base = DatabaseSettings()
    return base.model_copy(
        update={"name": f"{base.name}_test_{os.getpid()}", "max_pool": 5}
    )


async def _reachable(settings: DatabaseSettings) -> bool:
    try:
        conn = await asyncio.wait_for(
            asyncmy.connect(**settings.connect_kwargs(database=None)), timeout=5
        )
    except Exception:  # noqa: BLE001 - any failure at all means "skip these tests"
        return False
    conn.close()
    return True


@pytest.fixture(scope="session")
def db_settings() -> AsyncIterator[DatabaseSettings]:
    """Schema test milik sesi ini, dibersihkan saat sesi berakhir.

    Pembersihan per-test saja ternyata tidak cukup. Terukur: dua schema
    tertinggal setelah dua sesi yang keduanya keluar dengan kode nol -
    ``aruna_test_32560`` dan ``aruna_test_18796``, keduanya 45 tabel kosong.
    Bukan bencana, tapi ia menumpuk satu per sesi selamanya, dan tidak ada
    yang akan memperhatikan sampai daftar database penuh nama bernomor.

    Penutup di sini menangkap sisa mana pun: test yang tidak memakai fixture
    ``db`` tapi membuat schema-nya sendiri, atau teardown terakhir yang tidak
    sempat jalan. Kalau sudah bersih, DROP-nya tidak melakukan apa-apa.
    """
    settings = _test_settings()
    if not asyncio.run(_reachable(settings)):
        pytest.skip(
            f"MySQL not reachable at {settings.host}:{settings.port} "
            f"as {settings.user!r} - integration tests skipped"
        )
    yield settings
    asyncio.run(_drop_database(settings))


@pytest.fixture
async def db(db_settings: DatabaseSettings) -> AsyncIterator[Database]:
    """A migrated, empty test database, dropped afterwards."""
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


async def _drop_database(settings: DatabaseSettings) -> None:
    """Pastikan database test tidak ada. Satu pernyataan, bukan cek-lalu-DROP.

    Versi sebelumnya menanyakan ``information_schema.SCHEMATA`` lebih dulu dan
    baru menjatuhkan DROP kalau namanya ada di sana - untuk menghindari Note
    yang dicatat driver sebagai warning tiap test. Harganya ternyata jauh lebih
    mahal daripada warning itu, dan terukur dua kali pada suite 1900 test:

    * ``(1008, "Can't drop database; database doesn't exist")`` - SELECT
      menemukannya, DROP bilang tidak ada;
    * ``(1007, "Can't create database; database exists")`` - SELECT tidak
      menemukannya sehingga DROP dilewati, lalu CREATE menemukannya.

    Dua gejala berlawanan dari satu sebab: jawaban ``information_schema`` tidak
    selalu sepakat dengan kenyataan pada saat pernyataan berikutnya dijalankan.
    Mekanisme persisnya belum diketahui, dan tidak ditebak di sini.

    Menoleransi kedua kode error bukan jawabannya. 1007 yang ditelan berarti
    database sisa beserta tabel-tabelnya ikut ke test berikutnya - dan test
    yang lulus di atas data lama adalah kegagalan yang menyamar sebagai
    keberhasilan. Yang dihapus adalah pertanyaannya: ``IF EXISTS`` menyerahkan
    pemeriksaan itu ke server, di dalam pernyataan yang sama.

    ``sql_notes = 0`` mematikan Note yang dulu jadi alasan cek manual itu ada.
    """
    conn = await asyncmy.connect(**settings.connect_kwargs(database=None))
    try:
        async with conn.cursor() as cur:
            await cur.execute("SET sql_notes = 0")
            await cur.execute(f"DROP DATABASE IF EXISTS `{settings.name}`")
    finally:
        conn.close()


# ---------------------------------------------------------------------------


class TestSessionSettings:
    """The guarantees everything else in this schema depends on."""

    async def test_every_connection_is_pinned_to_utc(self, db: Database) -> None:
        assert await db.session_timezone() == "+00:00"

    async def test_server_default_timestamps_are_utc(self, db: Database) -> None:
        """DATETIME columns carry no offset, so CURRENT_TIMESTAMP(6) defaults
        are only correct because the session is pinned."""
        server_now = await db.fetchval("SELECT NOW(6)")
        drift = abs((server_now.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds())
        assert drift < 60, f"server clock is {drift:.0f}s from python UTC"

    async def test_strict_mode_rejects_overlong_values(self, db: Database) -> None:
        """Without strict mode MySQL would truncate silently - fabricated data,
        which SPEC 4 forbids."""
        with pytest.raises(DatabaseError):
            await db.execute(
                "INSERT INTO markets (code, display_name, timezone, is_continuous, "
                "quote_currency) VALUES (%s, 'x', 'UTC', TRUE, 'USD')",
                "C" * 40,  # column is VARCHAR(16)
            )

    async def test_check_constraints_are_enforced(self, db: Database) -> None:
        """MySQL before 8.0.16 parses CHECK and ignores it; this proves the
        running server actually enforces them."""
        with pytest.raises(DatabaseError):
            await db.execute(
                "INSERT INTO system_events (instance, phase, component, event_type, "
                "severity, message, details) "
                "VALUES ('t', 1, 'c', 'e', 'CATASTROPHE', 'm', '{}')"
            )


class TestMigrations:
    async def test_schema_is_recorded_as_applied(self, db: Database) -> None:
        status = await Migrator(db).status()
        assert status.is_up_to_date
        assert status.current_version is not None
        assert len(status.applied) >= 2

    async def test_second_upgrade_is_a_no_op(self, db: Database) -> None:
        assert await Migrator(db).upgrade() == ()

    async def test_every_phase_1_table_exists(self, db: Database) -> None:
        rows = await db.fetch(
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema = DATABASE()"
        )
        tables = {row["t"] for row in rows}
        assert {
            "schema_migrations",
            "markets",
            "assets",
            "app_state",
            "system_events",
            "audit_logs",
            "telegram_subscribers",
        } <= tables

    async def test_applied_timestamps_come_back_aware(self, db: Database) -> None:
        applied = await Migrator(db).applied()
        assert applied
        for record in applied:
            assert record.applied_at is not None
            assert record.applied_at.tzinfo is not None
            assert record.execution_ms >= 0


class TestMarkets:
    async def test_only_crypto_and_idx_are_seeded(self, db: Database) -> None:
        markets = await UniverseRepository(db).markets()
        assert {m.code for m in markets} == {Market.CRYPTO, Market.IDX}

    async def test_crypto_is_continuous_and_idx_is_not(self, db: Database) -> None:
        by_code = {m.code: m for m in await UniverseRepository(db).markets()}
        assert by_code[Market.CRYPTO].is_continuous is True
        assert by_code[Market.IDX].is_continuous is False
        assert by_code[Market.IDX].timezone == "Asia/Jakarta"

    async def test_the_database_refuses_a_third_market(self, db: Database) -> None:
        """Forex must be impossible even for a direct SQL writer."""
        with pytest.raises(DatabaseError):
            await db.execute(
                "INSERT INTO markets (code, display_name, timezone, is_continuous, "
                "quote_currency) VALUES ('FOREX', 'Forex', 'UTC', TRUE, 'USD')"
            )


class TestUniverse:
    async def test_seeding_the_default_universe(self, db: Database) -> None:
        repo = UniverseRepository(db)
        count = await repo.upsert_many(list(default_universe()))
        assert count == 16
        assert await repo.counts_by_market() == {"CRYPTO": 5, "IDX": 11}

    async def test_upsert_is_idempotent(self, db: Database) -> None:
        repo = UniverseRepository(db)
        await repo.upsert_many(list(default_universe()))
        await repo.upsert_many(list(default_universe()))
        assert await repo.counts_by_market() == {"CRYPTO": 5, "IDX": 11}

    async def test_upsert_returns_the_same_id_on_update(self, db: Database) -> None:
        """LAST_INSERT_ID(id) is what makes the id meaningful after an update."""
        repo = UniverseRepository(db)
        spec = AssetSpec(
            market=Market.IDX,
            symbol="TEST",
            display_name="Test",
            asset_class="IDX_EQUITY",
        )
        first = await repo.upsert_asset(spec)
        second = await repo.upsert_asset(spec)
        assert first == second

    async def test_lookup_returns_idx_metadata(self, db: Database) -> None:
        repo = UniverseRepository(db)
        await repo.upsert_many(list(default_universe()))

        asset = await repo.find(Market.IDX, "BBCA")
        assert asset is not None
        assert asset.lot_size == 100
        assert asset.sector == "Financials"
        assert asset.tick_size is None  # no provider yet - SPEC 4
        assert asset.metadata == {}

    async def test_disabling_an_asset_hides_it_from_the_active_universe(
        self, db: Database
    ) -> None:
        repo = UniverseRepository(db)
        await repo.upsert_many(list(default_universe()))
        assert await repo.set_asset_enabled(Market.IDX, "GOTO", False) is True

        symbols = {a.symbol for a in await repo.assets(market=Market.IDX)}
        assert "GOTO" not in symbols
        assert await repo.counts_by_market() == {"CRYPTO": 5, "IDX": 10}

    async def test_reseeding_does_not_re_enable_a_disabled_asset(
        self, db: Database
    ) -> None:
        """An operator's decision to disable an asset must survive a seed run."""
        repo = UniverseRepository(db)
        await repo.upsert_many(list(default_universe()))
        await repo.set_asset_enabled(Market.IDX, "GOTO", False)
        await repo.upsert_many(list(default_universe()))

        asset = await repo.find(Market.IDX, "GOTO")
        assert asset is not None and asset.enabled is False

    async def test_unknown_market_is_rejected_by_the_foreign_key(
        self, db: Database
    ) -> None:
        with pytest.raises(DatabaseError):
            await db.execute(
                "INSERT INTO assets (market_code, symbol, display_name, asset_class, "
                "metadata) VALUES ('NASDAQ', 'AAPL', 'Apple', 'IDX_EQUITY', '{}')"
            )

    async def test_delisting_before_listing_is_rejected(self, db: Database) -> None:
        await UniverseRepository(db).upsert_asset(
            AssetSpec(
                market=Market.IDX,
                symbol="TEST",
                display_name="Test",
                asset_class="IDX_EQUITY",
            )
        )
        with pytest.raises(DatabaseError):
            await db.execute(
                "UPDATE assets SET listed_on = '2026-01-01', delisted_on = '2025-01-01' "
                "WHERE symbol = 'TEST'"
            )

    async def test_updated_at_moves_on_update(self, db: Database) -> None:
        repo = UniverseRepository(db)
        await repo.upsert_many(list(default_universe()))
        before = await db.fetchval("SELECT updated_at FROM assets WHERE symbol = 'BBCA'")
        await repo.set_asset_enabled(Market.IDX, "BBCA", False)
        after = await db.fetchval("SELECT updated_at FROM assets WHERE symbol = 'BBCA'")
        assert after > before


class TestAppState:
    async def test_json_round_trip(self, db: Database) -> None:
        repo = AppStateRepository(db)
        payload = {"active": True, "reason": "spread blowout", "count": 3}
        await repo.set("test_key", payload, actor="tester")
        assert await repo.get("test_key") == payload

    async def test_missing_key_is_none(self, db: Database) -> None:
        assert await AppStateRepository(db).get("nope") is None

    async def test_set_overwrites(self, db: Database) -> None:
        repo = AppStateRepository(db)
        await repo.set("k", {"v": 1}, actor="a")
        await repo.set("k", {"v": 2}, actor="b")
        assert await repo.get("k") == {"v": 2}

    async def test_kill_switch_survives_a_restart(self, db: Database) -> None:
        repo = AppStateRepository(db)
        original = KillSwitchState(active=True, reason="halt", actor="telegram:1")
        await repo.set(KILL_SWITCH_KEY, original.to_dict(), actor="telegram:1")

        restored = KillSwitchState.from_dict(await repo.get(KILL_SWITCH_KEY))
        assert restored.active is True
        assert restored.reason == "halt"
        assert restored.actor == "telegram:1"

    async def test_delete(self, db: Database) -> None:
        repo = AppStateRepository(db)
        await repo.set("k", {"v": 1}, actor="a")
        assert await repo.delete("k") is True
        assert await repo.get("k") is None


class TestSystemEvents:
    async def test_record_and_read_back(self, db: Database) -> None:
        repo = SystemEventRepository(db, instance="test", phase=1)
        event_id = await repo.record(
            component="database",
            event_type="HEALTH_TRANSITION",
            severity=EventSeverity.CRITICAL,
            message="connection lost",
            status=HealthStatus.DOWN,
            details={"latency_ms": None, "attempt": 3},
        )
        assert event_id > 0

        events = await repo.recent(limit=5)
        assert len(events) == 1
        assert events[0].severity is EventSeverity.CRITICAL
        assert events[0].status is HealthStatus.DOWN
        assert events[0].details["attempt"] == 3

    async def test_timestamps_come_back_aware(self, db: Database) -> None:
        """A naive timestamp is how look-ahead bugs (SPEC 24) hide."""
        repo = SystemEventRepository(db, instance="test", phase=1)
        await repo.record(
            component="c", event_type="E", severity=EventSeverity.INFO, message="m"
        )
        event = (await repo.recent())[0]
        assert event.occurred_at.tzinfo is not None
        assert abs((datetime.now(UTC) - event.occurred_at).total_seconds()) < 60

    async def test_filter_by_component(self, db: Database) -> None:
        repo = SystemEventRepository(db, instance="test", phase=1)
        await repo.record(
            component="redis", event_type="X", severity=EventSeverity.INFO, message="a"
        )
        await repo.record(
            component="database", event_type="X", severity=EventSeverity.INFO, message="b"
        )
        assert len(await repo.recent(component="redis")) == 1

    async def test_count_since(self, db: Database) -> None:
        repo = SystemEventRepository(db, instance="test", phase=1)
        await repo.record(
            component="c", event_type="E", severity=EventSeverity.ERROR, message="m"
        )
        cutoff = datetime.now(UTC) - timedelta(minutes=5)
        assert await repo.count_since(cutoff) == 1
        assert await repo.count_since(cutoff, severity=EventSeverity.ERROR) == 1
        assert await repo.count_since(cutoff, severity=EventSeverity.INFO) == 0


class TestAuditLog:
    async def test_record_and_read_back(self, db: Database) -> None:
        repo = AuditRepository(db, instance="test")
        await repo.record(
            actor="telegram:555",
            action="KILL_SWITCH_ACTIVATE",
            entity_type="runtime_state",
            entity_id="kill_switch",
            before_state={"active": False},
            after_state={"active": True},
            detail="manual",
        )
        entries = await repo.recent()
        assert entries[0].actor == "telegram:555"
        assert entries[0].result == "SUCCESS"

    async def test_denied_attempts_are_recorded(self, db: Database) -> None:
        repo = AuditRepository(db, instance="test")
        await repo.record(actor="telegram:999", action="COMMAND_KILL", result="DENIED")
        assert (await repo.recent())[0].result == "DENIED"

    async def test_the_log_cannot_be_rewritten(self, db: Database) -> None:
        """Audit rows are evidence; an UPDATE must fail loudly, not silently."""
        await AuditRepository(db, instance="test").record(actor="a", action="B")
        with pytest.raises(DatabaseError, match="append-only"):
            await db.execute("UPDATE audit_logs SET actor = 'someone else'")

    async def test_the_log_cannot_be_deleted(self, db: Database) -> None:
        await AuditRepository(db, instance="test").record(actor="a", action="B")
        with pytest.raises(DatabaseError, match="append-only"):
            await db.execute("DELETE FROM audit_logs")

    async def test_invalid_result_is_refused(self, db: Database) -> None:
        with pytest.raises(DatabaseError):
            await db.execute(
                "INSERT INTO audit_logs (instance, actor, action, result) "
                "VALUES ('t', 'a', 'B', 'MAYBE')"
            )


class TestTelegramSubscribers:
    async def test_first_contact_creates_the_row(self, db: Database) -> None:
        repo = TelegramSubscriberRepository(db)
        await repo.touch(chat_id="555", authorized=True, username="operator")

        subscriber = await repo.get("555")
        assert subscriber is not None
        assert subscriber.authorized is True
        assert subscriber.command_count == 1

    async def test_repeat_contact_increments_the_counter(self, db: Database) -> None:
        repo = TelegramSubscriberRepository(db)
        await repo.touch(chat_id="555", authorized=True, username="operator")
        await repo.touch(chat_id="555", authorized=True)

        subscriber = await repo.get("555")
        assert subscriber is not None
        assert subscriber.command_count == 2
        # COALESCE keeps what an earlier contact told us.
        assert subscriber.username == "operator"

    async def test_unauthorized_contact_is_still_recorded(self, db: Database) -> None:
        """An unknown chat probing the bot is exactly what needs a record."""
        repo = TelegramSubscriberRepository(db)
        await repo.touch(chat_id="999", authorized=False)

        subscriber = await repo.get("999")
        assert subscriber is not None and subscriber.authorized is False


class TestSignalLock:
    """SPEC 20 enforced by MySQL, not by application discipline.

    The frozen dataclass is tested in ``test_signals.py``. These prove the
    second layer: even a caller holding a raw connection cannot rewrite a
    prediction after the fact.
    """

    async def _asset(self, db: Database) -> int:
        await UniverseRepository(db).upsert_many(list(default_universe()))
        asset = await UniverseRepository(db).find(Market.CRYPTO, "BTC/USDT")
        assert asset is not None
        return asset.id

    def _signal(self, **overrides):
        from decimal import Decimal

        from aruna.core.enums import Decision, Horizon
        from aruna.signals.models import LockedSignal

        moment = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
        base = {
            "signal_id": "dbtest0000000001",
            "market": Market.CRYPTO,
            "symbol": "BTC/USDT",
            "horizon": Horizon.H1,
            "direction": Decision.BUY,
            "confidence": 0.7,
            "reference_price": Decimal(1000),
            "entry_price": Decimal(1000),
            "target_price": Decimal(1050),
            "expected_move_pct": 5.0,
            "locked_at": moment,
            "as_of": moment - timedelta(minutes=1),
            "resolves_at": moment + timedelta(hours=1),
            "reasoning": ("stored for the trigger test",),
            "model_version": "test",
        }
        return LockedSignal(**(base | overrides))

    async def test_a_locked_prediction_cannot_be_updated(self, db: Database) -> None:
        from aruna.db.repositories.signals import SignalRepository

        repo = SignalRepository(db)
        await repo.lock(await self._asset(db), self._signal(), published=True)

        with pytest.raises(DatabaseError) as exc:
            await db.execute(
                "UPDATE signal_snapshots SET confidence = 0.99 "
                "WHERE signal_id = 'dbtest0000000001'"
            )
        assert "append-only" in str(exc.value)

    async def test_a_locked_prediction_cannot_be_deleted(self, db: Database) -> None:
        from aruna.db.repositories.signals import SignalRepository

        repo = SignalRepository(db)
        await repo.lock(await self._asset(db), self._signal(), published=True)

        with pytest.raises(DatabaseError):
            await db.execute(
                "DELETE FROM signal_snapshots WHERE signal_id = 'dbtest0000000001'"
            )

    async def test_locking_the_same_id_twice_fails_loudly(self, db: Database) -> None:
        """No upsert here on purpose: a silent overwrite is the failure mode."""
        from aruna.db.repositories.signals import SignalRepository

        repo = SignalRepository(db)
        asset_id = await self._asset(db)
        await repo.lock(asset_id, self._signal(), published=True)
        with pytest.raises(DatabaseError):
            await repo.lock(asset_id, self._signal(confidence=0.9), published=True)

    async def test_evidence_from_after_the_lock_is_refused_by_the_schema(
        self, db: Database
    ) -> None:
        from aruna.db.repositories.signals import SignalRepository

        with pytest.raises(DatabaseError):
            await SignalRepository(db).lock(
                await self._asset(db),
                # SPEC 24. The application refuses this too; this proves the
                # database would catch it even if the application did not.
                self._signal(as_of=datetime(2026, 1, 6, 4, 0, tzinfo=UTC)),
                published=True,
            )

    async def test_the_status_can_advance_without_touching_the_snapshot(
        self, db: Database
    ) -> None:
        from aruna.db.repositories.signals import SignalRepository
        from aruna.signals.models import SignalStatus

        repo = SignalRepository(db)
        await repo.lock(await self._asset(db), self._signal(), published=True)
        record = await repo.get("dbtest0000000001")
        assert record is not None
        before = record[1]

        await repo.set_status(
            "dbtest0000000001",
            SignalStatus.RESOLVED,
            resolved_at=datetime(2026, 1, 5, 5, 0, tzinfo=UTC),
        )
        after = await repo.get("dbtest0000000001")
        assert after is not None
        assert after[1] == before  # fingerprint unchanged
        assert after[0].status is SignalStatus.RESOLVED

    async def test_a_withheld_verdict_is_stored_but_not_listed_as_open(
        self, db: Database
    ) -> None:
        """Everything is recorded; only what was published is presented.

        The bug this pins: /signals listed WAIT records and declined calls as
        open predictions, so a quiet market read as a screen full of live
        signals.
        """
        from aruna.core.enums import Decision
        from aruna.db.repositories.signals import SignalRepository

        repo = SignalRepository(db)
        asset_id = await self._asset(db)
        await repo.lock(
            asset_id,
            self._signal(signal_id="dbtest0000000pub"),
            published=True,
        )
        await repo.lock(
            asset_id,
            self._signal(signal_id="dbtest0000000wai", direction=Decision.WAIT),
            published=False,
            withheld_reason="verdict is WAIT, not a position",
        )
        await repo.lock(
            asset_id,
            self._signal(signal_id="dbtest0000000stl", confidence=0.2),
            published=False,
            withheld_reason="confidence 0.20 below the 0.35 lock floor",
        )

        listed = await repo.open_signals()
        assert [row["signal_id"] for row in listed] == ["dbtest0000000pub"]

        # ...but all three are on the record.
        stored = await db.fetchval("SELECT count(*) FROM signal_snapshots")
        assert stored == 3
        reason = await db.fetchval(
            "SELECT withheld_reason FROM signals WHERE signal_id = 'dbtest0000000stl'"
        )
        assert "below the 0.35 lock floor" in reason

    async def test_a_published_signal_records_no_withheld_reason(
        self, db: Database
    ) -> None:
        from aruna.db.repositories.signals import SignalRepository

        await SignalRepository(db).lock(
            await self._asset(db),
            self._signal(),
            published=True,
            withheld_reason="ignored because it was published",
        )
        reason = await db.fetchval(
            "SELECT withheld_reason FROM signals WHERE signal_id = 'dbtest0000000001'"
        )
        assert reason is None

    async def test_a_real_trade_row_is_rejected(self, db: Database) -> None:
        """SPEC 46 as a constraint, not a comment."""
        from aruna.db.repositories.signals import SignalRepository

        await SignalRepository(db).lock(
            await self._asset(db), self._signal(), published=True
        )
        with pytest.raises(DatabaseError) as exc:
            await db.execute(
                "INSERT INTO paper_trades (signal_id, market_code, symbol, "
                "direction, quantity, entry_price, entry_at, is_paper) VALUES "
                "('dbtest0000000001', 'CRYPTO', 'BTC/USDT', 'BUY', 1, 1, "
                "'2026-01-05 04:00:00', FALSE)"
            )
        assert "paper_trades_paper_only" in str(exc.value)

    async def test_round_trip_preserves_the_fingerprint(self, db: Database) -> None:
        """The stored record must still hash to what was locked (SPEC 20)."""
        from aruna.db.repositories.signals import SignalRepository
        from aruna.signals.lock import verify_integrity

        signal = self._signal()
        repo = SignalRepository(db)
        await repo.lock(await self._asset(db), signal, published=True)

        loaded, stored_fingerprint = await repo.get("dbtest0000000001")
        assert stored_fingerprint == signal.fingerprint
        verify_integrity(loaded, stored_fingerprint)  # no raise
        assert loaded.reasoning == signal.reasoning
        assert loaded.locked_at == signal.locked_at


class TestAppendOnlyTriggers:
    """Every append-only trigger must survive MySQL's message limit.

    ``SIGNAL ... SET MESSAGE_TEXT`` is capped at 128 characters. Past that
    MySQL raises 1648 'Data too long for condition item' *instead of* the
    message: the write is still refused, but the explanation - the whole reason
    for writing a custom message - is replaced by an error that reads like a
    bug in ARUNA rather than a deliberate guarantee.
    """

    MESSAGE_LIMIT = 128

    async def test_no_trigger_message_exceeds_the_mysql_limit(
        self, db: Database
    ) -> None:
        rows = await db.fetch(
            "SELECT trigger_name AS name, action_statement AS body "
            "FROM information_schema.triggers WHERE trigger_schema = DATABASE()"
        )
        assert rows, "no triggers found - the schema did not apply"

        too_long = []
        for row in rows:
            statement = row["body"]
            start = statement.find("MESSAGE_TEXT")
            if start < 0:
                continue
            quoted = statement[statement.find("'", start) + 1 :]
            message = quoted[: quoted.rfind("'")]
            if len(message) > self.MESSAGE_LIMIT:
                too_long.append((row["name"], len(message)))

        assert too_long == [], (
            "these trigger messages exceed MySQL's 128-character MESSAGE_TEXT "
            f"limit and will be replaced by error 1648: {too_long}"
        )

    async def test_the_message_actually_reaches_the_caller(
        self, db: Database
    ) -> None:
        """The end-to-end version: refuse the write *and* say why."""
        await AuditRepository(db, instance="test").record(
            actor="test", action="PROBE"
        )
        with pytest.raises(DatabaseError) as exc:
            await db.execute("UPDATE audit_logs SET actor = 'rewritten'")

        message = str(exc.value)
        assert "append-only" in message
        assert "Data too long" not in message


class TestPoolAndHealth:
    async def test_ping_reports_latency(self, db: Database) -> None:
        assert await db.ping() > 0

    async def test_pool_stats(self, db: Database) -> None:
        stats = db.pool_stats()
        assert stats["max"] == 5
        assert stats["size"] >= 1

    async def test_transaction_rolls_back_on_error(self, db: Database) -> None:
        with pytest.raises(ArunaError):
            async with db.transaction() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "INSERT INTO app_state (state_key, state_value) "
                        "VALUES ('rollback', '{}')"
                    )
                raise DatabaseError("forced failure")
        assert await AppStateRepository(db).get("rollback") is None

    async def test_health_check_reports_up_with_schema_and_timezone(
        self, db: Database
    ) -> None:
        result = await DatabaseCheck(db).check()
        assert result.status is HealthStatus.UP
        assert result.latency_ms is not None
        assert result.details["schema_version"] is not None
        assert result.details["session_timezone"] == "+00:00"

    async def test_health_check_reports_down_after_close(self, db: Database) -> None:
        await db.close()
        result = await DatabaseCheck(db).check()

        assert result.status is HealthStatus.DOWN
        # Health output reaches logs and Telegram, so it must use the masked
        # form.  With Laragon's empty root password there is nothing to mask,
        # hence the explicit check that a configured one would be.
        assert result.details["target"] == db.settings.safe_dsn
        password = db.settings.password.get_secret_value()
        if password:
            assert password not in result.details["target"]


class TestJejakPengirimanSignal:
    """Signal mana yang benar-benar sampai ke Telegram (dilaporkan operator).

    ``published`` menjawab "layak diterbitkan" dan ditulis saat penguncian;
    sesudahnya ada gerbang yang menolak signal tanpa entry, stop, atau target.
    Terukur di produksi: 80 signal ditahan gerbang itu dengan barisnya tetap
    ``published = TRUE``, jadi hasil dari kedelapan puluhnya tetap didorong.
    """

    async def _sisipkan(
        self, db: Database, signal_id: str, *, locked_at: datetime
    ) -> None:
        """Satu prediksi terkunci, lengkap dengan snapshot yang dirujuknya.

        ``signals`` punya foreign key ke ``signal_snapshots``: isi keputusannya
        tidak bisa ada tanpa bukti yang melahirkannya (SPEC 20).
        """
        await UniverseRepository(db).upsert_many(
            [a for a in default_universe() if a.symbol == "BTC/USDT"][:1]
        )
        aset = await UniverseRepository(db).find(Market.CRYPTO, "BTC/USDT")
        assert aset is not None
        await db.execute(
            "INSERT INTO signal_snapshots (signal_id, fingerprint, asset_id, "
            "market_code, symbol, horizon_code, direction, confidence, "
            "reference_price, entry_price, locked_at, as_of, resolves_at, "
            "reasoning, model_version) VALUES (%s, %s, %s, 'CRYPTO', 'BTC/USDT', "
            "'15m', 'BUY', 0.8, 100, 100, %s, %s, %s, '{}', 'uji')",
            signal_id,
            signal_id.ljust(64, "0"),
            aset.id,
            locked_at,
            locked_at,
            locked_at + timedelta(minutes=15),
        )
        await db.execute(
            "INSERT INTO signals (signal_id, asset_id, market_code, symbol, "
            "horizon_code, status, locked_at, resolves_at, published) "
            "VALUES (%s, %s, 'CRYPTO', 'BTC/USDT', '15m', 'LOCKED', %s, %s, TRUE)",
            signal_id,
            aset.id,
            locked_at,
            locked_at + timedelta(minutes=15),
        )

    async def test_baris_tanpa_jejak_tidak_pernah_lolos(
        self, db: Database
    ) -> None:
        """Aturan yang tidak menebak: hanya yang punya jejak yang punya hasil.

        Dua percobaan sebelumnya melonggarkan ini untuk baris lama, dan
        keduanya mengembalikan bug aslinya - terukur pada signal
        ``483bb3b78ad54e52``, dikunci sehari sebelum pencatatan ada, hasilnya
        sampai ke Telegram tanpa signalnya.
        """
        from aruna.db.repositories.signals import SignalRepository

        lama = datetime(2026, 8, 18, 18, 12, tzinfo=UTC)
        await self._sisipkan(db, "lama-1", locked_at=lama)

        repo = SignalRepository(db)
        assert await repo.pushed_message_ids(["lama-1"]) == {}

    async def test_yang_terkirim_lolos_dengan_id_pesannya(
        self, db: Database
    ) -> None:
        from aruna.db.repositories.signals import SignalRepository

        saat = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
        await self._sisipkan(db, "terkirim", locked_at=saat)

        repo = SignalRepository(db)
        await repo.mark_pushed("terkirim", message_id=555, at=saat)

        assert await repo.pushed_message_ids(["terkirim"]) == {"terkirim": 555}

    async def test_terkirim_tanpa_id_tetap_dianggap_sampai(
        self, db: Database
    ) -> None:
        """Pengirim yang tidak melaporkan id-nya tetap berhasil mengirim: yang
        hilang hanya kemampuan membalas, bukan hasilnya."""
        from aruna.db.repositories.signals import SignalRepository

        saat = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
        await self._sisipkan(db, "tanpa-id", locked_at=saat)

        repo = SignalRepository(db)
        await repo.mark_pushed("tanpa-id", message_id=None, at=saat)
        hasil = await repo.pushed_message_ids(["tanpa-id"])

        assert set(hasil) == {"tanpa-id"}
        assert hasil["tanpa-id"] is None

    async def test_published_saja_tidak_cukup(self, db: Database) -> None:
        """Inti bugnya, dalam satu test.

        ``_sisipkan`` menulis ``published = TRUE`` - persis keadaan signal yang
        dikeluhkan operator. Tanpa jejak pengiriman, ia tetap tidak lolos.
        """
        from aruna.db.repositories.signals import SignalRepository

        saat = datetime(2026, 8, 19, 18, 0, tzinfo=UTC)
        await self._sisipkan(db, "terbit-tanpa", locked_at=saat)

        terbit = await db.fetchval(
            "SELECT published FROM signals WHERE signal_id = %s",
            "terbit-tanpa",
        )
        assert terbit == 1

        repo = SignalRepository(db)
        assert await repo.pushed_message_ids(["terbit-tanpa"]) == {}
