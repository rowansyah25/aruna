"""Conversions between MySQL's wire types and ARUNA's domain types.

Two things MySQL does not do for us, and both are correctness issues rather
than conveniences:

* **Timezones.** A ``DATETIME`` column has none. ARUNA writes UTC everywhere
  and pins each session to ``+00:00``, so a value read back *is* UTC - but the
  driver hands it over naive, and a naive timestamp is how look-ahead bugs
  (SPEC 24) become invisible. :func:`as_utc` re-attaches the tzinfo at the
  boundary, so nothing above the repository layer ever sees a naive datetime.

* **JSON.** The driver returns a ``JSON`` column as text. :func:`load_json` and
  :func:`dump_json` are the single place that is decoded and encoded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def as_utc(value: datetime | None) -> datetime | None:
    """Tag a naive MySQL ``DATETIME`` as UTC.

    Already-aware values are converted rather than relabelled, so passing one
    through twice is safe.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_mysql_datetime(value: datetime | None) -> datetime | None:
    """Render an aware datetime for storage: converted to UTC, then stripped.

    The column has no timezone, so the tzinfo must come off - but only after
    converting, never by discarding an offset.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(
            "naive datetime rejected: convert to UTC before storing so that "
            "ordering across providers is unambiguous"
        )
    return value.astimezone(UTC).replace(tzinfo=None)


def load_json(value: Any) -> Any:
    """Decode a ``JSON`` column.  Returns ``None`` for SQL NULL."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    return json.loads(value)


def dump_json(value: Any) -> str | None:
    """Encode a value for a ``JSON`` column.  ``None`` stays SQL NULL."""
    if value is None:
        return None
    return json.dumps(value, default=str)


def load_json_object(value: Any) -> dict[str, Any]:
    """Decode a ``JSON`` column that the schema guarantees is a non-null object."""
    decoded = load_json(value)
    return decoded if isinstance(decoded, dict) else {}


__all__ = [
    "as_utc",
    "dump_json",
    "load_json",
    "load_json_object",
    "to_mysql_datetime",
]
