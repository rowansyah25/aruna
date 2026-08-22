"""Shared HTTP client for provider adapters.

Centralises the things every adapter needs to get right: a bounded timeout,
retries that only apply to failures worth retrying, latency measurement for the
SPEC 4 provenance record, and turning transport errors into
:class:`DataSourceUnavailableError` so callers never see an httpx exception.

Two entry points, and the difference matters.  :meth:`HttpFetcher.get_json` and
:meth:`HttpFetcher.get_text` are the ordinary ones: a failed request is an
error, full stop.  :meth:`HttpFetcher.get_response` hands back the raw response
instead, for the adapter that has to distinguish one 4xx from another or read a
rate-limit header - information that no longer exists once a status has been
turned into an error message.

**The retry policy is a default, not a law.**  ``retry_statuses`` and
``max_retries`` can be narrowed per call because the right answer depends on
the venue, and the wrong answer is expensive in a way no unit test on this
module would show.  Retrying a 429 spends the very budget the venue is
complaining about; retrying a 503 on a host that has two live alternates spends
attempts on the host that just said no.  Both were measured on Binance before
these two parameters existed: a single ``fetch_candles`` sent four requests into
an active rate limit, and twelve into a venue-wide 5xx.

``follow_redirects`` is per adapter for the same reason.  Yahoo's chart endpoint
really does redirect and has to be followed; Binance's REST API never does, and
a 302 there means something between us and the venue is answering.  An adapter
that checks a path allowlist checks the URL it *builds*, so a followed redirect
lands on a path the allowlist never saw.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from aruna.core.clock import elapsed_ms, monotonic
from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger

log = get_logger("aruna.data.http")

#: Statuses worth retrying by default: transient server trouble and rate
#: limiting.  Public because a caller narrowing it needs to say what it is
#: narrowing *from* - ``RETRY_STATUSES - {429}`` states the exception at the
#: call site, where a reviewer sees it, instead of copying the set and letting
#: the two drift apart.
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class HttpFetcher:
    """A per-provider HTTP client that reports how long each call took."""

    def __init__(
        self,
        *,
        source: str,
        base_url: str = "",
        timeout_sec: float = 15.0,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._source = source
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._max_retries = max_retries
        self._headers = {"User-Agent": "ARUNA/0.1 (market research)", **(headers or {})}
        self._follow_redirects = follow_redirects
        # Injected only by tests, and deliberately so: a test that builds its
        # own AsyncClient is testing its own client, not this one.  Handing the
        # transport in keeps the retry loop, the redirect policy and the headers
        # under test as the code that ships.
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def open(self) -> None:
        if self._client is not None:
            return
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers=self._headers,
            follow_redirects=self._follow_redirects,
            transport=self._transport,
        )

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    @property
    def is_open(self) -> bool:
        return self._client is not None

    async def get_json(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> tuple[Any, float]:
        """Fetch and decode JSON.  Returns ``(payload, latency_ms)``."""
        raw, latency = await self.get_text(path, params=params)
        try:
            return httpx.Response(200, text=raw).json(), latency
        except ValueError as exc:
            raise DataSourceUnavailableError(
                f"{self._source} returned non-JSON for {path}: {raw[:120]!r}"
            ) from exc

    async def get_text(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> tuple[str, float]:
        response, latency = await self.get_response(path, params=params)
        if response.status_code >= 400:
            raise DataSourceUnavailableError(
                f"{self._source} returned HTTP {response.status_code} for "
                f"{path}: {response.text[:120]!r}"
            )
        return response.text, latency

    async def get_response(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        retry_statuses: frozenset[int] | None = None,
        max_retries: int | None = None,
    ) -> tuple[httpx.Response, float]:
        """Fetch, retrying only what is worth retrying.  ``(response, latency_ms)``.

        The response comes back **whatever its status**, 4xx included.
        :meth:`get_text` flattens those into ``DataSourceUnavailableError``,
        which is all most adapters want; an adapter that has to tell one 4xx
        from another or read a response header cannot, once the status has
        become an error string.  Binance answers 418 for an IP ban, 429 for a
        rate limit and 451 for a blocked jurisdiction - three different
        reactions - and reports its consumed request weight in a header that
        only exists on the response object.

        ``retry_statuses`` narrows :data:`RETRY_STATUSES` for one call.  An
        adapter that owns a rate-limit policy passes it without 429 so that the
        *first* 429 reaches it intact: retried here, the venue is hit three more
        times while it is already refusing, the adapter's cooldown starts only
        after the last of them, and a 429 turns into the 418 that bans the IP.

        ``max_retries`` overrides the configured budget for one call, for the
        caller with somewhere better to spend an attempt.  A transport that
        rotates to another host on a 5xx does not want three retries against the
        host that just answered 503 - that is the same failure measured three
        times, multiplied by the number of hosts.

        Exhausting the retries still raises, because "no response at all" is
        not a status code and there would be nothing to hand back.
        """
        if self._client is None:
            await self.open()
        assert self._client is not None

        statuses = RETRY_STATUSES if retry_statuses is None else retry_statuses
        retries = self._max_retries if max_retries is None else max(0, max_retries)
        started = monotonic()
        last_error = "unknown"

        for attempt in range(retries + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= retries:
                    break
                await self._backoff(attempt)
                continue

            if response.status_code in statuses and attempt < retries:
                last_error = f"HTTP {response.status_code}"
                log.warning(
                    "provider.retrying",
                    source=self._source,
                    path=path,
                    status=response.status_code,
                    attempt=attempt + 1,
                )
                await self._backoff(attempt, response)
                continue

            return response, elapsed_ms(started)

        raise DataSourceUnavailableError(
            f"{self._source} unreachable at {path} after "
            f"{retries + 1} attempt(s): {last_error}"
        )

    async def _backoff(self, attempt: int, response: httpx.Response | None = None) -> None:
        """Exponential backoff, honouring Retry-After when the server sets it."""
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    await asyncio.sleep(min(float(header), 30.0))
                    return
                except ValueError:
                    pass
        # Jitter so several symbols retrying together do not synchronise.
        delay = min(2.0**attempt, 8.0) * (0.5 + random.random() / 2)
        await asyncio.sleep(delay)


__all__ = ["RETRY_STATUSES", "HttpFetcher"]
