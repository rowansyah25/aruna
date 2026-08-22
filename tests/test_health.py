"""Health checks, aggregation, and monitor debounce."""

from __future__ import annotations

import asyncio

import pytest
from tests.conftest import make_settings

from aruna.core.enums import EventSeverity, HealthStatus
from aruna.core.runtime_state import RuntimeState
from aruna.health.checks import ClockCheck, ConfigCheck, ProcessCheck
from aruna.health.models import ComponentHealth, HealthReport
from aruna.health.monitor import HealthMonitor


class StubCheck:
    """A probe whose result the test controls."""

    def __init__(self, name: str, status: HealthStatus = HealthStatus.UP) -> None:
        self.name = name
        self.status = status
        self.calls = 0

    async def check(self) -> ComponentHealth:
        self.calls += 1
        return ComponentHealth(name=self.name, status=self.status, message="stub")


class RaisingCheck:
    name = "explosive"

    async def check(self) -> ComponentHealth:
        raise RuntimeError("probe is broken")


class TestAggregation:
    def test_worst_component_wins(self) -> None:
        report = HealthReport(
            components=(
                ComponentHealth(name="a", status=HealthStatus.UP),
                ComponentHealth(name="b", status=HealthStatus.DOWN),
                ComponentHealth(name="c", status=HealthStatus.DEGRADED),
            )
        )
        assert report.status is HealthStatus.DOWN
        assert report.healthy is False

    def test_disabled_components_do_not_drag_the_total_down(self) -> None:
        report = HealthReport(
            components=(
                ComponentHealth(name="a", status=HealthStatus.UP),
                ComponentHealth(name="telegram", status=HealthStatus.DISABLED),
            )
        )
        assert report.status is HealthStatus.UP
        assert report.healthy is True

    def test_all_disabled_is_unknown(self) -> None:
        report = HealthReport(
            components=(ComponentHealth(name="a", status=HealthStatus.DISABLED),)
        )
        assert report.status is HealthStatus.UNKNOWN

    def test_failing_lists_only_broken_components(self) -> None:
        report = HealthReport(
            components=(
                ComponentHealth(name="a", status=HealthStatus.UP),
                ComponentHealth(name="b", status=HealthStatus.DOWN),
            )
        )
        assert [c.name for c in report.failing()] == ["b"]

    def test_lookup_by_name(self) -> None:
        report = HealthReport(components=(ComponentHealth(name="db", status=HealthStatus.UP),))
        assert report.component("db") is not None
        assert report.component("missing") is None


class TestMonitorDebounce:
    async def test_first_failure_is_degraded_not_down(self) -> None:
        """One dropped packet must not page the operator."""
        check = StubCheck("flaky", HealthStatus.DOWN)
        monitor = HealthMonitor([check], failure_threshold=3)

        report = await monitor.run_once()
        assert report.component("flaky").status is HealthStatus.DEGRADED
        assert report.component("flaky").consecutive_failures == 1

    async def test_reaching_the_threshold_reports_down(self) -> None:
        check = StubCheck("flaky", HealthStatus.DOWN)
        monitor = HealthMonitor([check], failure_threshold=3)

        await monitor.run_once()
        await monitor.run_once()
        report = await monitor.run_once()

        assert report.component("flaky").status is HealthStatus.DOWN
        assert report.component("flaky").consecutive_failures == 3

    async def test_recovery_resets_the_counter(self) -> None:
        check = StubCheck("flaky", HealthStatus.DOWN)
        monitor = HealthMonitor([check], failure_threshold=2)
        await monitor.run_once()

        check.status = HealthStatus.UP
        report = await monitor.run_once()
        assert report.component("flaky").status is HealthStatus.UP
        assert report.component("flaky").consecutive_failures == 0

        check.status = HealthStatus.DOWN
        report = await monitor.run_once()
        assert report.component("flaky").status is HealthStatus.DEGRADED

    async def test_threshold_of_one_reports_down_immediately(self) -> None:
        monitor = HealthMonitor([StubCheck("x", HealthStatus.DOWN)], failure_threshold=1)
        report = await monitor.run_once()
        assert report.component("x").status is HealthStatus.DOWN


