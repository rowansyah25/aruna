"""Telegram chat registry.

Recording every chat that talks to the bot - authorized or not - is what makes
``/start`` from an unknown chat reviewable later.  The ``authorized`` column
mirrors the env allowlist; the env file remains the authority, this table is
the record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aruna.db.pool import Database
from aruna.db.types import as_utc

_SUBSCRIBER_COLUMNS = (
    "chat_id, chat_type, username, display_name, authorized, "
    "first_seen_at, last_seen_at, command_count"
)


@dataclass(frozen=True, slots=True)
class TelegramSubscriber:
    chat_id: str
    chat_type: str | None
    username: str | None
    display_name: str | None
    authorized: bool
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    command_count: int


class TelegramSubscriberRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def touch(
        self,
        *,
        chat_id: str,
        authorized: bool,
        chat_type: str | None = None,
        username: str | None = None,
        display_name: str | None = None,
    ) -> None:
        """Record a chat interaction, creating the row on first contact."""
        await self._db.execute(
            """
            INSERT INTO telegram_subscribers
                (chat_id, chat_type, username, display_name, authorized, command_count)
            VALUES (%s, %s, %s, %s, %s, 1) AS new
            ON DUPLICATE KEY UPDATE
                chat_type     = COALESCE(new.chat_type, telegram_subscribers.chat_type),
                username      = COALESCE(new.username, telegram_subscribers.username),
                display_name  = COALESCE(new.display_name,
                                         telegram_subscribers.display_name),
                authorized    = new.authorized,
                last_seen_at  = CURRENT_TIMESTAMP(6),
                command_count = telegram_subscribers.command_count + 1
            """,
            chat_id,
            chat_type,
            username,
            display_name,
            authorized,
        )

    async def get(self, chat_id: str) -> TelegramSubscriber | None:
        row = await self._db.fetchrow(
            f"SELECT {_SUBSCRIBER_COLUMNS} FROM telegram_subscribers WHERE chat_id = %s",
            chat_id,
        )
        return _to_subscriber(row) if row else None

    async def all(self) -> list[TelegramSubscriber]:
        rows = await self._db.fetch(
            f"SELECT {_SUBSCRIBER_COLUMNS} FROM telegram_subscribers "
            "ORDER BY last_seen_at DESC"
        )
        return [_to_subscriber(row) for row in rows]


def _to_subscriber(row: dict[str, Any]) -> TelegramSubscriber:
    return TelegramSubscriber(
        chat_id=row["chat_id"],
        chat_type=row["chat_type"],
        username=row["username"],
        display_name=row["display_name"],
        authorized=bool(row["authorized"]),
        first_seen_at=as_utc(row["first_seen_at"]),
        last_seen_at=as_utc(row["last_seen_at"]),
        command_count=int(row["command_count"]),
    )


__all__ = ["TelegramSubscriber", "TelegramSubscriberRepository"]
