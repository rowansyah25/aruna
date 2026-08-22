"""Durable runtime flags.

Small key/value store backing :class:`aruna.core.runtime_state.RuntimeState`.
Its job is to make an operator's ``/kill`` survive a process restart.

The columns are ``state_key``/``state_value`` because ``KEY`` is reserved in
MySQL, and a schema that needs backquotes everywhere eventually meets the one
query that forgot them.
"""

from __future__ import annotations

from typing import Any

from aruna.db.pool import Database
from aruna.db.types import dump_json, load_json


class AppStateRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(
            "SELECT state_value FROM app_state WHERE state_key = %s", key
        )
        return load_json(row["state_value"]) if row else None

    async def set(self, key: str, value: dict[str, Any], *, actor: str) -> None:
        await self._db.execute(
            """
            INSERT INTO app_state (state_key, state_value, updated_by)
            VALUES (%s, %s, %s) AS new
            ON DUPLICATE KEY UPDATE
                state_value = new.state_value,
                updated_by  = new.updated_by
            """,
            key,
            dump_json(value),
            actor,
        )

    async def delete(self, key: str) -> bool:
        return await self._db.execute(
            "DELETE FROM app_state WHERE state_key = %s", key
        ) == 1

    async def all(self) -> dict[str, dict[str, Any]]:
        rows = await self._db.fetch(
            "SELECT state_key, state_value FROM app_state ORDER BY state_key"
        )
        return {row["state_key"]: load_json(row["state_value"]) for row in rows}


__all__ = ["AppStateRepository"]
