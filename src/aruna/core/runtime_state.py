"""Runtime flags that gate what ARUNA is allowed to do right now.

The kill switch (SPEC 40 ``/kill`` and ``/resume``) is the important one.  It
is deliberately in-memory *and* durable: an operator who kills the system must
find it still killed after a restart, so the authoritative copy lives in
``app_state`` and this class is the hot cache in front of it.

Nothing here performs I/O.  Persistence is wired in by the application layer,
which lets the state machine be tested without a database.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import NoTradeReason
from aruna.core.errors import NotImplementedInPhaseError

#: Persisted under this key in ``app_state``.
KILL_SWITCH_KEY = "kill_switch"


@dataclass(frozen=True, slots=True)
class KillSwitchState:
    active: bool = False
    reason: str | None = None
    actor: str | None = None
    changed_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "reason": self.reason,
            "actor": self.actor,
            "changed_at": isoformat(self.changed_at) if self.changed_at else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> KillSwitchState:
        if not payload:
            return cls()
        raw_changed = payload.get("changed_at")
        changed_at = (
            datetime.fromisoformat(str(raw_changed).replace("Z", "+00:00"))
            if raw_changed
            else None
        )
        return cls(
            active=bool(payload.get("active", False)),
            reason=payload.get("reason") or None,  # type: ignore[arg-type]
            actor=payload.get("actor") or None,  # type: ignore[arg-type]
            changed_at=changed_at,
        )


#: Called whenever the kill switch flips, so the application can persist it.
PersistHook = Callable[[KillSwitchState], Awaitable[None]]


class RuntimeState:
    """Mutable process state, guarded by a lock so concurrent Telegram commands
    and the health loop cannot interleave a flip."""

    def __init__(self, *, persist: PersistHook | None = None) -> None:
        self._kill = KillSwitchState()
        self._persist = persist
        self._lock = asyncio.Lock()
        self._started_at = now_utc()

    # ---- kill switch ---------------------------------------------------

    @property
    def kill_switch(self) -> KillSwitchState:
        return self._kill

    @property
    def trading_allowed(self) -> bool:
        """False while the kill switch is engaged.

        Every future signal path must consult this before producing a decision;
        when it is False the correct output is WAIT with
        ``NoTradeReason.KILL_SWITCH_ACTIVE``, not a suppressed signal.
        """
        return not self._kill.active

    @property
    def block_reason(self) -> NoTradeReason | None:
        return NoTradeReason.KILL_SWITCH_ACTIVE if self._kill.active else None

    def set_persist_hook(self, persist: PersistHook | None) -> None:
        self._persist = persist

    def load_kill_switch(self, state: KillSwitchState) -> None:
        """Adopt a state read from storage at startup, without re-persisting."""
        self._kill = state

    async def activate_kill_switch(self, *, reason: str, actor: str) -> KillSwitchState:
        return await self._set_kill(active=True, reason=reason, actor=actor)

    async def release_kill_switch(self, *, actor: str) -> KillSwitchState:
        return await self._set_kill(active=False, reason=None, actor=actor)

    async def _set_kill(
        self, *, active: bool, reason: str | None, actor: str
    ) -> KillSwitchState:
        async with self._lock:
            self._kill = replace(
                self._kill,
                active=active,
                reason=reason,
                actor=actor,
                changed_at=now_utc(),
            )
            if self._persist is not None:
                await self._persist(self._kill)
            return self._kill

    # ---- process ------------------------------------------------------

    @property
    def started_at(self) -> datetime:
        return self._started_at

    @property
    def uptime_seconds(self) -> float:
        return (now_utc() - self._started_at).total_seconds()

    def snapshot(self) -> dict[str, object]:
        return {
            "started_at": isoformat(self._started_at),
            "uptime_seconds": round(self.uptime_seconds, 1),
            "trading_allowed": self.trading_allowed,
            "kill_switch": self._kill.to_dict(),
        }


def require_phase(feature: str, phase: int, current_phase: int) -> None:
    """Refuse to run a feature whose phase has not been built.

    SPEC 49 forbids presenting an unbuilt feature as working; this makes the
    refusal explicit instead of returning an empty or fabricated result.
    """
    if phase > current_phase:
        raise NotImplementedInPhaseError(feature, phase)


__all__ = [
    "KILL_SWITCH_KEY",
    "KillSwitchState",
    "PersistHook",
    "RuntimeState",
    "require_phase",
]
