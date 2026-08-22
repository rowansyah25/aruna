"""Operational timeline: system events and the audit log.

The two are separate on purpose.  ``system_events`` is what the machine
observed (a component went DOWN, a provider disconnected); ``audit_logs`` is
what somebody *did* (an operator engaged the kill switch, a command was
refused).  Mixing them makes the second impossible to review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from aruna.core.enums import EventSeverity, HealthStatus
from aruna.db.pool import Database
from aruna.db.types import as_utc, dump_json, load_json_object, to_mysql_datetime

AuditResult = Literal["SUCCESS", "FAILURE", "DENIED"]


@dataclass(frozen=True, slots=True)
class SystemEvent:
    id: int
    occurred_at: datetime
    component: str
    event_type: str
    severity: EventSeverity
    status: HealthStatus | None
    message: str
    details: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: int
    occurred_at: datetime
    actor: str
    action: str
    entity_type: str | None
    entity_id: str | None
    result: AuditResult
    detail: str | None


class SystemEventRepository:
    def __init__(self, db: Database, *, instance: str, phase: int) -> None:
        self._db = db
        self._instance = instance
        self._phase = phase

    async def record(
        self,
        *,
        component: str,
        event_type: str,
        severity: EventSeverity,
        message: str,
        status: HealthStatus | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        return await self._db.insert(
            """
            INSERT INTO system_events
                (instance, phase, component, event_type, severity, status, message, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            self._instance,
            self._phase,
            component,
            event_type,
            severity.value,
            status.value if status else None,
            message,
            dump_json(details or {}),
        )

    async def recent(
        self, *, limit: int = 20, component: str | None = None
    ) -> list[SystemEvent]:
        base = (
            "SELECT id, occurred_at, component, event_type, severity, status, "
            "message, details FROM system_events"
        )
        if component:
            rows = await self._db.fetch(
                f"{base} WHERE component = %s ORDER BY occurred_at DESC LIMIT %s",
                component,
                limit,
            )
        else:
            rows = await self._db.fetch(
                f"{base} ORDER BY occurred_at DESC LIMIT %s", limit
            )
        return [
            SystemEvent(
                id=row["id"],
                occurred_at=as_utc(row["occurred_at"]),
                component=row["component"],
                event_type=row["event_type"],
                severity=EventSeverity(row["severity"]),
                status=HealthStatus(row["status"]) if row["status"] else None,
                message=row["message"],
                details=load_json_object(row["details"]),
            )
            for row in rows
        ]

    async def count_since(
        self, since: datetime, *, severity: EventSeverity | None = None
    ) -> int:
        cutoff = to_mysql_datetime(since)
        if severity:
            return await self._db.fetchval(
                "SELECT count(*) FROM system_events "
                "WHERE occurred_at >= %s AND severity = %s",
                cutoff,
                severity.value,
            )
        return await self._db.fetchval(
            "SELECT count(*) FROM system_events WHERE occurred_at >= %s", cutoff
        )


class AuditRepository:
    """Append-only.  The table's triggers reject UPDATE and DELETE, so there is
    deliberately no method to change an entry."""

    def __init__(self, db: Database, *, instance: str) -> None:
        self._db = db
        self._instance = instance

    async def record(
        self,
        *,
        actor: str,
        action: str,
        result: AuditResult = "SUCCESS",
        entity_type: str | None = None,
        entity_id: str | None = None,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> int:
        return await self._db.insert(
            """
            INSERT INTO audit_logs
                (instance, actor, action, entity_type, entity_id,
                 before_state, after_state, result, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            self._instance,
            actor,
            action,
            entity_type,
            entity_id,
            dump_json(before_state),
            dump_json(after_state),
            result,
            detail,
        )

    async def recent(self, *, limit: int = 20, actor: str | None = None) -> list[AuditEntry]:
        base = (
            "SELECT id, occurred_at, actor, action, entity_type, entity_id, "
            "result, detail FROM audit_logs"
        )
        if actor:
            rows = await self._db.fetch(
                f"{base} WHERE actor = %s ORDER BY occurred_at DESC LIMIT %s",
                actor,
                limit,
            )
        else:
            rows = await self._db.fetch(
                f"{base} ORDER BY occurred_at DESC LIMIT %s", limit
            )
        return [
            AuditEntry(
                id=row["id"],
                occurred_at=as_utc(row["occurred_at"]),
                actor=row["actor"],
                action=row["action"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                result=row["result"],
                detail=row["detail"],
            )
            for row in rows
        ]


__all__ = [
    "AuditEntry",
    "AuditRepository",
    "AuditResult",
    "SystemEvent",
    "SystemEventRepository",
]
