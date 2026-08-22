"""Periodic health sweeps.

Two behaviours worth knowing about:

* **Debounce.** A component that fails is reported DEGRADED until it has failed
  ``failure_threshold`` times in a row, and only then DOWN.  Without this a
  single dropped packet pages the operator.
* **Edge-triggered reporting.** Events and alerts fire on *transitions*, not on
  every sweep.  A component that is down for an hour produces one alert, not
  one every 30 seconds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace

from aruna.core.clock import elapsed_ms, monotonic
from aruna.core.enums import EventSeverity, HealthStatus
from aruna.core.logging import get_logger
from aruna.health.models import ComponentHealth, HealthCheck, HealthReport
from aruna.health.severity import severity_for

log = get_logger("aruna.health")

#: Called with (report, changed components) whenever any component changes state.
AlertHook = Callable[[HealthReport, tuple[ComponentHealth, ...]], Awaitable[None]]

#: Called for every state transition so it can be written to system_events.
EventHook = Callable[[ComponentHealth, HealthStatus, EventSeverity], Awaitable[None]]


class HealthMonitor:
    def __init__(
        self,
        checks: Sequence[HealthCheck],
        *,
        interval_sec: float = 30.0,
        failure_threshold: int = 2,
        alert_hook: AlertHook | None = None,
        event_hook: EventHook | None = None,
    ) -> None:
        self._checks = list(checks)
        self._interval = interval_sec
        self._threshold = max(1, failure_threshold)
        self._alert_hook = alert_hook
        self._event_hook = event_hook

        self._failures: dict[str, int] = {}
        self._last_status: dict[str, HealthStatus] = {}
        self._report: HealthReport | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # ---- accessors -----------------------------------------------------

    @property
    def latest(self) -> HealthReport | None:
        """Most recent sweep, or None before the first one completes."""
        return self._report

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def set_alert_hook(self, hook: AlertHook | None) -> None:
        self._alert_hook = hook

    def set_event_hook(self, hook: EventHook | None) -> None:
        self._event_hook = hook

    def add_check(self, check: HealthCheck) -> None:
        self._checks.append(check)

    # ---- sweeps --------------------------------------------------------

    async def run_once(self) -> HealthReport:
        started = monotonic()
        # Probes are independent; run them together so one slow component does
        # not stretch the sweep past the interval.
        results = await asyncio.gather(
            *(self._safe_check(check) for check in self._checks)
        )
        components = tuple(self._apply_debounce(result) for result in results)
        report = HealthReport(components=components, duration_ms=elapsed_ms(started))
        self._report = report

        changed = self._detect_transitions(components)
        if changed:
            await self._announce(report, changed)
        return report

    async def _safe_check(self, check: HealthCheck) -> ComponentHealth:
        try:
            return await check.check()
        except Exception as exc:
            log.exception("health.check_raised", component=check.name)
            return ComponentHealth(
                name=check.name,
                status=HealthStatus.DOWN,
                message=f"probe raised {type(exc).__name__}: {exc}",
            )

    def _apply_debounce(self, result: ComponentHealth) -> ComponentHealth:
        if result.ok:
            self._failures.pop(result.name, None)
            return result

        count = self._failures.get(result.name, 0) + 1
        self._failures[result.name] = count

        if result.status is HealthStatus.DOWN and count < self._threshold:
            return replace(
                result,
                status=HealthStatus.DEGRADED,
                consecutive_failures=count,
                message=f"{result.message} (failure {count}/{self._threshold})",
            )
        return replace(result, consecutive_failures=count)

    def _detect_transitions(
        self, components: tuple[ComponentHealth, ...]
    ) -> tuple[ComponentHealth, ...]:
        changed: list[ComponentHealth] = []
        for component in components:
            previous = self._last_status.get(component.name)
            if previous != component.status:
                changed.append(component)
                self._last_status[component.name] = component.status
        return tuple(changed)

    async def _announce(
        self, report: HealthReport, changed: tuple[ComponentHealth, ...]
    ) -> None:
        for component in changed:
            severity = severity_for(component.name, component.status)
            log.log(
                _log_level(severity),
                "health.transition",
                component=component.name,
                status=component.status.value,
                message=component.message,
                latency_ms=component.latency_ms,
            )
            if self._event_hook is not None:
                try:
                    await self._event_hook(component, report.status, severity)
                except Exception:
                    log.exception("health.event_hook_failed", component=component.name)

        if self._alert_hook is not None:
            try:
                await self._alert_hook(report, changed)
            except Exception:
                log.exception("health.alert_hook_failed")

    # ---- background loop -----------------------------------------------

    async def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="aruna-health-monitor")
        log.info("health.monitor_started", interval_sec=self._interval)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        log.info("health.monitor_stopped")

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("health.sweep_failed")
            try:
                # Wait on the stop event rather than sleeping, so shutdown does
                # not have to wait out a full interval.
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
            except TimeoutError:
                continue


def _log_level(severity: EventSeverity) -> int:
    import logging

    return {
        EventSeverity.INFO: logging.INFO,
        EventSeverity.WARNING: logging.WARNING,
        EventSeverity.ERROR: logging.ERROR,
        EventSeverity.CRITICAL: logging.CRITICAL,
    }[severity]


__all__ = ["AlertHook", "EventHook", "HealthMonitor"]
