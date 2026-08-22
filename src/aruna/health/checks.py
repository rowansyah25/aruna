"""Individual health probes.

Every probe catches its own exceptions and reports DOWN.  A probe that raises
would abort the whole sweep and leave the remaining components unknown, which
is worse than a single reported failure.
"""

from __future__ import annotations

import asyncio
import platform
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aruna.cache.redis_client import Cache
from aruna.core.clock import (
    IDX_CALENDAR,
    crypto_session,
    elapsed_ms,
    isoformat,
    monotonic,
    now_utc,
)
from aruna.core.config import CURRENT_PHASE, Settings
from aruna.core.enums import HealthStatus
from aruna.core.errors import CacheError, DatabaseError
from aruna.core.runtime_state import RuntimeState
from aruna.db.pool import Database
from aruna.health.models import ComponentHealth


class DatabaseCheck:
    """MySQL reachability, latency, pool pressure, schema version, and the
    session timezone.

    The timezone is checked on every sweep rather than trusted from startup:
    ARUNA stores UTC in ``DATETIME`` columns that carry no offset, so a
    connection that came up without the ``+00:00`` pin would write timestamps
    that are wrong in a way nothing else would notice.
    """

    name = "database"

    def __init__(self, db: Database, *, timeout: float = 5.0) -> None:
        self._db = db
        self._timeout = timeout
        self._schema_version: str | None = None

    async def check(self) -> ComponentHealth:
        if not self._db.is_connected:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message="connection pool is not open",
                details={"target": self._db.settings.safe_dsn},
            )
        try:
            latency = await self._db.ping(self._timeout)
        except DatabaseError as exc:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=str(exc),
                details={"target": self._db.settings.safe_dsn},
            )

        stats = self._db.pool_stats()
        details: dict[str, Any] = {
            "target": self._db.settings.safe_dsn,
            "pool": stats,
        }

        # Read once; the schema does not change under a running process.
        if self._schema_version is None:
            try:
                self._schema_version = await self._db.fetchval(
                    "SELECT max(version) FROM schema_migrations"
                )
            except DatabaseError:
                self._schema_version = None
        details["schema_version"] = self._schema_version

        try:
            timezone = await self._db.session_timezone()
        except DatabaseError as exc:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                message=f"cannot read session timezone: {exc}",
                latency_ms=latency,
                details=details,
            )
        details["session_timezone"] = timezone

        status = HealthStatus.UP
        message = f"ok in {latency:.1f} ms"

        if timezone != "+00:00":
            # Not a latency problem - every timestamp written on this
            # connection would be in the wrong zone with nothing to reveal it.
            status = HealthStatus.DOWN
            message = (
                f"session timezone is {timezone!r}, expected '+00:00'; "
                "timestamps written now would not be UTC"
            )
        # No idle connections while at max size means the next query queues.
        elif stats["max"] and stats["idle"] == 0 and stats["size"] >= stats["max"]:
            status = HealthStatus.DEGRADED
            message = "connection pool exhausted"
        elif latency > 1_000:
            status = HealthStatus.DEGRADED
            message = f"slow response: {latency:.0f} ms"

        return ComponentHealth(
            name=self.name,
            status=status,
            message=message,
            latency_ms=latency,
            details=details,
        )


class RedisCheck:
    name = "redis"

    def __init__(self, cache: Cache, *, timeout: float = 3.0) -> None:
        self._cache = cache
        self._timeout = timeout

    async def check(self) -> ComponentHealth:
        if not self._cache.enabled:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DISABLED,
                message="disabled via ARUNA_REDIS_ENABLED=false",
            )
        if not self._cache.available:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message="not connected",
                details={"target": self._cache.settings.safe_url},
            )
        try:
            async with asyncio.timeout(self._timeout):
                latency = await self._cache.ping()
                info = await self._cache.info()
        except (CacheError, TimeoutError) as exc:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=f"ping failed: {exc}",
                details={"target": self._cache.settings.safe_url},
            )

        status = HealthStatus.DEGRADED if latency > 500 else HealthStatus.UP
        return ComponentHealth(
            name=self.name,
            status=status,
            message=f"ok in {latency:.1f} ms",
            latency_ms=latency,
            details={"target": self._cache.settings.safe_url, **info},
        )


