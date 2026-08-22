"""Redis client.

**Redis is a cache, never a store of record.**  Anything ARUNA must not lose -
predictions, council transcripts, outcomes, audit entries - goes to MySQL.
Redis holds the latest health snapshot, hot lookups, and rate-limit counters.

Because of that, every operation degrades quietly: if Redis is down, reads
return ``None`` and writes return ``False`` instead of raising.  The outage is
not hidden - the health monitor reports Redis as DOWN and the first failure is
logged - but a cache outage must not take the system with it.  Use
:meth:`Cache.require` when a caller genuinely cannot proceed without it.
"""

from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from aruna.core.clock import elapsed_ms, monotonic
from aruna.core.config import RedisSettings
from aruna.core.errors import CacheError
from aruna.core.logging import get_logger

log = get_logger("aruna.cache")


class Cache:
    def __init__(self, settings: RedisSettings) -> None:
        self._settings = settings
        self._client: aioredis.Redis | None = None
        self._degraded_logged = False

    # ---- lifecycle -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def settings(self) -> RedisSettings:
        return self._settings

    async def connect(self) -> bool:
        """Connect and verify with a PING.  Returns False if unavailable.

        A failure here is not fatal: ARUNA continues without a cache.
        """
        if not self._settings.enabled:
            log.info("cache.disabled", reason="ARUNA_REDIS_ENABLED=false")
            return False
        if self._client is not None:
            return True

        started = monotonic()
        client = aioredis.from_url(
            self._settings.url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            health_check_interval=30,
            # RESP2. redis-py 6+ defaults to RESP3, whose HELLO handshake does
            # not exist before Redis 6.0 - including the Redis 5.0 that Laragon
            # bundles, where it fails the connection outright. ARUNA uses only
            # GET/SET/DEL/INCR/EXPIRE/PING/INFO, none of which need RESP3.
            protocol=2,
        )
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            await client.aclose()
            log.warning(
                "cache.unavailable",
                target=self._settings.safe_url,
                error=str(exc),
                impact="running without cache; MySQL remains the store of record",
            )
            return False

        self._client = client
        self._degraded_logged = False
        log.info(
            "cache.connected",
            target=self._settings.safe_url,
            namespace=self._settings.namespace,
            connect_ms=elapsed_ms(started),
        )
        return True

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        log.info("cache.closed")

    def require(self) -> aioredis.Redis:
        """The raw client, for callers that cannot degrade.  Raises if absent."""
        if self._client is None:
            raise CacheError(
                "Redis is required for this operation but is not connected "
                f"({self._settings.safe_url})"
            )
        return self._client

    # ---- health --------------------------------------------------------

    async def ping(self) -> float:
        """Round-trip latency in milliseconds.  Raises when Redis is absent."""
        client = self.require()
        started = monotonic()
        try:
            await client.ping()
        except (RedisError, OSError) as exc:
            raise CacheError(f"redis ping failed: {exc}") from exc
        return elapsed_ms(started)

    async def info(self) -> dict[str, Any]:
        client = self.require()
        try:
            raw = await client.info("server")
        except (RedisError, OSError) as exc:
            raise CacheError(f"redis INFO failed: {exc}") from exc
        return {
            "version": raw.get("redis_version"),
            "uptime_seconds": raw.get("uptime_in_seconds"),
            "mode": raw.get("redis_mode"),
        }

    # ---- operations ----------------------------------------------------

    def key(self, *parts: str) -> str:
        return self._settings.key(*parts)

    async def get_json(self, *parts: str) -> Any | None:
        client = self._client
        if client is None:
            return None
        full = self.key(*parts)
        try:
            raw = await client.get(full)
        except (RedisError, OSError) as exc:
            self._note_degraded("get", full, exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # A corrupt cache entry is a cache miss, not a crash.
            log.warning("cache.corrupt_entry", key=full)
            return None

    async def set_json(self, *parts: str, value: Any, ttl_sec: int | None = None) -> bool:
        client = self._client
        if client is None:
            return False
        full = self.key(*parts)
        try:
            await client.set(full, json.dumps(value, default=str), ex=ttl_sec)
        except (RedisError, OSError, TypeError) as exc:
            self._note_degraded("set", full, exc)
            return False
        return True

    async def delete(self, *parts: str) -> bool:
        client = self._client
        if client is None:
            return False
        full = self.key(*parts)
        try:
            return bool(await client.delete(full))
        except (RedisError, OSError) as exc:
            self._note_degraded("delete", full, exc)
            return False

    async def incr(self, *parts: str, ttl_sec: int | None = None) -> int | None:
        """Increment a counter, setting a TTL on first write.  Used for rate limits."""
        client = self._client
        if client is None:
            return None
        full = self.key(*parts)
        try:
            pipe = client.pipeline()
            pipe.incr(full)
            if ttl_sec is not None:
                # NX so a burst does not keep pushing the window forward.
                pipe.expire(full, ttl_sec, nx=True)
            results = await pipe.execute()
        except (RedisError, OSError) as exc:
            self._note_degraded("incr", full, exc)
            return None
        return int(results[0])

    def _note_degraded(self, op: str, key: str, exc: Exception) -> None:
        """Log the first failure loudly, subsequent ones at debug.

        A dead Redis would otherwise emit a warning per operation and drown the
        log in identical lines.
        """
        if self._degraded_logged:
            log.debug("cache.operation_failed", op=op, key=key, error=str(exc))
            return
        self._degraded_logged = True
        log.warning(
            "cache.degraded",
            op=op,
            key=key,
            error=str(exc),
            impact="cache reads return None and writes are dropped until Redis recovers",
        )


__all__ = ["Cache"]
