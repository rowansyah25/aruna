"""Kill switch behaviour (SPEC 40)."""

from __future__ import annotations

import pytest

from aruna.core.enums import NoTradeReason
from aruna.core.errors import NotImplementedInPhaseError
from aruna.core.runtime_state import KillSwitchState, RuntimeState, require_phase


class TestKillSwitch:
    async def test_starts_released(self) -> None:
        state = RuntimeState()
        assert state.trading_allowed is True
        assert state.block_reason is None

    async def test_activation_blocks_trading(self) -> None:
        state = RuntimeState()
        result = await state.activate_kill_switch(reason="spread blowout", actor="telegram:1")

        assert result.active is True
        assert state.trading_allowed is False
        assert state.block_reason is NoTradeReason.KILL_SWITCH_ACTIVE
        assert state.kill_switch.reason == "spread blowout"
        assert state.kill_switch.actor == "telegram:1"
        assert state.kill_switch.changed_at is not None

    async def test_release_restores_trading(self) -> None:
        state = RuntimeState()
        await state.activate_kill_switch(reason="test", actor="a")
        await state.release_kill_switch(actor="b")

        assert state.trading_allowed is True
        assert state.kill_switch.reason is None
        assert state.kill_switch.actor == "b"

    async def test_persist_hook_receives_every_change(self) -> None:
        seen: list[KillSwitchState] = []

        async def persist(value: KillSwitchState) -> None:
            seen.append(value)

        state = RuntimeState(persist=persist)
        await state.activate_kill_switch(reason="r", actor="a")
        await state.release_kill_switch(actor="a")

        assert [s.active for s in seen] == [True, False]

    async def test_loading_stored_state_does_not_repersist(self) -> None:
        """Startup adopts what is already in the database; rewriting it would
        overwrite the original actor and timestamp."""
        calls: list[KillSwitchState] = []

        async def persist(value: KillSwitchState) -> None:
            calls.append(value)

        state = RuntimeState(persist=persist)
        state.load_kill_switch(KillSwitchState(active=True, reason="from db", actor="op"))

        assert state.trading_allowed is False
        assert calls == []


class TestSerialisation:
    def test_round_trip(self) -> None:
        state = RuntimeState()
        original = KillSwitchState(active=True, reason="halt", actor="telegram:9")
        restored = KillSwitchState.from_dict(original.to_dict())

        assert restored.active is True
        assert restored.reason == "halt"
        assert restored.actor == "telegram:9"
        state.load_kill_switch(restored)
        assert state.trading_allowed is False

    async def test_round_trip_keeps_the_timestamp_to_the_millisecond(self) -> None:
        """Serialisation uses the canonical ISO-with-milliseconds form, so
        sub-millisecond precision is intentionally dropped."""
        state = RuntimeState()
        await state.activate_kill_switch(reason="r", actor="a")

        original = state.kill_switch.changed_at
        restored = KillSwitchState.from_dict(state.kill_switch.to_dict()).changed_at

        assert original is not None and restored is not None
        assert abs((restored - original).total_seconds()) < 0.001

    def test_missing_payload_is_a_released_switch(self) -> None:
        assert KillSwitchState.from_dict(None).active is False
        assert KillSwitchState.from_dict({}).active is False


class TestSnapshot:
    async def test_snapshot_reports_uptime_and_switch(self) -> None:
        state = RuntimeState()
        snapshot = state.snapshot()
        assert snapshot["trading_allowed"] is True
        assert snapshot["uptime_seconds"] >= 0
        assert snapshot["kill_switch"]["active"] is False


class TestPhaseGuard:
    def test_future_phase_is_refused(self) -> None:
        with pytest.raises(NotImplementedInPhaseError, match="PHASE 7"):
            require_phase("prediction lock", phase=7, current_phase=1)

    def test_current_and_past_phases_pass(self) -> None:
        require_phase("logging", phase=1, current_phase=1)
        require_phase("config", phase=1, current_phase=3)
