"""Binance spot market data (PASAL 5, 6, 8, 10, 41; SPEC 4, 5, 24, 47).

PASAL 5 makes Binance the crypto source and PASAL 6 restricts ARUNA to
USDT-denominated pairs.  This module is the REST half of that: initial
snapshot, historical candles, order book, venue metadata, recovery.

**Built so a websocket can sit on top of it (PASAL 8).**  Transport is a
separate object - :class:`BinanceSpotRest` - and ``transport`` is instance
state rather than a constant welded into :attr:`BinanceSpotProvider.capabilities`.
A streaming provider later sets ``self._transport = Transport.STREAM`` and
keeps using the same REST object for backfill and reconciliation.  Had POLL
been hardcoded in the capabilities property, adding STREAM would have meant
reopening capabilities, the registry and the health surface together.

**Read-only by construction (PASAL 41).**  The guarantee is the absence of
credentials, and it is worth stating in that order.  Nothing here signs a
request, holds a key, or sends the header Binance requires for an authenticated
call, so every private endpoint answers this adapter with a refusal no matter
what path is asked for.  :data:`PUBLIC_ENDPOINTS` sits in front of that as a second, weaker check:
:meth:`BinanceSpotRest.get` refuses any path outside it, but it can only see the
URL this module *builds*.  A redirect would carry the client somewhere the
allowlist never saw - measured, not imagined: a 302 to ``/api/v3/order`` really
was followed and its body really was read back, which is why the Binance
transport now sets ``follow_redirects=False`` and treats a redirect as a host
that is not the API.  A test asserts this module contains no execution path at
all, because a promise in a docstring is not a guarantee.

**Reachability was measured, not assumed.**  On 2026-08-17 from this machine
``api.binance.com``, ``api1.binance.com`` and ``api2.binance.com`` each
answered ``/api/v3/ping`` with HTTP 200.  An older note in this project said
Binance was unreachable from Indonesian networks because its domains resolve to
the Kominfo TrustPositif block page; that was one observation on one network
and it is not a property of the host.  This adapter calls the venue and reports
what came back - a block arrives as UNREACHABLE carrying the transport error,
never as a guess about the cause (SPEC 49).

What does not depend on the network: Binance is not registered with Bappebti.
That is a legal fact, it travels with every row in ``regulatory_note``, and
SPEC 47 leaves the consequence to the operator.

**Rate limit is real.**  The spot REST API allows 6000 request weight per
minute per IP.  ``X-MBX-USED-WEIGHT-1M`` is read off every response so
consumption is visible before the wall, and a 429 or 418 puts the adapter into
a cooldown during which it will not call the venue at all.  Retrying through a
rate limit is how a 429 becomes a 418, and how a two-minute ban becomes a
three-day one - so this transport opts 429 out of
:data:`~aruna.data.http.RETRY_STATUSES` on every call it makes.  It did not,
once: the shared fetcher retried the 429 three times before the cooldown branch
below ever ran, so one ``fetch_candles`` sent four requests into an active rate
limit and slept ninety seconds doing it.  The docstring said otherwise for as
long as that was true, which is the failure this paragraph now describes rather
than repeats.

**The last candle is still forming and Binance does not say so.**  ``is_closed``
is decided against the *venue's* clock - :meth:`BinanceSpotRest.venue_now`,
measured from ``/api/v3/time`` - never against this machine's.  Reverse that and
a bar that is still moving becomes settled evidence, so outcome resolution
scores a prediction against a close that has not happened yet - look-ahead that
looks entirely plausible in the data (SPEC 24).  Measured on 2026-08-17: this
machine ran 12.2 seconds behind the venue, and a machine 30 seconds *ahead*
would have stored a bar as closed whose close price then moved and whose volume
grew by a third.  When the offset has never been measured, nothing is marked
closed: that delays evidence, which is recoverable, instead of inventing it,
which is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from aruna.core.clock import crypto_session, elapsed_ms, monotonic, now_utc
from aruna.core.config import DataSettings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError, DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.crypto.symbols import to_venue_symbol
from aruna.data.http import RETRY_STATUSES, HttpFetcher
from aruna.data.models import (
    Candle,
    OrderBook,
    OrderBookLevel,
    Provenance,
    Quote,
    Snapshot,
    quantize_bps,
    quantize_pct,
)
from aruna.data.provider import (
    MarketDataProvider,
    ProviderCapabilities,
    ProviderStatus,
    Transport,
)

log = get_logger("aruna.data.crypto.binance")

#: Provenance value stored in ``candles.source``, ``market_snapshots.source``
#: and ``provider_events.provider``.  Distinct from the futures adapter's
#: ``binance-futures`` on purpose: spot and perpetual are two feeds with
#: different prices, and one shared label would make them indistinguishable
#: after the fact.  This is data - once a row carries it, it does not change.
SOURCE = "binance-spot"

#: Primary host first, then the documented alternates.  All three answered on
#: 2026-08-17; they exist because Binance rotates capacity between them, not
#: because one is a workaround for another being blocked.
HOSTS: tuple[str, ...] = (
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
)

#: Every path this adapter may request.  Market data only - see the module
#: docstring.  Adding an execution path here would be a deliberate act, visible
#: in review, and the test suite refuses it.
#:
#: **Exactly the paths that have a caller in this module, and a test enforces
#: the equality.**  ``/api/v3/ticker/bookTicker`` and ``/api/v3/ping`` used to
#: sit here with no caller anywhere in ``src/``: bookTicker because the ticker
#: it would duplicate is already fetched from ``/api/v3/ticker/24hr`` in one
#: request (see :meth:`BinanceSpotProvider._ticker`), ping because
#: :meth:`BinanceSpotProvider.status` needs the clock that ``/api/v3/time``
#: returns and ping does not.  An entry nobody calls is an entry nobody reviews
#: again, and this list is the only structural narrowing PASAL 41 has here.  A
#: websocket stage that needs bookTicker for reconciliation adds it back
#: together with the code that calls it.
PUBLIC_ENDPOINTS: frozenset[str] = frozenset(
    {
        "/api/v3/time",
        "/api/v3/exchangeInfo",
        "/api/v3/klines",
        "/api/v3/ticker/24hr",
        "/api/v3/depth",
    }
)

#: ARUNA horizon -> Binance ``interval``.  Only what the venue genuinely
#: serves.  Two absences are deliberate:
#:
#: * ``10m`` does not exist on Binance spot at all, and nothing derives it here
#:   either: :mod:`aruna.data.resample` has no caller anywhere in ``src/``, so a
#:   request for a 10m candle is refused rather than filled.  This comment used
#:   to say the horizon "is resampled from 1m like everywhere else", which was
#:   read by the operator surface and was not true anywhere (SPEC 4).
#: * ``1M`` exists at the venue but is a *calendar* month, while
#:   ``Horizon.MO1`` is a flat 30 days.  Mapping them would put a close_time on
#:   every monthly bar that the venue never agreed to.
#:
#: ``3m`` is present and native, which it was not on the previous source.
INTERVALS: dict[Horizon, str] = {
    Horizon.M1: "1m",
    Horizon.M3: "3m",
    Horizon.M5: "5m",
    Horizon.M15: "15m",
    Horizon.M30: "30m",
    Horizon.H1: "1h",
    Horizon.H4: "4h",
    Horizon.D1: "1d",
    Horizon.D3: "3d",
    Horizon.W1: "1w",
}

#: Documented spot REST ceiling: request weight per minute, per IP.
WEIGHT_LIMIT_1M = 6_000
#: Share of the ceiling at which consumption starts being logged.  A warning
#: that only fires at the wall is a warning that arrives too late to act on.
WEIGHT_WARN_RATIO = 0.8

#: ``/api/v3/klines`` never returns more than this, whatever ``limit`` says.
MAX_KLINES_PER_REQUEST = 1_000
#: Pages one :meth:`BinanceSpotProvider.fetch_candles` call may walk.  A caller
#: asking for more than 1000 bars gets them across several requests rather than
#: silently receiving 1000 - the shortfall would look like a venue with no
#: history.  The cap stops a bad ``limit`` from becoming an unbounded crawl.
MAX_KLINE_PAGES = 10

#: Depth sizes ``/api/v3/depth`` accepts.  A request for 20 levels is legal; a
#: request for 25 is a 400, so an arbitrary depth is rounded *up* to the next
#: legal size and trimmed locally.
DEPTH_LIMITS: tuple[int, ...] = (5, 10, 20, 50, 100, 500, 1_000, 5_000)

#: Cooldowns used when the venue rate-limits without stating ``Retry-After``.
#: 120s for a 418 is Binance's documented *minimum* ban, not a guess; bans run
#: from two minutes to three days, so this is the shortest it can be.
DEFAULT_RATE_LIMIT_COOLDOWN_SEC = 60.0
DEFAULT_BAN_COOLDOWN_SEC = 120.0

#: Statuses the shared fetcher may retry *for this venue*.  429 is removed on
#: purpose: it is the venue saying the per-IP weight budget is already spent,
#: and three more requests spend more of it.  The cooldown in :meth:`_request`
#: is the reaction, and it cannot react to a 429 the layer below already
#: swallowed and retried.
_BINANCE_RETRY_STATUSES = RETRY_STATUSES - {429}

#: How long a measured venue-clock offset is trusted before it is taken again.
#: ``/api/v3/time`` costs 1 weight out of 6000 per minute, so refreshing is
#: nearly free; what matters is that the number stays smaller than the skew this
#: project treats as tolerable (``future_tolerance_sec`` = 30s).  A machine
#: clock does not drift 30 seconds in five minutes on its own - it gets stepped,
#: which is exactly the event this must not keep inheriting.
CLOCK_MAX_AGE_SEC = 300.0


class ForbiddenEndpoint(ArunaError):
    """An attempt to call anything but public market data (PASAL 41)."""


class BinanceRateLimited(DataSourceUnavailableError):
    """The venue rate-limited us (429) or banned this IP (418).

    A subclass of ``DataSourceUnavailableError`` so every existing handler
    keeps working, and its own type so a caller that wants to back off harder
    can tell it apart from a venue that is merely down.
    """


class BinanceBlocked(DataSourceUnavailableError):
    """The request was refused for who or where we are, not for what we asked.

    HTTP 451 (jurisdiction) and 403 (edge/WAF).  Separated from a plain outage
    because the operator's next action is completely different, and reporting
    "venue down" for a jurisdiction block would name a cause nobody measured
    (SPEC 49).
    """


class _HostFault(ArunaError):
    """Internal: this host failed in a way another host might not."""


class _NotTheApi(_HostFault):
    """Internal: this host answered, but what answered was not the API.

    A 200 carrying HTML, or a redirect away from the path that was asked for.
    Both are the shape of something sitting between ARUNA and the venue - a
    block page, a captive portal, a hijacked DNS answer - and both used to be
    terminal, which meant the two configured alternates were never tried in
    precisely the scenario :class:`BinanceBlocked` was written for.  It is a
    host fault because one hijacked resolver does not hijack all three hosts;
    when every host answers this way, :meth:`BinanceSpotRest.get` reports it as
    :class:`BinanceBlocked` with the body as evidence rather than as a generic
    outage, because "blocked" and "down" need different actions from an
    operator (SPEC 49).
    """


@dataclass(frozen=True, slots=True)
class ClockReading:
    """One measurement of the venue's clock against this machine's.

    ``offset_sec`` is taken *after* the response arrived, so it carries up to
    one round trip of measurement error and reads the venue as slightly earlier
    than it really is.  That bias is kept on purpose: everything downstream uses
    it to decide whether a bar has closed, and reading the venue early can only
    delay that verdict.  Reading it late would settle a bar that is still
    moving (SPEC 24).

    ``measured_mono`` is what the reading is actually carried forward on, and
    ``measured_at`` is kept only so an operator can see when it was taken.
    The first version aged and extrapolated the reading with ``now_utc()`` -
    the very clock the offset exists to distrust - so a single NTP step walked
    straight into ``venue_now`` and stayed there for the whole cache window.
    Measured on the stack that ships: venue truth 12:00:30, machine stepped
    +30s, and a 1m bar with 30 seconds still to run came back ``is_closed``.
    A monotonic anchor cannot be stepped, so the reading now decays only with
    real elapsed time, which is the one thing it is allowed to assume.
    """

    server_time: datetime
    offset_sec: float
    measured_at: datetime
    #: ``monotonic()`` at the moment of measurement. Immune to wall-clock steps.
    measured_mono: float
    latency_ms: float


@dataclass(frozen=True, slots=True)
class SymbolMetadata:
    """Venue-declared trading rules for one pair, from ``/api/v3/exchangeInfo``.

    ``assets.tick_size`` and ``assets.price_precision`` have been NULL since the
    schema was written because no provider could fill them.  Binance states
    both.  They are read from the venue or left absent - inferring a precision
    from whatever price happened to be on screen would be fabricated data
    (SPEC 4), and it would be wrong for exactly the assets where it matters:
    a coin trading at 0.4523 looks like four decimals until it is not.
    """

    symbol: str
    venue_symbol: str
    base_asset: str
    quote_asset: str
    status: str
    tick_size: Decimal | None
    step_size: Decimal | None
    min_notional: Decimal | None
    provenance: Provenance

    @property
    def price_precision(self) -> int | None:
        """Decimal places implied by ``tick_size``.  ``0.01`` -> 2."""
        if self.tick_size is None:
            return None
        exponent = self.tick_size.normalize().as_tuple().exponent
        if not isinstance(exponent, int):
            return None
        return max(0, -exponent)


class BinanceSpotRest:
    """The REST transport, kept apart from the data mapping (PASAL 8).

    Everything venue-shaped and nothing ARUNA-shaped lives here: the endpoint
    allowlist, host failover, rate-limit accounting, and the mapping from
    Binance's status codes onto reasons an operator can act on.  A websocket
    adapter added later owns its own transport and reuses this one for
    snapshots, backfill and reconciliation without touching either.
    """

    def __init__(
        self,
        *,
        hosts: tuple[str, ...] = HOSTS,
        timeout_sec: float = 15.0,
        max_retries: int = 3,
        fetcher: HttpFetcher | None = None,
    ) -> None:
        if not hosts:
            raise ValueError("BinanceSpotRest butuh minimal satu host")
        self._hosts = tuple(hosts)
        self._host_index = 0
        # No base_url: the fetcher is handed a full URL per call so one client
        # and one connection pool can serve all three hosts.
        self._http = fetcher or HttpFetcher(
            source=SOURCE,
            timeout_sec=timeout_sec,
            max_retries=max_retries,
            # Binance REST does not redirect.  Following one would take the
            # request to a path :data:`PUBLIC_ENDPOINTS` never checked, since
            # the allowlist only ever sees the URL built here (PASAL 41).
            follow_redirects=False,
        )
        self._max_retries = max_retries
        self._cooldown_until: datetime | None = None
        self._clock: ClockReading | None = None
        self.used_weight_1m: int | None = None
        self.weight_seen_at: datetime | None = None

    @property
    def hosts(self) -> tuple[str, ...]:
        """Every host this transport may rotate through, in order."""
        return self._hosts

    @property
    def max_retries(self) -> int:
        """Retry budget handed to the fetcher on the last host in the rotation."""
        return self._max_retries

    @property
    def host(self) -> str:
        """Host currently in use.  Sticky: whichever answered last is kept."""
        return self._hosts[self._host_index]

    @property
    def clock(self) -> ClockReading | None:
        """Last venue-clock measurement, or ``None`` if there has never been one."""
        return self._clock

    @property
    def cooldown_until(self) -> datetime | None:
        return self._cooldown_until

    async def open(self) -> None:
        await self._http.open()

    async def close(self) -> None:
        await self._http.close()

    async def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, float]:
        """One public endpoint.  Returns ``(payload, latency_ms)``.

        Host rotation is for host faults only - a refused connection, a
        timeout, a 5xx, and an answer that is not the API at all.  A 451, a 418
        or a malformed request is answered the same way by every host, and
        asking all three would triple the weight spent learning the same thing.

        **The retry budget belongs to the rotation, not to the host.**  Every
        host but the last gets a single attempt; the last one keeps the
        configured retries, because after it there is nowhere else to go.
        Handing each host the full budget made one call cost 12 requests and
        15.4 seconds during a venue-wide 5xx - three times the same answer,
        while a 5-second poll interval went by.
        """
        if path not in PUBLIC_ENDPOINTS:
            raise ForbiddenEndpoint(
                f"{path} bukan endpoint market data. Adapter ini read-only: "
                "tidak bisa membuat, mengubah atau membatalkan order, dan tidak "
                "menyentuh akun atau saldo (PASAL 41)."
            )
        self._check_cooldown()

        started = monotonic()
        attempted: list[str] = []
        not_the_api: list[str] = []
        last = len(self._hosts) - 1
        for offset in range(len(self._hosts)):
            index = (self._host_index + offset) % len(self._hosts)
            host = self._hosts[index]
            try:
                payload, latency = await self._request(
                    host, path, params, max_retries=None if offset == last else 0
                )
            except _HostFault as exc:
                attempted.append(f"{host} -> {exc}")
                if isinstance(exc, _NotTheApi):
                    not_the_api.append(f"{host} -> {exc}")
                log.warning(
                    "binance.host_fault",
                    source=SOURCE,
                    host=host,
                    path=path,
                    error=str(exc)[:200],
                )
                continue
            # Sticky on success: a host that answers keeps being asked, so one
            # bad host is paid for once rather than on every single call.
            self._host_index = index
            return payload, latency

        # The cost of the whole rotation, stated once where an operator reading
        # a failure can see what it spent.  Without it the only trace of a
        # 15-second call is the wall clock between two log lines.
        elapsed = elapsed_ms(started)
        log.warning(
            "binance.all_hosts_failed",
            source=SOURCE,
            path=path,
            hosts=len(self._hosts),
            elapsed_ms=round(elapsed, 1),
        )
        if not_the_api and len(not_the_api) == len(attempted):
            # Every host answered with something that is not this API.  Naming
            # that as a block is a measurement, not a guess - and it is the one
            # cause whose remedy has nothing to do with waiting.
            raise BinanceBlocked(
                f"binance-spot: semua host membalas sesuatu yang bukan API-nya "
                f"untuk {path}. Bentuk seperti ini (halaman HTML atau redirect) "
                f"adalah tanda pemblokiran di jalur jaringan, bukan venue mati. "
                + "; ".join(not_the_api)
            )
        raise DataSourceUnavailableError(
            f"binance-spot tidak bisa dihubungi di {path} setelah "
            f"{elapsed / 1000:.1f}s; semua host dicoba: " + "; ".join(attempted)
        )

    # ---- internals ------------------------------------------------------

    def _check_cooldown(self) -> None:
        if self._cooldown_until is None:
            return
        remaining = (self._cooldown_until - now_utc()).total_seconds()
        if remaining <= 0:
            self._cooldown_until = None
            return
        raise BinanceRateLimited(
            f"binance-spot sedang menahan diri {remaining:.0f}s lagi setelah "
            "kena rate limit. Request tidak dikirim - mencoba terus justru "
            "memperpanjang ban (PASAL 5, rate limit venue)."
        )

    def _start_cooldown(self, seconds: float, reason: str) -> None:
        self._cooldown_until = now_utc() + timedelta(seconds=seconds)
        log.warning(
            "binance.cooldown_started",
            source=SOURCE,
            seconds=round(seconds, 1),
            reason=reason,
            until=self._cooldown_until.isoformat(),
        )

    async def _request(
        self,
        host: str,
        path: str,
        params: dict[str, Any] | None,
        *,
        max_retries: int | None = None,
    ) -> tuple[Any, float]:
        try:
            response, latency = await self._http.get_response(
                f"{host}{path}",
                params=params,
                # 429 handled here, never below: see _BINANCE_RETRY_STATUSES.
                retry_statuses=_BINANCE_RETRY_STATUSES,
                max_retries=max_retries,
            )
        except DataSourceUnavailableError as exc:
            # The fetcher already retried what was worth retrying; whatever is
            # left is this host failing to answer at all.
            raise _HostFault(str(exc)) from exc

        self._note_weight(response, path)
        status = response.status_code

        if status == 418:
            wait = _retry_after_sec(response) or DEFAULT_BAN_COOLDOWN_SEC
            self._start_cooldown(wait, "HTTP 418 IP ban")
            raise BinanceRateLimited(
                f"binance-spot membalas HTTP 418: IP ini sedang di-ban karena "
                f"melampaui rate limit. Tidak dicoba ulang; tunggu {wait:.0f}s "
                "sesuai jawaban venue."
            )
        if status == 429:
            wait = _retry_after_sec(response) or DEFAULT_RATE_LIMIT_COOLDOWN_SEC
            self._start_cooldown(wait, "HTTP 429 rate limit")
            raise BinanceRateLimited(
                f"binance-spot membalas HTTP 429: rate limit terlampaui "
                f"(used weight 1m: {self.used_weight_1m}/{WEIGHT_LIMIT_1M}). "
                f"Berhenti dulu {wait:.0f}s."
            )
        if status == 451:
            raise BinanceBlocked(
                "binance-spot membalas HTTP 451: venue menolak permintaan dari "
                "yurisdiksi ini. Ini penolakan hukum dari sisi venue, bukan "
                "gangguan jaringan, dan ARUNA tidak mengakalinya (SPEC 47)."
            )
        if status == 403:
            raise BinanceBlocked(
                "binance-spot membalas HTTP 403: permintaan ditolak di lapisan "
                f"edge/WAF, bukan oleh API-nya. Isi jawaban: "
                f"{response.text[:120]!r}"
            )
        if status == 404:
            # Every path this adapter can send is on the allowlist and spelled
            # correctly, so a 404 is never "you asked for something that does
            # not exist" - it is this host not serving the API.  Observed on
            # 2026-08-17: a Binance subdomain that does not exist still
            # resolves and answers with an nginx 404 page, so a mistyped or
            # retired host would have been reported as a terminal failure and
            # the alternates never tried.
            raise _HostFault(f"HTTP 404 - host tidak melayani {path}")
        if status >= 500:
            raise _HostFault(f"HTTP {status}")
        if status >= 400:
            raise DataSourceUnavailableError(
                f"binance-spot membalas HTTP {status} untuk {path}: "
                f"{_venue_error(response)}"
            )
        if 300 <= status < 400:
            # Not followed - the fetcher for this transport has redirects off -
            # and not a normal answer either.  Binance REST replies in place, so
            # a redirect is something else answering for it.
            location = response.headers.get("Location", "")
            raise _NotTheApi(
                f"HTTP {status} redirect ke {location[:120]!r}; endpoint REST "
                "Binance tidak pernah meredirect"
            )

        try:
            return response.json(), latency
        except ValueError as exc:
            # HTTP 200 whose body is not JSON: the shape of a block page or a
            # portal, not of this API.  Another host may not be intercepted, so
            # this rotates instead of ending the call.
            raise _NotTheApi(
                f"HTTP {status} tapi body bukan JSON: {response.text[:120]!r}"
            ) from exc

    # ---- venue clock ----------------------------------------------------

    async def measure_clock(self) -> ClockReading | None:
        """Read ``/api/v3/time`` and record the offset.  Raises like any call.

        ``None`` means the venue answered without a readable ``serverTime`` -
        distinct from "the call failed", which raises, and from "never
        measured", which is :attr:`clock` staying ``None``.
        """
        payload, latency = await self.get("/api/v3/time")
        server_time = _ms_to_utc(payload.get("serverTime")) if isinstance(payload, dict) else None
        if server_time is None:
            return None
        measured_at = now_utc()
        self._clock = ClockReading(
            server_time=server_time,
            offset_sec=(server_time - measured_at).total_seconds(),
            measured_at=measured_at,
            measured_mono=monotonic(),
            latency_ms=latency,
        )
        return self._clock

    async def venue_now(self) -> datetime | None:
        """The venue's clock now, or ``None`` if it cannot be established.

        Refreshes itself when the last reading is older than
        :data:`CLOCK_MAX_AGE_SEC`, which costs one weight out of 6000 per
        minute.  A failure is swallowed and reported as ``None`` rather than
        raised: the caller is deciding whether a bar has closed, and the honest
        answer when the venue's clock is unknown is "not yet" - never this
        machine's clock, which is the guess this whole method exists to remove.

        Both the ageing and the extrapolation run on ``monotonic()``. Running
        either on ``now_utc()`` put the wall clock back in the answer: an
        offset only corrects the skew present when it was taken, so a step
        after that is inherited whole, and the age check could not notice
        because it was reading the stepped clock too. The measured
        counterexample is in :class:`ClockReading`.
        """
        reading = self._clock
        if (
            reading is not None
            and (monotonic() - reading.measured_mono) <= CLOCK_MAX_AGE_SEC
        ):
            return reading.server_time + timedelta(
                seconds=monotonic() - reading.measured_mono
            )

        try:
            reading = await self.measure_clock()
        except ArunaError as exc:
            log.warning("binance.clock_unmeasured", source=SOURCE, error=str(exc)[:200])
            return None
        if reading is None:
            log.warning(
                "binance.clock_unmeasured",
                source=SOURCE,
                error="/api/v3/time menjawab tanpa serverTime",
            )
            return None
        return reading.server_time + timedelta(
            seconds=monotonic() - reading.measured_mono
        )

    def _note_weight(self, response: httpx.Response, path: str) -> None:
        """Record consumed request weight so the ceiling is observable.

        Without this the first sign of trouble is a 429, by which point the
        budget has already been spent.  The header is per IP and resets each
        minute; it is recorded, never used to fabricate a delay.
        """
        raw = response.headers.get("x-mbx-used-weight-1m") or response.headers.get(
            "x-mbx-used-weight"
        )
        if raw is None:
            return
        try:
            used = int(raw)
        except ValueError:
            return

        self.used_weight_1m = used
        self.weight_seen_at = now_utc()
        if used >= WEIGHT_LIMIT_1M * WEIGHT_WARN_RATIO:
            log.warning(
                "binance.weight_high",
                source=SOURCE,
                path=path,
                used_weight_1m=used,
                limit=WEIGHT_LIMIT_1M,
            )


class BinanceSpotProvider(MarketDataProvider):
    """Binance spot, the USDT side of it (PASAL 5, 6).

    Holds no credentials and needs none: every endpoint used here is public.
    """

    def __init__(
        self,
        settings: DataSettings,
        *,
        hosts: tuple[str, ...] = HOSTS,
        rest: BinanceSpotRest | None = None,
    ) -> None:
        self._settings = settings
        self._rest = rest or BinanceSpotRest(
            hosts=hosts,
            timeout_sec=settings.request_timeout_sec,
            max_retries=settings.max_retries,
        )
        # Instance state, not a constant in `capabilities`: PASAL 8 puts a
        # websocket above this adapter later, and that subclass has to be able
        # to say STREAM without capabilities being rewritten underneath it.
        self._transport = Transport.POLL

    @property
    def rest(self) -> BinanceSpotRest:
        """The transport, so a health surface can read consumed weight."""
        return self._rest

    @property
    def capabilities(self) -> ProviderCapabilities:
        hosts = len(self._rest.hosts)
        worst_case_requests = (hosts - 1) + (self._rest.max_retries + 1)
        limitations = [
            "read-only: tidak ada endpoint order, akun, atau transfer yang "
            "bisa dicapai dari adapter ini (PASAL 41)",
            # No claim of a derived 10m: aruna.data.resample has no caller in
            # src/, and this line is printed by `aruna providers` (SPEC 4).
            "interval 10m tidak ada di Binance spot dan tidak diturunkan dari "
            "1m; permintaan candle 10m ditolak, bukan dikarang",
            "klines maksimal 1000 bar per request; permintaan yang lebih besar "
            f"dipecah jadi beberapa request, maksimal {MAX_KLINE_PAGES} halaman "
            f"({MAX_KLINE_PAGES * MAX_KLINES_PER_REQUEST} bar) per panggilan",
            "rate limit 6000 request weight per menit per IP; adapter membaca "
            "header X-MBX-USED-WEIGHT-1M dan berhenti sendiri saat kena "
            "429/418, dan 429 tidak pernah di-retry",
            f"saat venue balas 5xx: tiap host dicoba sekali dan retry hanya di "
            f"host terakhir, jadi satu panggilan bisa mengirim sampai "
            f"{worst_case_requests} request sebelum menyerah",
            "bar paling akhir bisa masih terbentuk; ditandai is_closed=false, "
            "bukan disembunyikan. is_closed dinilai dengan jam venue "
            "(/api/v3/time); selama jam itu belum terukur, tidak ada bar yang "
            "ditandai closed",
        ]
        if self._transport is Transport.POLL:
            limitations.insert(
                1,
                "polling REST; WebSocket Binance belum dipasang di tahap ini, "
                "jadi jeda antar observasi = poll interval",
            )
        return ProviderCapabilities(
            name=SOURCE,
            market=Market.CRYPTO,
            transport=self._transport,
            # The data itself carries no venue-declared delay; the gap between
            # observations is the poll interval, which is a transport property
            # and is stated as such above.
            is_realtime=True,
            expected_delay_sec=0,
            supports_order_book=True,
            supported_intervals=tuple(INTERVALS),
            max_candles_per_request=MAX_KLINES_PER_REQUEST,
            requires_credentials=False,
            regulatory_note=(
                "Binance tidak terdaftar di Bappebti. Yang dipakai hanya "
                "endpoint market data publik, tanpa credentials, tanpa jalur "
                "eksekusi. Kesesuaian dengan yurisdiksi deployment adalah "
                "keputusan operator (SPEC 47, PASAL 41)."
            ),
            limitations=tuple(limitations),
        )

    # ---- lifecycle ------------------------------------------------------

    async def open(self) -> None:
        await self._rest.open()

    async def close(self) -> None:
        await self._rest.close()

    async def status(self) -> ProviderStatus:
        """Reachability plus the one number nobody should inherit: clock skew.

        ``/api/v3/time`` is used rather than ``/api/v3/ping`` because ping
        returns ``{}`` - same reachability answer, no clock.  The staleness and
        future-tolerance thresholds in :class:`~aruna.core.config.DataSettings`
        exist to absorb skew, and a threshold sized against a *different*
        venue's clock is a number with no measurement behind it (SPEC 49).
        This makes the real figure readable from ``aruna providers``.

        The measurement is not only printed: it is the same reading
        :meth:`BinanceSpotRest.venue_now` hands to ``is_closed``.  It used to be
        formatted into this string and thrown away, while bars were settled
        against the machine clock this call exists to distrust.
        """
        try:
            reading = await self._rest.measure_clock()
        except ArunaError as exc:
            return ProviderStatus(reachable=False, detail=str(exc)[:300])

        if reading is None:
            return ProviderStatus(
                reachable=False,
                detail="/api/v3/time tidak mengembalikan serverTime yang bisa dibaca",
            )

        # Includes up to one round trip of measurement error, which at these
        # latencies is tens of milliseconds - stated, not hidden.
        detail = (
            f"{self._rest.host}; skew jam venue {reading.offset_sec:+.3f}s "
            "(termasuk 1 round trip)"
        )
        if self._rest.used_weight_1m is not None:
            detail += f"; used weight 1m {self._rest.used_weight_1m}/{WEIGHT_LIMIT_1M}"
        return ProviderStatus(
            reachable=True,
            latency_ms=reading.latency_ms,
            detail=detail,
            server_time=reading.server_time,
        )

    # ---- symbols --------------------------------------------------------

    def provider_symbol(self, symbol: str) -> str:
        """``BTC/USDT`` -> ``BTCUSDT``.

        The mapping itself lives in :mod:`aruna.data.crypto.symbols`; what
        happens here is the error type.  A pair this venue cannot serve -
        ``BTC/IDR`` above all - has to arrive as ``DataSourceUnavailableError``
        rather than ``ValueError``, because :meth:`poll_once` catches the
        former per asset and lets the latter escape.  One IDR row left in the
        universe would otherwise end the whole poll instead of costing one
        symbol.
        """
        try:
            return to_venue_symbol(symbol)
        except ValueError as exc:
            raise DataSourceUnavailableError(f"binance-spot: {exc}") from exc

    # ---- data -----------------------------------------------------------

    async def _ticker(self, symbol: str) -> tuple[dict[str, Any], float]:
        """``/api/v3/ticker/24hr`` for one pair: last, bid/ask, 24h stats.

        One request rather than a ticker plus a bookTicker: two calls cost
        twice the weight and, worse, describe two different instants.
        """
        pair = self.provider_symbol(symbol)
        payload, latency = await self._rest.get("/api/v3/ticker/24hr", {"symbol": pair})
        if not isinstance(payload, dict) or "lastPrice" not in payload:
            raise DataSourceUnavailableError(
                f"binance-spot: ticker {symbol} ({pair}) tidak berisi harga; "
                f"jawaban: {payload!r:.120}"
            )
        return payload, latency

    async def fetch_quote(self, symbol: str) -> Quote:
        payload, latency = await self._ticker(symbol)
        return Quote(
            market=Market.CRYPTO,
            symbol=symbol,
            price=_decimal(payload.get("lastPrice"), f"{symbol} last price"),
            bid=_optional_decimal(payload.get("bidPrice")),
            ask=_optional_decimal(payload.get("askPrice")),
            bid_quantity=_optional_decimal(payload.get("bidQty")),
            ask_quantity=_optional_decimal(payload.get("askQty")),
            provenance=Provenance(
                source=SOURCE,
                server_timestamp=now_utc(),
                # closeTime is the end of the rolling 24h window, i.e. the
                # venue's own clock at the moment it answered.
                provider_timestamp=_ms_to_utc(payload.get("closeTime")),
                latency_ms=latency,
            ),
        )

    async def fetch_candles(
        self,
        symbol: str,
        interval: Horizon,
        *,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        code = INTERVALS.get(interval)
        if code is None:
            raise ValueError(
                f"binance spot tidak menyediakan candle {interval.value}; "
                f"tersedia: {', '.join(h.value for h in INTERVALS)}"
            )

        pair = self.provider_symbol(symbol)
        wanted = max(1, int(limit))
        rows, rejected = await self._klines(
            pair, symbol, code, interval, wanted, start, end
        )

        received = now_utc()
        # The venue's clock, not this machine's, decides which of these bars has
        # closed - and it is fetched on the live path, here, rather than being
        # left to whoever happens to call status() (SPEC 24).
        venue_now = await self._rest.venue_now()
        candles: list[Candle] = []
        for open_ms in sorted(rows):
            row, latency = rows[open_ms]
            candle = self._to_candle(row, symbol, interval, received, venue_now, latency)
            if candle is None:
                rejected += 1
                continue
            if start is not None and candle.open_time < start:
                continue
            if end is not None and candle.open_time > end:
                continue
            candles.append(candle)

        if rejected and not candles:
            # Rows arrived and every one of them was unusable.  Reporting that
            # as "no data" would read as a quiet venue instead of a broken
            # payload, and the two need different reactions (SPEC 4).
            raise DataSourceUnavailableError(
                f"binance-spot: {rejected} baris kline {symbol} {interval.value} "
                "tidak ada yang lolos pemeriksaan bentuk; tidak ada candle yang "
                "bisa dipakai"
            )
        return candles[-limit:] if limit and len(candles) > limit else candles

    async def _klines(
        self,
        pair: str,
        symbol: str,
        code: str,
        interval: Horizon,
        wanted: int,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[dict[int, tuple[Any, float]], int]:
        """Raw kline rows keyed by open time, plus a count of unusable ones.

        Binance caps a response at 1000 bars whatever ``limit`` asks for.
        Returning those 1000 and saying nothing is the failure mode this repo
        already fixed once on the IDX side: a caller asking for a wider history
        gets less than it asked for, and the shortfall is indistinguishable
        from a venue that has no more history.  So the window is walked.

        Direction matters.  With a ``start`` the venue pages forward from it;
        without one, the caller wants the *newest* bars, so paging runs
        backwards from ``end`` (or now).  Getting that backwards would hand a
        request for the last 50 bars the 50 oldest bars Binance still holds.

        Keying by open time deduplicates across pages: the boundary bar
        otherwise arrives twice and would be stored twice (PASAL 10, duplicate
        check).

        A row whose open time cannot be read is counted rather than quietly
        skipped.  Dropped in silence, a page of garbage would leave an empty
        result indistinguishable from a venue that simply has no history there,
        and the caller needs to tell those apart (SPEC 4).

        The page cap is the same failure mode one order of magnitude up, and it
        used to be just as silent: ``limit=12500`` came back with exactly 10000
        bars, no exception, no warning, and the shortfall looked identical to a
        venue with no more history.  Hitting the cap while still short of
        ``wanted`` now logs its own warning - not the ``klines_paged`` line that
        fires on every ordinary multi-page walk, which is invisible precisely
        because it is always there.
        """
        forward = start is not None
        cursor: int | None = _to_ms(start) if forward else _to_ms(end)
        stop_at: int | None = _to_ms(end) if forward else None

        collected: dict[int, tuple[Any, float]] = {}
        malformed = 0
        pages = 0
        exhausted = False
        while len(collected) < wanted and pages < MAX_KLINE_PAGES:
            params: dict[str, Any] = {
                "symbol": pair,
                "interval": code,
                "limit": min(wanted - len(collected), MAX_KLINES_PER_REQUEST),
            }
            if forward:
                params["startTime"] = cursor
                if stop_at is not None:
                    params["endTime"] = stop_at
            elif cursor is not None:
                params["endTime"] = cursor

            payload, latency = await self._rest.get("/api/v3/klines", params)
            if not isinstance(payload, list):
                raise DataSourceUnavailableError(
                    f"binance-spot: klines {symbol} {interval.value} membalas "
                    f"bentuk tak terduga: {payload!r:.120}"
                )
            pages += 1

            before = len(collected)
            seen: list[int] = []
            for row in payload:
                open_ms = _row_open_ms(row)
                if open_ms is None:
                    malformed += 1
                    log.warning(
                        "binance.candle_malformed",
                        source=SOURCE,
                        symbol=symbol,
                        interval=interval.value,
                        detail="open time tidak terbaca",
                    )
                    continue
                seen.append(open_ms)
                collected.setdefault(open_ms, (row, latency))

            # Nothing new: the venue has no more history in this direction.
            # Reported by stopping, never padded (SPEC 4).
            if not seen or len(collected) == before:
                exhausted = True
                break
            cursor = max(seen) + 1 if forward else min(seen) - 1

        if pages > 1:
            log.info(
                "binance.klines_paged",
                source=SOURCE,
                symbol=symbol,
                interval=interval.value,
                pages=pages,
                bars=len(collected),
                wanted=wanted,
            )
        if not exhausted and pages >= MAX_KLINE_PAGES and len(collected) < wanted:
            # ARUNA stopped, not the venue.  Said plainly, because "fewer bars
            # than asked for" otherwise reads as history that does not exist.
            log.warning(
                "binance.page_cap_reached",
                source=SOURCE,
                symbol=symbol,
                interval=interval.value,
                wanted=wanted,
                bars=len(collected),
                pages=pages,
                cap=MAX_KLINE_PAGES,
            )
        return collected, malformed

    def _to_candle(
        self,
        row: Any,
        symbol: str,
        interval: Horizon,
        received: datetime,
        venue_now: datetime | None,
        latency: float,
    ) -> Candle | None:
        """One kline row into a :class:`Candle`, or ``None`` if it is not one.

        PASAL 10 wants a sequence/consistency check before anything downstream
        sees the data, and klines make one available for free: Binance states
        each bar's own close time, and it is always ``open + duration - 1ms``.
        A row that disagrees is not a bar of the interval that was requested,
        whatever its prices say - and a mislabelled bar is the kind of input
        that produces a confident, wrong analysis rather than an obvious error.

        Rejected rows are dropped and logged, never repaired.
        """
        if not isinstance(row, (list, tuple)) or len(row) < 7:
            log.warning(
                "binance.candle_malformed",
                source=SOURCE,
                symbol=symbol,
                interval=interval.value,
                detail="kolom kurang dari 7",
            )
            return None

        open_ms = _row_open_ms(row)
        venue_close_ms = _int(row[6])
        prices = [_optional_decimal(row[index]) for index in (1, 2, 3, 4)]
        volume = _optional_decimal(row[5])
        if open_ms is None or venue_close_ms is None or volume is None or None in prices:
            log.warning(
                "binance.candle_malformed",
                source=SOURCE,
                symbol=symbol,
                interval=interval.value,
                detail="timestamp, harga, atau volume tidak terbaca",
            )
            return None

        duration_ms = int(interval.duration.total_seconds() * 1_000)
        if venue_close_ms - open_ms != duration_ms - 1:
            log.warning(
                "binance.candle_interval_mismatch",
                source=SOURCE,
                symbol=symbol,
                interval=interval.value,
                open_ms=open_ms,
                span_ms=venue_close_ms - open_ms,
                expected_ms=duration_ms - 1,
            )
            return None

        # Intraday and daily bars are anchored to the epoch, so the modulo test
        # is exact for them.  3d and 1w are anchored to a venue-chosen origin -
        # weekly bars open on a Monday and the epoch was a Thursday - so
        # applying it there would reject every correct weekly bar.
        if duration_ms <= 86_400_000 and open_ms % duration_ms != 0:
            log.warning(
                "binance.candle_misaligned",
                source=SOURCE,
                symbol=symbol,
                interval=interval.value,
                open_ms=open_ms,
            )
            return None

        open_time = _ms_to_utc(open_ms)
        assert open_time is not None
        close_time = open_time + interval.duration
        open_, high, low, close = prices  # type: ignore[misc]
        return Candle(
            market=Market.CRYPTO,
            symbol=symbol,
            interval=interval,
            open_time=open_time,
            close_time=close_time,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            quote_volume=_optional_decimal(row[7]) if len(row) > 7 else None,
            trade_count=_int(row[8]) if len(row) > 8 else None,
            # Against the venue's clock, and only if it has been measured.
            # ``received`` is this machine's clock: it is the right answer for
            # "when did we see this" (provenance) and the wrong one for "has
            # this bar closed", because a machine running 30 seconds fast
            # settles a bar whose close price has not happened yet - and the
            # measured skew on this machine was 12.2 seconds on 2026-08-17, so
            # the error is not hypothetical, only its sign was lucky.
            # ``venue_now is None`` means the venue clock could not be read at
            # all, and then nothing is closed: delayed evidence, not invented
            # evidence (SPEC 24).  The venue's own close is one millisecond
            # earlier than ARUNA's convention, which errs the same safe way.
            is_closed=venue_now is not None and close_time <= venue_now,
            provenance=Provenance(
                source=SOURCE,
                server_timestamp=received,
                provider_timestamp=open_time,
                latency_ms=latency,
            ),
        )

    async def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook | None:
        pair = self.provider_symbol(symbol)
        payload, latency = await self._rest.get(
            "/api/v3/depth", {"symbol": pair, "limit": _depth_limit(depth)}
        )
        if not isinstance(payload, dict):
            raise DataSourceUnavailableError(
                f"binance-spot: depth {symbol} membalas bentuk tak terduga: "
                f"{payload!r:.120}"
            )
        return OrderBook(
            market=Market.CRYPTO,
            symbol=symbol,
            bids=_levels(payload.get("bids"), depth),
            asks=_levels(payload.get("asks"), depth),
            provenance=Provenance(
                source=SOURCE, server_timestamp=now_utc(), latency_ms=latency
            ),
        )

    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        payload, latency = await self._ticker(symbol)
        received = now_utc()

        last = _decimal(payload.get("lastPrice"), f"{symbol} last price")
        bid = _optional_decimal(payload.get("bidPrice"))
        ask = _optional_decimal(payload.get("askPrice"))

        spread_bps: Decimal | None = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2
            if mid > 0:
                spread_bps = quantize_bps((ask - bid) / mid * Decimal(10_000))

        return Snapshot(
            market=Market.CRYPTO,
            symbol=symbol,
            captured_at=received,
            last_price=last,
            bid=bid,
            ask=ask,
            spread_bps=spread_bps,
            high_24h=_optional_decimal(payload.get("highPrice")),
            low_24h=_optional_decimal(payload.get("lowPrice")),
            # ``volume`` is base asset; ``quoteVolume`` is the USDT side and is
            # kept in ``raw`` rather than mixed into the same field.
            volume_24h=_optional_decimal(payload.get("volume")),
            change_24h_pct=quantize_pct(
                _optional_decimal(payload.get("priceChangePercent"))
            ),
            session=crypto_session(received).value,
            # Crypto never closes, so "is the market open" has no meaning here.
            market_open=None,
            provenance=Provenance(
                source=SOURCE,
                server_timestamp=received,
                provider_timestamp=_ms_to_utc(payload.get("closeTime")),
                latency_ms=latency,
            ),
            raw=dict(payload),
        )

    async def fetch_metadata(self, symbol: str) -> SymbolMetadata:
        """Venue trading rules for one pair (``/api/v3/exchangeInfo``).

        Not part of the :class:`~aruna.data.provider.MarketDataProvider`
        contract - no other venue in this project publishes it - but PASAL 8
        names metadata as REST work, and this is the only honest source for
        ``assets.tick_size`` / ``assets.price_precision``.
        """
        pair = self.provider_symbol(symbol)
        payload, latency = await self._rest.get(
            "/api/v3/exchangeInfo", {"symbol": pair}
        )
        entries = payload.get("symbols") if isinstance(payload, dict) else None
        entry = next(
            (e for e in entries or [] if isinstance(e, dict) and e.get("symbol") == pair),
            None,
        )
        if entry is None:
            raise DataSourceUnavailableError(
                f"binance-spot tidak melisting {symbol} ({pair}); tidak ada tick "
                "size maupun precision untuk pair ini"
            )

        filters = {
            f.get("filterType"): f
            for f in entry.get("filters", [])
            if isinstance(f, dict)
        }
        return SymbolMetadata(
            symbol=symbol,
            venue_symbol=pair,
            base_asset=str(entry.get("baseAsset", "")),
            quote_asset=str(entry.get("quoteAsset", "")),
            status=str(entry.get("status", "")),
            tick_size=_optional_decimal(filters.get("PRICE_FILTER", {}).get("tickSize")),
            step_size=_optional_decimal(filters.get("LOT_SIZE", {}).get("stepSize")),
            min_notional=_optional_decimal(
                filters.get("NOTIONAL", {}).get("minNotional")
                or filters.get("MIN_NOTIONAL", {}).get("minNotional")
            ),
            provenance=Provenance(
                source=SOURCE, server_timestamp=now_utc(), latency_ms=latency
            ),
        )


# ---------------------------------------------------------------------------
# Parsing helpers - strict, because a silently coerced price is a wrong price.
# ---------------------------------------------------------------------------


def _decimal(value: Any, label: str) -> Decimal:
    result = _optional_decimal(value)
    if result is None:
        raise DataSourceUnavailableError(
            f"binance-spot tidak mengembalikan {label} yang bisa dipakai: {value!r}"
        )
    return result


def _optional_decimal(value: Any) -> Decimal | None:
    """A venue string as a Decimal, or ``None`` if it is not a usable number.

    ``Decimal("NaN")`` and ``Decimal("Infinity")`` are perfectly legal
    constructions that raise nothing, so a parser calling itself strict while
    catching only ``InvalidOperation`` let both through: they passed the
    ``None in prices`` check in :meth:`BinanceSpotProvider._to_candle` and blew
    up far downstream in ``quality._check_price_sane`` as
    ``decimal.InvalidOperation`` - an exception the per-asset handler in the
    ingest loop does not catch, so one poisoned field ended the poll for every
    other asset.  Refusing it here routes it into the rejection path that
    already exists: a malformed row is logged and dropped, a required price
    raises ``DataSourceUnavailableError``.
    """
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_ms(value: datetime | None) -> int | None:
    """An aware datetime as milliseconds since the epoch, which is Binance's unit."""
    if value is None:
        return None
    return int(value.timestamp() * 1_000)


