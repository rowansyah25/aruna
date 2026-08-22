"""Health data types.

The aggregation rule is deliberately pessimistic: the overall status is the
worst component status, and an unreachable component is never averaged away by
healthy ones.  SPEC 5 and SPEC 18 both depend on ARUNA knowing, without
ambiguity, when its own inputs are untrustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import HealthStatus


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    checked_at: datetime = field(default_factory=now_utc)
    #: How many checks in a row this component has failed.  Reset on recovery.
    consecutive_failures: int = 0

    @property
    def ok(self) -> bool:
        return self.status.is_operational

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "details": self.details,
            "checked_at": isoformat(self.checked_at),
            "consecutive_failures": self.consecutive_failures,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    components: tuple[ComponentHealth, ...]
    checked_at: datetime = field(default_factory=now_utc)
    duration_ms: float = 0.0

    @property
    def status(self) -> HealthStatus:
        """Worst component status.  DISABLED components do not drag it down."""
        considered = [c.status for c in self.components if c.status is not HealthStatus.DISABLED]
        if not considered:
            return HealthStatus.UNKNOWN
        return max(considered, key=lambda s: s.severity)

    @property
    def healthy(self) -> bool:
        return self.status.is_operational

    def component(self, name: str) -> ComponentHealth | None:
        return next((c for c in self.components if c.name == name), None)

    def failing(self) -> tuple[ComponentHealth, ...]:
        return tuple(c for c in self.components if not c.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "checked_at": isoformat(self.checked_at),
            "duration_ms": self.duration_ms,
            "components": [c.to_dict() for c in self.components],
        }


@runtime_checkable
class HealthCheck(Protocol):
    """One probe.  Implementations must not raise - they report DOWN instead,
    so that one broken probe cannot stop the monitor from checking the rest."""

    name: str

    async def check(self) -> ComponentHealth: ...


__all__ = ["ComponentHealth", "HealthCheck", "HealthReport"]