class TelegramCheck:
    """Calls ``getMe``.

    Reports DISABLED, not DOWN, when no token is set - running headless is a
    configuration choice, not a fault.

    **A bot that was configured and failed to start is DOWN, not DISABLED.**
    Both states arrive here as "not running", and collapsing them said "no bot
    token configured - ARUNA is running headless" to an operator who had
    configured a token and whose bot was broken. DISABLED is excluded from the
    overall verdict, so the system also reported itself healthy while its only
    interface was dead. ``configured`` is what separates the two.

    **``hidup`` is a callable, and that is the whole point.** It used to be a
    plain ``bool``, read once when the monitor was assembled. ARUNA retries a
    bot that failed to start - `ArunaApplication._retry_telegram`, added
    2026-08-19 for exactly the Indonesian-ISP blockage that keeps recurring -
    and the retry works. The health check never learned. Measured 2026-08-22::

        07:19:47  health  DOWN  "the bot did not start"
        07:19:49  telegram.reconnected - bot menyala sesudah gagal saat startup
        07:33     health  still DOWN, fourteen minutes later

    An operator reading that believes signals are not being delivered while
    they are. Two halves that do not talk: one was fixed, the other was not
    told.

    Taking a callable makes the old shape **unwritable**. A ``bool`` cannot be
    passed any more, so nobody can freeze this state again by accident - which
    is the only kind of fix worth making for a bug whose entire nature was
    "somebody read a moving value once".
    """

    name = "telegram"

    def __init__(
        self,
        *,
        hidup: Callable[[], bool],
        probe: Callable[[], Awaitable[dict[str, Any]]] | None,
        timeout: float = 10.0,
        configured: bool = False,
    ) -> None:
        self._hidup = hidup
        self._probe = probe
        self._timeout = timeout
        self._configured = configured

    async def check(self) -> ComponentHealth:
        # Dibaca SEKARANG, tiap sapuan. Lihat catatan `hidup` di docstring.
        if not self._hidup() or self._probe is None:
            if self._configured:
                return ComponentHealth(
                    name=self.name,
                    status=HealthStatus.DOWN,
                    message=(
                        "a bot token is configured but the bot did not start. "
                        "This is a fault, not a headless deployment - see the "
                        "telegram START_FAILED event for the reason"
                    ),
                )
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DISABLED,
                message="no bot token configured - ARUNA is running headless",
            )
        started = monotonic()
        try:
            async with asyncio.timeout(self._timeout):
                info = await self._probe()
        except TimeoutError:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=f"getMe timed out after {self._timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 - a probe must never propagate
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=f"getMe failed: {exc}",
            )
        return ComponentHealth(
            name=self.name,
            status=HealthStatus.UP,
            message=f"connected as @{info.get('username', 'unknown')}",
            latency_ms=elapsed_ms(started),
            details=info,
        )


class ConfigCheck:
    """Surfaces configuration gaps as a component so they show up in /status
    rather than only in a startup log line nobody scrolls back to."""

    name = "config"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self) -> ComponentHealth:
        warnings = self._settings.startup_warnings()
        notices = self._settings.phase_notices()

        # Only actionable misconfiguration degrades health.  Expected gaps for
        # this build stage are reported, not counted - see
        # Settings.phase_notices for why the distinction matters.
        status = HealthStatus.DEGRADED if warnings else HealthStatus.UP
        if warnings:
            message = f"{len(warnings)} configuration warning(s)"
        elif notices:
            message = f"configured for PHASE {CURRENT_PHASE}; {len(notices)} known gap(s)"
        else:
            message = "fully configured"

        return ComponentHealth(
            name=self.name,
            status=status,
            message=message,
            details={
                "warnings": warnings,
                "phase_notices": notices,
                "markets": [m.value for m in self._settings.app.enabled_markets],
                "providers": self._settings.providers.configured,
            },
        )


class ClockCheck:
    """Timezone data and session labels.

    A missing IANA database on Windows is a real failure mode - without
    ``tzdata`` the IDX session clock cannot be computed at all, and every IDX
    timestamp would silently fall back to something wrong.
    """

    name = "clock"

    def __init__(self, timezone: str = "Asia/Jakarta") -> None:
        self._timezone = timezone

    async def check(self) -> ComponentHealth:
        try:
            zone = ZoneInfo(self._timezone)
        except ZoneInfoNotFoundError as exc:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"timezone {self._timezone!r} unavailable ({exc}); "
                    "install the tzdata package"
                ),
            )

        now = now_utc()
        local: datetime = now.astimezone(zone)
        return ComponentHealth(
            name=self.name,
            status=HealthStatus.UP,
            message=f"{self._timezone} resolved",
            details={
                "utc": isoformat(now),
                "local": local.isoformat(timespec="seconds"),
                "idx_session": IDX_CALENDAR.session(now).value,
                "idx_open": IDX_CALENDAR.is_open(now),
                "idx_holiday_calendar_loaded": IDX_CALENDAR.has_holiday_calendar,
                "crypto_session": crypto_session(now).value,
            },
        )


class ProcessCheck:
    """Uptime and the kill switch.

    An engaged kill switch is DEGRADED, not DOWN: the process is healthy, it is
    just refusing to trade, and an operator needs to see that state clearly
    rather than have it look like an outage.
    """

    name = "process"

    def __init__(self, state: RuntimeState, *, instance: str, phase: int) -> None:
        self._state = state
        self._instance = instance
        self._phase = phase

    async def check(self) -> ComponentHealth:
        kill = self._state.kill_switch
        status = HealthStatus.DEGRADED if kill.active else HealthStatus.UP
        message = (
            f"kill switch ACTIVE ({kill.reason or 'no reason given'})"
            if kill.active
            else f"running for {self._state.uptime_seconds / 3600:.1f} h"
        )
        return ComponentHealth(
            name=self.name,
            status=status,
            message=message,
            details={
                "instance": self._instance,
                "phase": self._phase,
                "python": platform.python_version(),
                "platform": f"{platform.system()} {platform.release()}",
                **self._state.snapshot(),
            },
        )


__all__ = [
    "ClockCheck",
    "ConfigCheck",
    "DatabaseCheck",
    "ProcessCheck",
    "RedisCheck",
    "TelegramCheck",
]