def _ms_to_utc(value: Any) -> datetime | None:
    """Binance timestamps are milliseconds since the epoch, UTC."""
    number = _int(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number / 1_000, tz=UTC)


def _row_open_ms(row: Any) -> int | None:
    if not isinstance(row, (list, tuple)) or not row:
        return None
    open_ms = _int(row[0])
    return open_ms if open_ms is not None and open_ms > 0 else None


def _retry_after_sec(response: httpx.Response) -> float | None:
    """``Retry-After`` in seconds, when the venue states one."""
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        seconds = float(header)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _venue_error(response: httpx.Response) -> str:
    """Binance's own ``{"code": -1121, "msg": "Invalid symbol."}``, when present.

    The code is worth surfacing: -1121 is a symbol that does not exist, -1120
    an interval that does not, and both read very differently from an outage.
    """
    try:
        body = response.json()
    except ValueError:
        return repr(response.text[:120])
    if isinstance(body, dict) and "msg" in body:
        return f"{body.get('msg')} (code {body.get('code')})"
    return repr(response.text[:120])


def _depth_limit(depth: int) -> int:
    """Smallest legal ``/api/v3/depth`` size that still covers ``depth``."""
    for allowed in DEPTH_LIMITS:
        if allowed >= depth:
            return allowed
    return DEPTH_LIMITS[-1]


def _levels(rows: Any, depth: int) -> tuple[OrderBookLevel, ...]:
    if not isinstance(rows, list):
        return ()
    levels: list[OrderBookLevel] = []
    for row in rows[:depth]:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        price = _optional_decimal(row[0])
        quantity = _optional_decimal(row[1])
        if price is None or quantity is None:
            continue
        levels.append(OrderBookLevel(price=price, quantity=quantity))
    return tuple(levels)


__all__ = [
    "CLOCK_MAX_AGE_SEC",
    "DEPTH_LIMITS",
    "HOSTS",
    "INTERVALS",
    "MAX_KLINES_PER_REQUEST",
    "MAX_KLINE_PAGES",
    "PUBLIC_ENDPOINTS",
    "SOURCE",
    "WEIGHT_LIMIT_1M",
    "BinanceBlocked",
    "BinanceRateLimited",
    "BinanceSpotProvider",
    "BinanceSpotRest",
    "ClockReading",
    "ForbiddenEndpoint",
    "SymbolMetadata",
]