class TestMonitorTransitions:
    async def test_events_fire_only_on_change(self) -> None:
        check = StubCheck("db")
        events: list[tuple[str, HealthStatus]] = []

        async def event_hook(
            component: ComponentHealth, _overall: HealthStatus, _sev: EventSeverity
        ) -> None:
            events.append((component.name, component.status))

        monitor = HealthMonitor([check], event_hook=event_hook, failure_threshold=1)
        await monitor.run_once()
        await monitor.run_once()
        await monitor.run_once()
        assert events == [("db", HealthStatus.UP)]

        check.status = HealthStatus.DOWN
        await monitor.run_once()
        await monitor.run_once()
        assert events == [("db", HealthStatus.UP), ("db", HealthStatus.DOWN)]

    async def test_alert_hook_receives_only_the_changed_components(self) -> None:
        good = StubCheck("cache")
        bad = StubCheck("db")
        seen: list[tuple[str, ...]] = []

        async def alert_hook(
            _report: HealthReport, changed: tuple[ComponentHealth, ...]
        ) -> None:
            seen.append(tuple(c.name for c in changed))

        monitor = HealthMonitor([good, bad], alert_hook=alert_hook, failure_threshold=1)
        await monitor.run_once()
        bad.status = HealthStatus.DOWN
        await monitor.run_once()

        assert seen[-1] == ("db",)

    async def test_severity_escalates_with_status(self) -> None:
        check = StubCheck("db", HealthStatus.DOWN)
        severities: list[EventSeverity] = []

        async def event_hook(
            _c: ComponentHealth, _o: HealthStatus, severity: EventSeverity
        ) -> None:
            severities.append(severity)

        monitor = HealthMonitor([check], event_hook=event_hook, failure_threshold=2)
        await monitor.run_once()  # debounced -> DEGRADED -> WARNING
        await monitor.run_once()  # DOWN -> CRITICAL
        assert severities == [EventSeverity.WARNING, EventSeverity.CRITICAL]

    async def test_a_hook_that_raises_does_not_break_the_sweep(self) -> None:
        async def bad_hook(*_args: object) -> None:
            raise RuntimeError("hook exploded")

        monitor = HealthMonitor(
            [StubCheck("db")], alert_hook=bad_hook, event_hook=bad_hook, failure_threshold=1
        )
        report = await monitor.run_once()
        assert report.status is HealthStatus.UP


class TestMonitorResilience:
    async def test_a_raising_probe_becomes_a_down_component(self) -> None:
        monitor = HealthMonitor(
            [RaisingCheck(), StubCheck("db")], failure_threshold=1
        )
        report = await monitor.run_once()

        assert report.component("explosive").status is HealthStatus.DOWN
        assert "probe is broken" in report.component("explosive").message
        # The healthy component was still checked.
        assert report.component("db").status is HealthStatus.UP

    async def test_background_loop_starts_and_stops(self) -> None:
        check = StubCheck("db")
        monitor = HealthMonitor([check], interval_sec=0.05)
        await monitor.start()
        assert monitor.running is True
        await asyncio.sleep(0.16)
        await monitor.stop()

        assert monitor.running is False
        assert check.calls >= 2

    async def test_latest_is_none_before_the_first_sweep(self) -> None:
        assert HealthMonitor([StubCheck("db")]).latest is None


class TestRealChecks:
    async def test_clock_check_resolves_jakarta(self) -> None:
        result = await ClockCheck("Asia/Jakarta").check()
        assert result.status is HealthStatus.UP
        assert "idx_session" in result.details
        assert "crypto_session" in result.details

    async def test_clock_check_reports_a_missing_timezone(self) -> None:
        result = await ClockCheck("Mars/Olympus_Mons").check()
        assert result.status is HealthStatus.DOWN
        assert "tzdata" in result.message

    async def test_config_check_is_up_when_only_phase_gaps_remain(self) -> None:
        """PHASE 1 has no data providers by design.  Reporting that as degraded
        would leave health permanently yellow and train operators to ignore it."""
        result = await ConfigCheck(make_settings()).check()

        assert result.status is HealthStatus.UP
        assert result.details["warnings"] == []
        assert result.details["phase_notices"]
        assert "known gap" in result.message

    async def test_config_check_degrades_on_real_misconfiguration(self) -> None:
        from pydantic import SecretStr

        from aruna.core.config import TelegramSettings

        settings = make_settings(
            telegram=TelegramSettings(
                _env_file=None,
                bot_token=SecretStr("123456789:AAFakeTokenForTestsOnly_0123456789abc"),
                chat_id="",
            )
        )
        result = await ConfigCheck(settings).check()

        assert result.status is HealthStatus.DEGRADED
        assert any("authorized chat ids" in w for w in result.details["warnings"])

    async def test_process_check_is_up_when_running_normally(self) -> None:
        state = RuntimeState()
        result = await ProcessCheck(state, instance="test", phase=1).check()
        assert result.status is HealthStatus.UP

    async def test_process_check_degrades_when_killed(self) -> None:
        """An engaged kill switch is a deliberate state, not an outage - but it
        must be visible."""
        state = RuntimeState()
        await state.activate_kill_switch(reason="manual", actor="op")
        result = await ProcessCheck(state, instance="test", phase=1).check()

        assert result.status is HealthStatus.DEGRADED
        assert "kill switch ACTIVE" in result.message


@pytest.mark.parametrize("status", list(HealthStatus))
def test_component_serialisation_covers_every_status(status: HealthStatus) -> None:
    payload = ComponentHealth(name="x", status=status).to_dict()
    assert payload["status"] == status.value
    assert "checked_at" in payload
