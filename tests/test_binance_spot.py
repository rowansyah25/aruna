"""Binance spot adapter (PASAL 5, 6, 8, 10, 33, 41).

No network anywhere in this file.  Three layers, and which one a test stands on
decides what the test is able to notice:

* :class:`FakeRest` replaces the whole transport, so the mapping from Binance's
  payloads onto ARUNA's records is measured on its own.
* :class:`FakeFetcher` replaces the HTTP client, so status codes, response
  headers and host failover can be exercised exactly - a 418 is a fact about
  the venue's answer, and waiting for a real one is not a test strategy.
* :func:`live_rest` replaces **only the socket**: a real
  :class:`~aruna.data.crypto.binance.BinanceSpotRest` over a real
  :class:`~aruna.data.http.HttpFetcher` over :class:`httpx.MockTransport`.

That third layer exists because of a fault this file used to have.  The
rate-limit tests ran on :class:`FakeFetcher`, which has no retry loop at all, so
``assert len(fetcher.calls) == 1`` was true of the fake and false of the code
that ships: the real fetcher retried a 429 three more times into an active rate
limit before the adapter's cooldown ever ran.  A property that lives in
``HttpFetcher`` can only be tested through ``HttpFetcher``; anything about
request counts, retries or redirects therefore belongs on :func:`live_rest`.

The previous crypto adapter had **no transport test at all**: eighteen of its
twenty behaviours were never executed by the suite, so nothing here would have
changed colour if its kline parsing had been wrong.  Every test below was
verified to go red by removing the line it is about.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar
from unittest import mock

import httpx
import pytest

from aruna.core.clock import monotonic, now_utc
from aruna.core.config import DataSettings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.data.crypto import binance as binance_module
from aruna.data.crypto.binance import (
    CLOCK_MAX_AGE_SEC,
    DEPTH_LIMITS,
    HOSTS,
    INTERVALS,
    MAX_KLINE_PAGES,
    MAX_KLINES_PER_REQUEST,
    SOURCE,
    BinanceBlocked,
    BinanceRateLimited,
    BinanceSpotProvider,
    BinanceSpotRest,
    ClockReading,
    ForbiddenEndpoint,
)
from aruna.data.crypto.symbols import (
    ENGINE_QUOTE_ASSETS,
    QUOTE_ASSETS,
    to_canonical_symbol,
    to_venue_symbol,
)
from aruna.data.provider import Transport
from aruna.data.registry import available, build_provider

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
WEEK_MS = 604_800_000


def settings() -> DataSettings:
    return DataSettings(_env_file=None)


def aligned_minute(moment: datetime) -> int:
    """Epoch ms of the minute ``moment`` falls in - how Binance opens a 1m bar."""
    stamp = int(moment.timestamp() * 1000)
    return stamp - (stamp % MINUTE_MS)


def kline(
    open_ms: int,
    *,
    span_ms: int = MINUTE_MS,
    close_ms: int | None = None,
    open_: str = "100.00",
    high: str = "101.00",
    low: str = "99.00",
    close: str = "100.50",
    volume: str = "1.25000000",
) -> list[Any]:
    """One row in Binance's kline shape: twelve columns, epoch milliseconds."""
    return [
        open_ms,
        open_,
        high,
        low,
        close,
        volume,
        close_ms if close_ms is not None else open_ms + span_ms - 1,
        "125.75000000",
        17,
        "0.60000000",
        "60.30000000",
        "0",
    ]


TICKER_PAYLOAD: dict[str, Any] = {
    "symbol": "BTCUSDT",
    "priceChange": "-560.10000000",
    "priceChangePercent": "-0.472",
    "weightedAvgPrice": "118000.11",
    "lastPrice": "118234.56000000",
    "lastQty": "0.00120000",
    "bidPrice": "118234.55000000",
    "bidQty": "3.41000000",
    "askPrice": "118234.56000000",
    "askQty": "1.20000000",
    "openPrice": "118794.66000000",
    "highPrice": "119400.00000000",
    "lowPrice": "117050.02000000",
    "volume": "12345.67000000",
    "quoteVolume": "1456789012.34000000",
    "openTime": 1_755_000_000_000,
    "closeTime": 1_755_086_400_000,
    "count": 1_234_567,
}


class FakeRest:
    """The transport reduced to "a path goes in, a payload comes out".

    Records every call so a test can assert on what was *asked for*, not only
    on what came back - the pagination and depth-rounding behaviours are
    entirely about the request.

    ``venue_skew_sec`` models the one thing this fake cannot be allowed to
    hide: the venue's clock is not this machine's clock.  ``0.0`` means the two
    agree, a number means they differ by that many seconds, and ``None`` means
    the venue clock has never been read - which is why the default is a real
    value and the tests that care set it explicitly.
    """

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.host = "https://fake.binance.test"
        self.hosts = ("https://fake.binance.test",)
        self.max_retries = 3
        self.used_weight_1m: int | None = None
        self.venue_skew_sec: float | None = 0.0
        self.opened = 0
        self.closed = 0

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1

    async def venue_now(self) -> datetime | None:
        if self.venue_skew_sec is None:
            return None
        return now_utc() + timedelta(seconds=self.venue_skew_sec)

    async def measure_clock(self) -> ClockReading | None:
        payload, latency = await self.get("/api/v3/time")
        server_ms = payload.get("serverTime") if isinstance(payload, dict) else None
        if server_ms is None:
            return None
        server_time = datetime.fromtimestamp(server_ms / 1000, tz=UTC)
        measured_at = now_utc()
        self.venue_skew_sec = (server_time - measured_at).total_seconds()
        return ClockReading(
            server_time=server_time,
            offset_sec=self.venue_skew_sec,
            measured_at=measured_at,
            measured_mono=monotonic(),
            latency_ms=latency,
        )

    async def get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> tuple[Any, float]:
        sent = dict(params or {})
        self.calls.append((path, sent))
        if path not in self.responses:
            raise AssertionError(f"adapter asked for an unexpected path: {path}")
        payload = self.responses[path]
        if callable(payload):
            payload = payload(sent)
        return payload, 11.5

    def paths(self) -> list[str]:
        return [path for path, _ in self.calls]


class FakeKlineVenue:
    """A venue holding a fixed run of 1m bars, answering like Binance does.

    Honours ``limit``, ``startTime`` and ``endTime`` with Binance's anchoring:
    a request bounded by ``endTime`` returns the *newest* bars at or before it,
    a request bounded by ``startTime`` the oldest at or after it.  Getting that
    wrong in the adapter is how a request for the last 50 bars comes back with
    the 50 oldest bars in existence, which is why the fake models it.
    """

    def __init__(self, *, bars: int, newest_open_ms: int, span_ms: int = MINUTE_MS) -> None:
        self.span_ms = span_ms
        self.opens = sorted(newest_open_ms - index * span_ms for index in range(bars))
        self.requests: list[dict[str, Any]] = []
        #: Every bar handed out, in order, repeats included.  This is what
        #: exposes a cursor that does not move: the output can look perfectly
        #: clean while the same bar is being fetched again on every page.
        self.served: list[int] = []

    def __call__(self, params: dict[str, Any]) -> list[list[Any]]:
        self.requests.append(dict(params))
        limit = int(params.get("limit", 500))
        start = params.get("startTime")
        end = params.get("endTime")

        window = [
            open_ms
            for open_ms in self.opens
            if (start is None or open_ms >= start) and (end is None or open_ms <= end)
        ]
        chosen = window[:limit] if start is not None else window[-limit:]
        self.served.extend(chosen)
        return [kline(open_ms, span_ms=self.span_ms) for open_ms in chosen]


class FakeFetcher:
    """Stands in for :class:`~aruna.data.http.HttpFetcher`.

    ``handler`` receives the full URL and returns an :class:`httpx.Response` to
    hand back, or an exception instance to raise.  Returning the response
    rather than raising on 4xx is the whole point of ``get_response``: the
    adapter has to see 418, 429 and 451 as different things.

    A host that cannot be reached at all raises ``DataSourceUnavailableError``,
    not ``httpx.ConnectError`` - the real fetcher has already retried and
    wrapped it by then, and a fake that raises the httpx error instead would
    let the adapter pass a test against a contract nothing implements.
    """

    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.calls: list[str] = []
        self.retry_statuses: list[frozenset[int] | None] = []
        self.retry_budgets: list[int | None] = []
        self.opened = 0
        self.closed = 0

    async def open(self) -> None:
        self.opened += 1

    async def close(self) -> None:
        self.closed += 1

    async def get_response(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        retry_statuses: frozenset[int] | None = None,
        max_retries: int | None = None,
    ) -> tuple[httpx.Response, float]:
        self.calls.append(url)
        self.retry_statuses.append(retry_statuses)
        self.retry_budgets.append(max_retries)
        result = self.handler(url, dict(params or {}))
        if isinstance(result, BaseException):
            raise result
        return result, 7.5


def provider_with(responses: dict[str, Any]) -> tuple[BinanceSpotProvider, FakeRest]:
    rest = FakeRest(responses)
    return BinanceSpotProvider(settings(), rest=rest), rest  # type: ignore[arg-type]


def live_rest(
    handler: Any, *, hosts: tuple[str, ...] = HOSTS, max_retries: int = 3
) -> tuple[BinanceSpotRest, list[httpx.Request]]:
    """A real transport stack with a mock socket underneath it.

    ``BinanceSpotRest`` builds its own ``HttpFetcher`` here, exactly as it does
    in production, so the retry loop, the retry-status set and the redirect
    policy under test are the ones that ship.  Only the transport at the bottom
    is swapped, and every request that reaches it is recorded - request counts
    are the whole point, and they exist nowhere else in the stack.

    A fake fetcher cannot substitute: it has no retry loop, so it agrees with
    any expectation a test writes down.
    """
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    rest = BinanceSpotRest(hosts=hosts, max_retries=max_retries, timeout_sec=5.0)
    rest._http._transport = httpx.MockTransport(record)
    return rest, sent


def live_provider(
    handler: Any, *, hosts: tuple[str, ...] = HOSTS, max_retries: int = 3
) -> tuple[BinanceSpotProvider, list[httpx.Request]]:
    """The provider on top of :func:`live_rest` - the whole live read path."""
    rest, sent = live_rest(handler, hosts=hosts, max_retries=max_retries)
    return BinanceSpotProvider(settings(), rest=rest), sent


class RecordingLog:
    """Captures ``log.warning`` / ``log.info`` events by name and fields.

    Used where the fix *is* the log line: a shortfall the caller cannot see in
    the return value is only reported if something says so out loud.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def info(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def error(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def fields(self, event: str) -> list[dict[str, Any]]:
        return [values for name, values in self.events if name == event]


# ---------------------------------------------------------------------------
# PASAL 41 - no execution path exists
# ---------------------------------------------------------------------------


class TestReadOnly:
    """These must never be relaxed."""

    FORBIDDEN = (
        "/order",
        "/openOrders",
        "/allOrders",
        "/account",
        "/myTrades",
        "/userDataStream",
        "/sapi/v1/capital/withdraw/apply",
        "/asset/transfer",
    )

    def test_no_execution_endpoint_is_allowlisted(self) -> None:
        for path in binance_module.PUBLIC_ENDPOINTS:
            for forbidden in self.FORBIDDEN:
                assert not path.endswith(forbidden), path

    def test_the_module_contains_no_execution_path_at_all(self) -> None:
        """The check that survives somebody adding "just one small helper"."""
        body = inspect.getsource(binance_module).split('"""', 2)[-1]
        for forbidden in self.FORBIDDEN:
            assert f'"{forbidden}"' not in body, forbidden
            assert f"'{forbidden}'" not in body, forbidden

    def test_nothing_signs_a_request(self) -> None:
        source = inspect.getsource(binance_module).lower()
        for marker in ("hmac", "signature", "api_secret", "apisecret", "x-mbx-apikey"):
            assert marker not in source, marker

    async def test_an_unlisted_path_is_refused_before_any_request(self) -> None:
        fetcher = FakeFetcher(lambda url, params: httpx.Response(200, json={}))
        rest = BinanceSpotRest(fetcher=fetcher)  # type: ignore[arg-type]

        with pytest.raises(ForbiddenEndpoint) as exc:
            await rest.get("/api/v3/order", {"symbol": "BTCUSDT"})

        assert "read-only" in str(exc.value)
        # Refused *before* the wire, not after: nothing was sent.
        assert fetcher.calls == []

    def test_the_allowlist_is_exactly_what_the_module_calls(self) -> None:
        """Every entry has a caller, and every caller has an entry.

        The allowlist is the only structural narrowing PASAL 41 has in this
        module, and an entry nobody calls is an entry nobody reviews again -
        ``/api/v3/ticker/bookTicker`` sat here for a phase with zero callers,
        two weight per call it never spent.  Widening the surface is then free
        and invisible, which is the opposite of what this list is for.
        """
        source = inspect.getsource(binance_module)
        called = set(re.findall(r'get\(\s*"(/api/v3/[A-Za-z0-9/]+)"', source))

        assert called == set(binance_module.PUBLIC_ENDPOINTS)

    def test_the_provider_exposes_no_execution_method(self) -> None:
        methods = {
            name
            for name, _ in inspect.getmembers(BinanceSpotProvider, inspect.isfunction)
        }
        for banned in (
            "create_order",
            "place_order",
            "cancel_order",
            "new_order",
            "transfer",
            "withdraw",
        ):
            assert banned not in methods


# ---------------------------------------------------------------------------
# Symbol mapping - one place, both directions
# ---------------------------------------------------------------------------


class TestSymbolMapping:
    def test_canonical_becomes_the_venue_form(self) -> None:
        assert to_venue_symbol("BTC/USDT") == "BTCUSDT"
        assert to_venue_symbol("ETH/USDT") == "ETHUSDT"
        assert to_venue_symbol("SOL/USDT") == "SOLUSDT"

    def test_the_venue_form_is_uppercase(self) -> None:
        """Binance is case-sensitive on REST: ``btcusdt`` is ``Invalid symbol``."""
        assert to_venue_symbol("btc/usdt") == "BTCUSDT"
        assert to_venue_symbol("Btc/Usdt").isupper()

    @pytest.mark.parametrize("written", ["BTC-USDT", "BTC_USDT", "  btc/usdt  "])
    def test_hand_typed_separators_are_accepted(self, written: str) -> None:
        assert to_venue_symbol(written) == "BTCUSDT"

    def test_an_idr_pair_is_refused_rather_than_translated(self) -> None:
        """PASAL 6.  ``BTCIDR`` is not listed, so passing it through would come
        back as HTTP 400 - which reads like an outage, not like a rule."""
        with pytest.raises(ValueError, match="PASAL 6"):
            to_venue_symbol("BTC/IDR")

    @pytest.mark.parametrize(
        "symbol", ["BTC/USDC", "BTC/BUSD", "ETH/FDUSD", "BTC/TUSD", "btc/usdc"]
    )
    def test_a_non_usdt_quote_is_refused_even_though_the_venue_lists_it(
        self, symbol: str
    ) -> None:
        """PASAL 33 is "CRYPTO: USDT PAIRS ONLY", and this is the gate that
        enforces it.

        Unlike ``BTC/IDR``, these pairs exist at Binance and answer with a real
        price: ``BTC/USDC`` was fetched live at 63629.57 while ``BTC/USDT`` sat
        at 63690.92 - different markets, not different labels.  The only thing
        keeping them out before was that nobody had seeded one, which is a
        property of a table, not a rule.
        """
        with pytest.raises(ValueError, match="PASAL 33"):
            to_venue_symbol(symbol)

    def test_the_inbound_gate_is_usdt_and_nothing_else(self) -> None:
        """Red the moment someone widens the tuple instead of writing down a
        deviation from PASAL 33."""
        assert ENGINE_QUOTE_ASSETS == ("USDT",)

    def test_reading_a_venue_symbol_still_knows_the_wider_vocabulary(self) -> None:
        """The two directions answer different questions, so they keep different
        lists: splitting ``ETHFDUSD`` correctly is better than splitting it at a
        guessed offset, and the refusal happens on the way back in."""
        assert to_canonical_symbol("ETHFDUSD") == "ETH/FDUSD"
        assert to_canonical_symbol("BTCUSDC") == "BTC/USDC"
        with pytest.raises(ValueError, match="PASAL 33"):
            to_venue_symbol(to_canonical_symbol("BTCUSDC"))

    def test_a_symbol_without_a_quote_is_refused(self) -> None:
        with pytest.raises(ValueError, match="BASE/QUOTE"):
            to_venue_symbol("BTCUSDT")

    def test_the_venue_form_maps_back(self) -> None:
        assert to_canonical_symbol("BTCUSDT") == "BTC/USDT"
        assert to_canonical_symbol("ETHFDUSD") == "ETH/FDUSD"
        assert to_canonical_symbol("USDCUSDT") == "USDC/USDT"

    def test_an_unknown_quote_is_not_split_by_guessing(self) -> None:
        """A mis-split symbol matches no asset row, and the failure surfaces
        far away from the guess that caused it."""
        with pytest.raises(ValueError, match="tebakan"):
            to_canonical_symbol("ETHBTC")

    @pytest.mark.parametrize("base", ["BTC", "ETH", "SOL", "BNB", "XRP"])
    def test_round_trip_is_stable(self, base: str) -> None:
        canonical = f"{base}/USDT"
        assert to_canonical_symbol(to_venue_symbol(canonical)) == canonical

    def test_usdt_is_a_recognised_quote(self) -> None:
        assert "USDT" in QUOTE_ASSETS
        assert "IDR" not in QUOTE_ASSETS
        assert "USDT" in ENGINE_QUOTE_ASSETS

    def test_the_adapter_uses_that_one_mapping(self) -> None:
        provider, _ = provider_with({})
        assert provider.provider_symbol("BTC/USDT") == "BTCUSDT"

    async def test_a_bad_symbol_costs_one_asset_not_the_whole_poll(self) -> None:
        """``poll_once`` catches ``DataSourceUnavailableError`` per asset and
        lets ``ValueError`` escape.  If this leaked a ValueError, one leftover
        IDR row in the universe would end the poll for every other symbol."""
        provider, _ = provider_with({})

        with pytest.raises(DataSourceUnavailableError):
            provider.provider_symbol("BTC/IDR")


# ---------------------------------------------------------------------------
# Capabilities - PASAL 8, SPEC 47
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_it_is_registered_under_its_own_name(self) -> None:
        assert "binance-spot" in available(Market.CRYPTO)
        provider = build_provider(Market.CRYPTO, "binance-spot", settings())
        assert provider.name == SOURCE
        assert provider.market is Market.CRYPTO

    def test_it_declares_polling_not_streaming(self) -> None:
        """PASAL 8 adds a websocket later.  Saying STREAM now would misstate
        this adapter's own latency."""
        caps = BinanceSpotProvider(settings()).capabilities
        assert caps.transport is Transport.POLL
        assert caps.is_realtime is True
        assert caps.expected_delay_sec == 0
        assert caps.supports_order_book is True
        assert caps.requires_credentials is False

    def test_the_transport_is_swappable_without_touching_capabilities(self) -> None:
        """The structural half of PASAL 8: a streaming subclass changes one
        attribute.  Had POLL been a literal inside ``capabilities``, adding
        STREAM would have meant rewriting capabilities, the registry and the
        health surface at the same time."""
        provider = BinanceSpotProvider(settings())
        provider._transport = Transport.STREAM

        assert provider.capabilities.transport is Transport.STREAM

    def test_intervals_are_the_ones_binance_actually_serves(self) -> None:
        caps = BinanceSpotProvider(settings()).capabilities
        # Native on Binance spot, unlike the previous source.
        assert caps.supports(Horizon.M3)
        # Binance has no 10m at all, and nothing here derives one.
        assert not caps.supports(Horizon.M10)
        # 1M exists at the venue but is a calendar month, not a flat 30 days.
        assert not caps.supports(Horizon.MO1)
        assert caps.supports(Horizon.M1)
        assert caps.supports(Horizon.W1)

    def test_the_kline_cap_is_binances_own(self) -> None:
        caps = BinanceSpotProvider(settings()).capabilities
        assert caps.max_candles_per_request == MAX_KLINES_PER_REQUEST == 1_000

    def test_the_regulatory_note_states_the_legal_fact(self) -> None:
        """Bappebti registration is a legal fact and survives any network
        measurement.  A claim about reachability would not."""
        caps = BinanceSpotProvider(settings()).capabilities
        assert "Bappebti" in caps.regulatory_note
        assert "diblokir" not in caps.regulatory_note.lower()
        assert "trustpositif" not in caps.regulatory_note.lower()

    def test_the_limitations_name_the_things_flags_cannot(self) -> None:
        caps = BinanceSpotProvider(settings()).capabilities
        joined = " ".join(caps.limitations)
        assert "read-only" in joined
        assert "10m" in joined
        assert "1000 bar" in joined

    def test_no_limitation_promises_a_resampling_that_does_not_exist(self) -> None:
        """This text is printed by ``aruna providers``, and it used to say the
        10m horizon "di-resample dari 1m".

        Nothing resamples anything: :mod:`aruna.data.resample` has zero callers
        in ``src/``, no candle row carries a ``resampled`` source, and asking
        for 10m raises.  An operator reading that line would have believed a
        derived bar was available (SPEC 4).  Red again the moment the promise
        comes back.
        """
        caps = BinanceSpotProvider(settings()).capabilities
        joined = " ".join(caps.limitations).lower()

        assert "resample" not in joined
        assert "10m tidak ada di binance spot" in joined
        assert "tidak diturunkan dari" in joined

    def test_the_limitations_state_what_a_failover_costs(self) -> None:
        """One ``fetch_candles`` against a venue-wide 5xx sent 12 requests over
        15.4 seconds while the poll interval was 5.  The number is now bounded
        by the rotation, and stated where an operator sizing a poll interval
        would look."""
        caps = BinanceSpotProvider(settings()).capabilities
        joined = " ".join(caps.limitations)

        assert "5xx" in joined
        assert "429 tidak pernah di-retry" in joined
        assert "is_closed dinilai dengan jam venue" in joined

    def test_the_source_name_separates_it_from_the_futures_feed(self) -> None:
        """``source`` is written into candles, snapshots and provider_events.
        One shared 'binance' would make two feeds with different prices
        indistinguishable after the fact."""
        from aruna.futures.binance import BinanceFuturesProvider

        assert SOURCE == "binance-spot"
        assert BinanceFuturesProvider.name != SOURCE


# ---------------------------------------------------------------------------
# Candles - PASAL 10 and SPEC 24
# ---------------------------------------------------------------------------


class TestCandles:
    async def test_a_kline_row_is_parsed_column_by_column(self) -> None:
        open_ms = aligned_minute(now_utc() - timedelta(minutes=10))
        provider, rest = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)

        assert len(candles) == 1
        candle = candles[0]
        assert candle.open == Decimal("100.00")
        assert candle.high == Decimal("101.00")
        assert candle.low == Decimal("99.00")
        assert candle.close == Decimal("100.50")
        assert candle.volume == Decimal("1.25000000")
        assert candle.quote_volume == Decimal("125.75000000")
        assert candle.trade_count == 17
        assert candle.open_time == datetime.fromtimestamp(open_ms / 1000, tz=UTC)
        assert candle.close_time == candle.open_time + timedelta(minutes=1)
        assert candle.is_coherent
        assert rest.calls[0][1]["symbol"] == "BTCUSDT"
        assert rest.calls[0][1]["interval"] == "1m"

    async def test_provenance_records_the_source_and_the_venue_timestamp(self) -> None:
        open_ms = aligned_minute(now_utc() - timedelta(minutes=5))
        provider, _ = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})

        candle = (await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1))[0]

        assert candle.provenance.source == SOURCE
        assert candle.provenance.provider_timestamp == candle.open_time
        assert candle.provenance.latency_ms == 11.5
        assert candle.provenance.declared_delay_sec == 0

    async def test_an_elapsed_bar_is_closed(self) -> None:
        open_ms = aligned_minute(now_utc() - timedelta(minutes=30))
        provider, rest = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})
        rest.venue_skew_sec = 0.0  # the two clocks agree

        candle = (await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1))[0]

        assert candle.is_closed is True

    async def test_the_bar_still_forming_is_not_marked_closed(self) -> None:
        """Binance hands back the running bar with no marker of any kind.
        Storing it closed would let outcome resolution settle a prediction
        against a close that has not happened yet (SPEC 24)."""
        open_ms = aligned_minute(now_utc())
        provider, rest = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})
        rest.venue_skew_sec = 0.0

        candle = (await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1))[0]

        assert candle.is_closed is False

    async def test_a_bar_whose_span_is_not_the_interval_is_rejected(self) -> None:
        """PASAL 10, sequence/consistency.  Binance states every bar's own close
        time as open + duration - 1ms.  A row that disagrees is not a bar of
        the interval that was asked for, whatever its prices look like."""
        open_ms = aligned_minute(now_utc() - timedelta(minutes=10))
        good = kline(open_ms)
        bad = kline(open_ms + MINUTE_MS, close_ms=open_ms + MINUTE_MS + HOUR_MS - 1)
        provider, _ = provider_with({"/api/v3/klines": lambda p: [good, bad]})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=10)

        assert [c.open_time.timestamp() * 1000 for c in candles] == [open_ms]

    async def test_a_bar_not_aligned_to_its_interval_is_rejected(self) -> None:
        open_ms = aligned_minute(now_utc() - timedelta(minutes=10)) + 17_000
        provider, _ = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})

        with pytest.raises(DataSourceUnavailableError, match="pemeriksaan bentuk"):
            await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)

    async def test_a_weekly_bar_survives_the_alignment_check(self) -> None:
        """Weekly bars open on a Monday and the epoch was a Thursday, so a
        blanket ``open_ms %% duration == 0`` would reject every correct weekly
        bar the venue has ever published."""
        monday = datetime(2026, 8, 10, tzinfo=UTC)
        open_ms = int(monday.timestamp() * 1000)
        assert open_ms % WEEK_MS != 0  # the trap this test exists for
        provider, _ = provider_with(
            {"/api/v3/klines": lambda p: [kline(open_ms, span_ms=WEEK_MS)]}
        )

        candles = await provider.fetch_candles("BTC/USDT", Horizon.W1, limit=1)

        assert len(candles) == 1
        assert candles[0].open_time == monday

    async def test_an_unsupported_interval_is_refused_not_silently_resampled(
        self,
    ) -> None:
        provider, rest = provider_with({})

        with pytest.raises(ValueError, match="10m"):
            await provider.fetch_candles("BTC/USDT", Horizon.M10, limit=5)

        assert rest.calls == []

    async def test_a_payload_of_the_wrong_shape_is_an_outage_not_an_empty_list(
        self,
    ) -> None:
        provider, _ = provider_with({"/api/v3/klines": lambda p: {"code": -1121}})

        with pytest.raises(DataSourceUnavailableError, match="bentuk tak terduga"):
            await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=5)

    async def test_rows_that_all_fail_are_reported_not_returned_as_no_data(self) -> None:
        """"No candles" and "every candle was garbage" need different
        reactions, and an empty list cannot tell them apart (SPEC 4)."""
        broken = [["not-a-timestamp", "1", "2"], [None, None]]
        provider, _ = provider_with({"/api/v3/klines": lambda p: broken})

        with pytest.raises(DataSourceUnavailableError, match="pemeriksaan bentuk"):
            await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=5)

    async def test_a_venue_with_nothing_returns_nothing(self) -> None:
        provider, _ = provider_with({"/api/v3/klines": lambda p: []})

        assert await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=5) == []

    async def test_candles_come_back_oldest_first(self) -> None:
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=30, newest_open_ms=newest)
        provider, _ = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=30)

        opens = [c.open_time for c in candles]
        assert opens == sorted(opens)
        assert len(set(opens)) == len(opens)


class TestIsClosedUsesTheVenueClock:
    """SPEC 24.  ``is_closed`` is a claim about the venue, not about this box.

    These tests exist because the ones they join could not fail on their own:
    a bar built from ``now_utc()`` and then checked against ``now_utc()`` makes
    the machine clock both the question and the answer.  Every test here makes
    the two clocks disagree, which is the only condition under which the fault
    is visible - and the measured disagreement on this machine on 2026-08-17
    was 12.2 seconds, with only its sign in our favour.
    """

    def bar_that_this_machine_would_call_closed(self) -> int:
        """A 1m bar that elapsed five minutes ago by the local clock."""
        return aligned_minute(now_utc() - timedelta(minutes=5))

    async def test_a_venue_running_behind_keeps_the_bar_open(self) -> None:
        """The dangerous direction, made visible: the machine says the bar
        closed four minutes ago, the venue says it is still forming.  Settling
        it here writes a close price that has not happened yet - measured live,
        the same bar closed at 63690.88 after being recorded at 63690.93, with
        a third of its volume still to come."""
        open_ms = self.bar_that_this_machine_would_call_closed()
        provider, rest = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})
        # Venue clock sits 30 seconds into that bar - it has not closed there.
        rest.venue_skew_sec = (open_ms + 30_000) / 1000 - now_utc().timestamp()

        candle = (await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1))[0]

        assert candle.is_closed is False

    async def test_a_bar_the_venue_has_finished_is_closed(self) -> None:
        """The mirror of the test above.  Without it, ``is_closed = False``
        everywhere would pass and no evidence would ever settle."""
        open_ms = self.bar_that_this_machine_would_call_closed()
        provider, rest = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})
        # Venue clock 30 seconds past the bar's close.
        rest.venue_skew_sec = (open_ms + 90_000) / 1000 - now_utc().timestamp()

        candle = (await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1))[0]

        assert candle.is_closed is True

    async def test_an_unmeasured_clock_closes_nothing(self) -> None:
        """No reading, no verdict.  Marking the bar open delays evidence, which
        the next poll repairs; marking it closed on a guess writes a settled row
        that nothing downstream will ever question."""
        open_ms = aligned_minute(now_utc() - timedelta(hours=3))
        provider, rest = provider_with({"/api/v3/klines": lambda p: [kline(open_ms)]})
        rest.venue_skew_sec = None  # /api/v3/time never answered

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)

        # The data still arrives - a clock failure must not lose candles.
        assert len(candles) == 1
        assert candles[0].is_closed is False

    async def test_the_live_read_path_really_asks_the_venue(self) -> None:
        """The wiring, not the arithmetic.

        Through the shipping transport: klines and ``/api/v3/time`` come from
        the same mock socket, and the venue's clock is planted 30 seconds inside
        a bar this machine finished with minutes ago.  Red if ``fetch_candles``
        stops asking for the venue clock, which is exactly how the measured
        skew ended up formatted into a status string and thrown away.
        """
        open_ms = self.bar_that_this_machine_would_call_closed()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v3/time":
                return httpx.Response(200, json={"serverTime": open_ms + 30_000})
            return httpx.Response(200, json=[kline(open_ms)])

        provider, sent = live_provider(handler, hosts=(HOSTS[0],))

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)
        await provider.close()

        assert len(candles) == 1
        assert candles[0].is_closed is False
        assert "/api/v3/time" in [request.url.path for request in sent]

    async def test_a_venue_clock_that_cannot_be_read_does_not_lose_the_candles(
        self,
    ) -> None:
        """``/api/v3/time`` down on every host, klines fine.  The bars still
        arrive, none of them settled."""
        open_ms = self.bar_that_this_machine_would_call_closed()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v3/time":
                return httpx.Response(503, text="down")
            return httpx.Response(200, json=[kline(open_ms)])

        provider, _ = live_provider(handler, max_retries=0)

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)
        await provider.close()

        assert len(candles) == 1
        assert candles[0].is_closed is False


class TestCandlePaging:
    """A request larger than the venue's page must not come back short."""

    async def test_more_than_one_page_is_walked(self) -> None:
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=2_000, newest_open_ms=newest)
        provider, rest = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1_500)

        assert len(candles) == 1_500
        assert len(rest.calls) == 2
        assert venue.requests[0]["limit"] == MAX_KLINES_PER_REQUEST
        assert venue.requests[1]["limit"] == 500

    async def test_paging_produces_no_duplicate_bars(self) -> None:
        """PASAL 10, duplicate check - and there are two mechanisms, so there
        are two assertions.

        Keying the collected rows by open time is what removes a duplicate that
        has already arrived.  The *exclusive* cursor is what stops it arriving
        at all.  Only the second assertion notices when the cursor stops
        moving: the returned candles stay perfectly clean because the dict
        swallows the repeat, while the venue is asked for the boundary bar
        again on every page - and the walk quietly comes up one bar short of
        what was requested for each page it took.
        """
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=2_000, newest_open_ms=newest)
        provider, _ = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1_500)

        opens = [c.open_time for c in candles]
        assert len(set(opens)) == len(opens)
        assert len(venue.served) == len(set(venue.served))

    async def test_a_single_page_request_asks_once(self) -> None:
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=500, newest_open_ms=newest)
        provider, rest = provider_with({"/api/v3/klines": venue})

        await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=100)

        assert len(rest.calls) == 1
        assert venue.requests[0]["limit"] == 100

    async def test_a_venue_that_runs_out_stops_the_walk(self) -> None:
        """The shortfall is reported by absence.  Nothing is padded, and the
        adapter does not keep asking for history that does not exist."""
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=1_200, newest_open_ms=newest)
        provider, rest = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=5_000)

        assert len(candles) == 1_200
        assert len(rest.calls) <= binance_module.MAX_KLINE_PAGES

    async def test_without_a_start_the_newest_bars_are_the_ones_returned(self) -> None:
        """Anchoring on ``startTime`` when the caller named none would hand a
        request for the last 50 bars the 50 oldest the venue still holds."""
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=1_000, newest_open_ms=newest)
        provider, _ = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=50)

        assert int(candles[-1].open_time.timestamp() * 1000) == newest
        assert "startTime" not in venue.requests[0]

    async def test_a_start_is_sent_to_the_venue_and_pages_forward(self) -> None:
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=2_000, newest_open_ms=newest)
        start = datetime.fromtimestamp((newest - 1_500 * MINUTE_MS) / 1000, tz=UTC)
        provider, _ = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles(
            "BTC/USDT", Horizon.M1, limit=1_200, start=start
        )

        assert venue.requests[0]["startTime"] == int(start.timestamp() * 1000)
        assert venue.requests[1]["startTime"] > venue.requests[0]["startTime"]
        assert candles[0].open_time >= start
        assert len(candles) == 1_200

    async def test_hitting_the_page_cap_is_said_out_loud(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cap is ARUNA's limit, not the venue's, and silence made the two
        indistinguishable.

        ``--limit 12500`` returned exactly 10000 bars, no exception, no warning:
        the same failure this adapter's own docstring says was fixed on the IDX
        side, one order of magnitude up.  ``klines_paged`` does not count - it
        fires on every ordinary multi-page walk, and a line that is always there
        reports nothing.
        """
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=20_000, newest_open_ms=newest)
        provider, _ = provider_with({"/api/v3/klines": venue})
        recorder = RecordingLog()
        monkeypatch.setattr(binance_module, "log", recorder)

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=12_500)

        assert len(candles) == MAX_KLINE_PAGES * MAX_KLINES_PER_REQUEST
        capped = recorder.fields("binance.page_cap_reached")
        assert len(capped) == 1
        assert capped[0]["wanted"] == 12_500
        assert capped[0]["bars"] == MAX_KLINE_PAGES * MAX_KLINES_PER_REQUEST
        assert capped[0]["cap"] == MAX_KLINE_PAGES

    async def test_a_venue_that_simply_runs_out_does_not_report_the_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half, and the reason the flag exists.  A warning that fires
        whenever a walk comes back short would say "ARUNA stopped early" every
        time the venue's history ends - which is the same lie in the other
        direction."""
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=1_200, newest_open_ms=newest)
        provider, _ = provider_with({"/api/v3/klines": venue})
        recorder = RecordingLog()
        monkeypatch.setattr(binance_module, "log", recorder)

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=5_000)

        assert len(candles) == 1_200
        assert "binance.page_cap_reached" not in recorder.names()

    async def test_bars_outside_an_explicit_window_are_dropped(self) -> None:
        newest = aligned_minute(now_utc() - timedelta(minutes=1))
        venue = FakeKlineVenue(bars=200, newest_open_ms=newest)
        end = datetime.fromtimestamp((newest - 100 * MINUTE_MS) / 1000, tz=UTC)
        provider, _ = provider_with({"/api/v3/klines": venue})

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=50, end=end)

        assert candles
        assert all(c.open_time <= end for c in candles)


# ---------------------------------------------------------------------------
# Quotes, snapshots, order book
# ---------------------------------------------------------------------------


class TestQuoteAndSnapshot:
    async def test_a_quote_carries_both_sides_of_the_book(self) -> None:
        provider, rest = provider_with({"/api/v3/ticker/24hr": lambda p: TICKER_PAYLOAD})

        quote = await provider.fetch_quote("BTC/USDT")

        assert quote.price == Decimal("118234.56000000")
        assert quote.bid == Decimal("118234.55000000")
        assert quote.ask == Decimal("118234.56000000")
        assert quote.bid_quantity == Decimal("3.41000000")
        assert quote.provenance.source == SOURCE
        assert rest.calls == [("/api/v3/ticker/24hr", {"symbol": "BTCUSDT"})]

    async def test_a_snapshot_reports_the_24h_window_it_was_given(self) -> None:
        provider, _ = provider_with({"/api/v3/ticker/24hr": lambda p: TICKER_PAYLOAD})

        snapshot = await provider.fetch_snapshot("BTC/USDT")

        assert snapshot.last_price == Decimal("118234.56000000")
        assert snapshot.high_24h == Decimal("119400.00000000")
        assert snapshot.low_24h == Decimal("117050.02000000")
        assert snapshot.volume_24h == Decimal("12345.67000000")
        assert snapshot.change_24h_pct == Decimal("-0.472000")
        assert snapshot.provenance.source == SOURCE
        assert snapshot.raw is not None
        assert snapshot.raw["quoteVolume"] == "1456789012.34000000"

    async def test_the_spread_is_computed_in_basis_points(self) -> None:
        """USDT spreads on a major pair are a fraction of a basis point.  An
        absolute spread would be unreadable across assets priced 0.5 and
        118000."""
        provider, _ = provider_with({"/api/v3/ticker/24hr": lambda p: TICKER_PAYLOAD})

        snapshot = await provider.fetch_snapshot("BTC/USDT")

        assert snapshot.spread_bps is not None
        assert 0 < snapshot.spread_bps < Decimal("0.01")

    async def test_crypto_has_no_closing_bell(self) -> None:
        provider, _ = provider_with({"/api/v3/ticker/24hr": lambda p: TICKER_PAYLOAD})

        snapshot = await provider.fetch_snapshot("BTC/USDT")

        assert snapshot.market_open is None
        assert snapshot.session

    async def test_a_ticker_without_a_price_is_an_outage(self) -> None:
        provider, _ = provider_with(
            {"/api/v3/ticker/24hr": lambda p: {"code": -1121, "msg": "Invalid symbol."}}
        )

        with pytest.raises(DataSourceUnavailableError, match="tidak berisi harga"):
            await provider.fetch_snapshot("BTC/USDT")


class TestStrictParsing:
    """"Strict" has to mean strict, or the label is the whole bug."""

    @pytest.mark.parametrize(
        "value", ["NaN", "nan", "-NaN", "Infinity", "-Infinity", "inf", "sNaN"]
    )
    def test_a_number_that_is_not_finite_is_not_a_number(self, value: str) -> None:
        """``Decimal("NaN")`` and ``Decimal("Infinity")`` are legal
        constructions that raise nothing, so catching ``InvalidOperation`` alone
        let both through a parser that called itself strict."""
        assert binance_module._optional_decimal(value) is None

    @pytest.mark.parametrize("value", ["0", "-0", "1e400", "63690.92000000"])
    def test_an_ordinary_number_still_parses(self, value: str) -> None:
        """The guard rejects non-finite values, not unusual ones.  A price of
        zero is wrong, but it is quality.py's job to say so - dropping it here
        would hide it."""
        assert binance_module._optional_decimal(value) == Decimal(value)

    async def test_a_nan_price_never_reaches_a_candle(self) -> None:
        """Where it used to land instead: past ``None in prices``, into a
        ``Candle``, and out through ``quality._check_price_sane`` as
        ``decimal.InvalidOperation`` - which the per-asset handler in the ingest
        loop does not catch, so one poisoned field ended the poll for every
        other asset."""
        open_ms = aligned_minute(now_utc() - timedelta(minutes=10))
        provider, _ = provider_with(
            {"/api/v3/klines": lambda p: [kline(open_ms, close="NaN")]}
        )

        with pytest.raises(DataSourceUnavailableError, match="pemeriksaan bentuk"):
            await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)

    async def test_a_nan_last_price_is_an_outage_not_a_quote(self) -> None:
        payload = dict(TICKER_PAYLOAD, lastPrice="Infinity")
        provider, _ = provider_with({"/api/v3/ticker/24hr": lambda p: payload})

        with pytest.raises(DataSourceUnavailableError, match="last price"):
            await provider.fetch_quote("BTC/USDT")


class TestOrderBook:
    BOOK: ClassVar[dict[str, Any]] = {
        "lastUpdateId": 77_123_456,
        "bids": [["118234.55", "3.41"], ["118234.40", "0.75"], ["118234.10", "9.20"]],
        "asks": [["118234.56", "1.20"], ["118234.90", "2.05"], ["118235.10", "0.40"]],
    }

    async def test_depth_is_parsed_best_first(self) -> None:
        provider, _ = provider_with({"/api/v3/depth": lambda p: self.BOOK})

        book = await provider.fetch_order_book("BTC/USDT", depth=3)

        assert book is not None
        assert book.best_bid == Decimal("118234.55")
        assert book.best_ask == Decimal("118234.56")
        assert book.is_crossed is False
        assert book.bid_depth == Decimal("13.36")

    async def test_an_illegal_depth_is_rounded_up_to_a_legal_one(self) -> None:
        """``/api/v3/depth`` answers 400 for a size outside its documented set,
        so 25 has to become 50 on the wire and be trimmed here."""
        provider, rest = provider_with({"/api/v3/depth": lambda p: self.BOOK})

        book = await provider.fetch_order_book("BTC/USDT", depth=25)

        assert rest.calls[0][1]["limit"] == 50
        assert rest.calls[0][1]["limit"] in DEPTH_LIMITS
        assert book is not None

    async def test_the_ingestors_depth_of_twenty_is_already_legal(self) -> None:
        provider, rest = provider_with({"/api/v3/depth": lambda p: self.BOOK})

        await provider.fetch_order_book("BTC/USDT", depth=20)

        assert rest.calls[0][1]["limit"] == 20

    async def test_a_level_that_cannot_be_read_is_dropped_not_zeroed(self) -> None:
        payload = {"bids": [["118234.55", "3.41"], ["oops"], []], "asks": []}
        provider, _ = provider_with({"/api/v3/depth": lambda p: payload})

        book = await provider.fetch_order_book("BTC/USDT", depth=20)

        assert book is not None
        assert len(book.bids) == 1
        assert book.asks == ()


class TestMetadata:
    INFO: ClassVar[dict[str, Any]] = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            }
        ]
    }

    async def test_tick_size_and_precision_come_from_the_venue(self) -> None:
        """``assets.price_precision`` has been NULL since the schema was
        written.  Reading it from exchangeInfo is the difference between a
        measured figure and one inferred from whatever price was on screen."""
        provider, _ = provider_with({"/api/v3/exchangeInfo": lambda p: self.INFO})

        meta = await provider.fetch_metadata("BTC/USDT")

        assert meta.tick_size == Decimal("0.01000000")
        assert meta.price_precision == 2
        assert meta.step_size == Decimal("0.00001000")
        assert meta.min_notional == Decimal("5.00000000")
        assert meta.base_asset == "BTC"
        assert meta.quote_asset == "USDT"

    async def test_an_unlisted_pair_is_absent_not_guessed(self) -> None:
        provider, _ = provider_with({"/api/v3/exchangeInfo": lambda p: {"symbols": []}})

        with pytest.raises(DataSourceUnavailableError, match="melisting"):
            await provider.fetch_metadata("BTC/USDT")


class TestStatus:
    async def test_reachable_reports_measured_clock_skew(self) -> None:
        """``future_tolerance_sec`` exists to absorb skew.  Sized against a
        different venue's clock it is a number with no measurement behind it,
        so the real figure has to be readable (SPEC 49)."""
        server_ms = int(now_utc().timestamp() * 1000) + 400
        provider, rest = provider_with({"/api/v3/time": lambda p: {"serverTime": server_ms}})

        status = await provider.status()

        assert status.reachable is True
        assert status.server_time is not None
        assert "skew" in status.detail
        assert rest.paths() == ["/api/v3/time"]

    async def test_a_payload_without_a_clock_is_not_reachable(self) -> None:
        provider, _ = provider_with({"/api/v3/time": lambda p: {}})

        status = await provider.status()

        assert status.reachable is False
        assert "serverTime" in status.detail

    async def test_an_unreachable_venue_says_so_without_raising(self) -> None:
        class Dead(FakeRest):
            async def get(self, path, params=None):
                raise DataSourceUnavailableError("semua host dicoba")

        provider = BinanceSpotProvider(settings(), rest=Dead({}))  # type: ignore[arg-type]

        status = await provider.status()

        assert status.reachable is False
        assert status.latency_ms is None


class TestVenueClock:
    """The measurement ``is_closed`` depends on, taken at the transport.

    ``status()`` measured this number from the first day and formatted it into
    a string; nothing read it back.  Now it is one reading, taken here, printed
    by ``status`` and used by ``fetch_candles``.
    """

    def clock_handler(self, offset_sec: float) -> Any:
        def handler(url: str, params: dict[str, Any]) -> httpx.Response:
            server_ms = int(
                (now_utc() + timedelta(seconds=offset_sec)).timestamp() * 1000
            )
            return httpx.Response(200, json={"serverTime": server_ms})

        return handler

    async def test_venue_now_follows_the_venue_not_this_machine(self) -> None:
        rest, _ = rest_with(self.clock_handler(45))

        venue = await rest.venue_now()

        assert venue is not None
        assert 40 < (venue - now_utc()).total_seconds() <= 45

    async def test_a_reading_is_reused_inside_its_window(self) -> None:
        """One weight per five minutes, not one per candle fetch."""
        rest, fetcher = rest_with(self.clock_handler(0))

        await rest.venue_now()
        await rest.venue_now()
        await rest.venue_now()

        assert len(fetcher.calls) == 1

    async def test_a_stale_reading_is_taken_again(self) -> None:
        """A clock offset does not expire by drifting, it expires by being
        stepped - so it is re-measured on a window rather than trusted for the
        life of the process."""
        rest, fetcher = rest_with(self.clock_handler(0))
        await rest.venue_now()
        assert rest.clock is not None
        rest._clock = replace(
            rest.clock,
            measured_at=now_utc() - timedelta(seconds=CLOCK_MAX_AGE_SEC + 1),
            # The age is judged on the monotonic anchor, not on `measured_at`.
            # Ageing it by the wall clock alone would leave the reading fresh -
            # which is the whole point: a stepped wall clock must not be able to
            # make a reading look old *or* young.
            measured_mono=rest.clock.measured_mono - (CLOCK_MAX_AGE_SEC + 1),
        )

        await rest.venue_now()

        assert len(fetcher.calls) == 2

    async def test_a_wall_clock_step_does_not_reach_venue_now(self) -> None:
        """The residue the first version left behind.

        An offset only corrects the skew that existed when it was taken, so a
        wall-clock step afterwards is inherited whole - and the age check could
        not notice, because it was reading the stepped clock too. Measured on
        the shipping stack: venue truth 12:00:30, machine stepped +30s, and a
        1m bar with 30 seconds still to run came back ``is_closed``.

        Nothing here touches the venue; the reading is already cached. Only
        this machine's wall clock moves.
        """
        rest, fetcher = rest_with(self.clock_handler(0))
        before = await rest.venue_now()
        assert before is not None

        stepped = now_utc() + timedelta(seconds=30)
        with mock.patch("aruna.data.crypto.binance.now_utc", return_value=stepped):
            after = await rest.venue_now()

        assert after is not None
        # The venue did not move, so neither may the answer - a step must not
        # buy 30 seconds of the future.
        assert abs((after - before).total_seconds()) < 1.0
        assert len(fetcher.calls) == 1, "a step must not force a re-measure either"

    async def test_a_clock_that_cannot_be_read_is_not_guessed(self) -> None:
        rest, _ = rest_with(lambda url, params: httpx.Response(503, text="down"))

        assert await rest.venue_now() is None
        assert rest.clock is None

    async def test_a_payload_without_a_clock_is_not_a_clock(self) -> None:
        rest, _ = rest_with(lambda url, params: httpx.Response(200, json={}))

        assert await rest.venue_now() is None

    async def test_status_and_is_closed_read_the_same_measurement(self) -> None:
        """The wiring that was missing: the figure ``aruna providers`` prints is
        the figure the candle mapper uses, taken once."""
        rest, fetcher = rest_with(self.clock_handler(-20))
        provider = BinanceSpotProvider(settings(), rest=rest)

        status = await provider.status()
        venue = await rest.venue_now()

        assert status.reachable is True
        assert rest.clock is not None
        assert venue is not None
        assert -21 < (venue - now_utc()).total_seconds() < -19
        assert len(fetcher.calls) == 1  # reused, not measured twice


# ---------------------------------------------------------------------------
# Transport: rate limits, refusals, host failover
# ---------------------------------------------------------------------------


def rest_with(handler: Any) -> tuple[BinanceSpotRest, FakeFetcher]:
    fetcher = FakeFetcher(handler)
    return BinanceSpotRest(fetcher=fetcher), fetcher  # type: ignore[arg-type]


class TestRateLimit:
    async def test_used_weight_is_read_off_every_response(self) -> None:
        """Without this the first sign of trouble is a 429, by which point the
        minute's budget is already spent."""
        rest, _ = rest_with(
            lambda url, params: httpx.Response(
                200, json={"serverTime": 1}, headers={"x-mbx-used-weight-1m": "137"}
            )
        )

        await rest.get("/api/v3/time")

        assert rest.used_weight_1m == 137
        assert rest.weight_seen_at is not None

    async def test_a_429_stops_the_next_call_from_reaching_the_venue(self) -> None:
        """The protection that matters is not the failed call, it is the
        forty after it: a poll walks five assets across ten intervals and would
        otherwise pile every one of them onto an active rate limit."""
        rest, fetcher = rest_with(
            lambda url, params: httpx.Response(
                429, headers={"Retry-After": "30"}, text='{"code":-1003}'
            )
        )

        with pytest.raises(BinanceRateLimited, match="429"):
            await rest.get("/api/v3/time")

        assert len(fetcher.calls) == 1
        with pytest.raises(BinanceRateLimited, match="menahan diri"):
            await rest.get("/api/v3/time")
        assert len(fetcher.calls) == 1  # nothing new went out

    async def test_the_cooldown_uses_the_venues_own_retry_after(self) -> None:
        rest, _ = rest_with(
            lambda url, params: httpx.Response(429, headers={"Retry-After": "45"})
        )

        with pytest.raises(BinanceRateLimited):
            await rest.get("/api/v3/time")

        assert rest.cooldown_until is not None
        remaining = (rest.cooldown_until - now_utc()).total_seconds()
        assert 40 < remaining <= 45

    async def test_an_ip_ban_is_never_retried(self) -> None:
        """418 is the ban Binance issues for repeatedly ignoring 429.  Retrying
        through it is how two minutes becomes three days."""
        rest, fetcher = rest_with(
            lambda url, params: httpx.Response(418, headers={"Retry-After": "120"})
        )

        with pytest.raises(BinanceRateLimited, match="418"):
            await rest.get("/api/v3/time")

        assert len(fetcher.calls) == 1
        assert rest.cooldown_until is not None

    async def test_a_ban_without_a_stated_wait_still_cools_down(self) -> None:
        rest, _ = rest_with(lambda url, params: httpx.Response(418))

        with pytest.raises(BinanceRateLimited):
            await rest.get("/api/v3/time")

        assert rest.cooldown_until is not None

    async def test_the_transport_opts_429_out_of_the_shared_retry_set(self) -> None:
        """Stated at the call site, derived from the shared set so the two
        cannot drift: ``RETRY_STATUSES - {429}``."""
        rest, fetcher = rest_with(
            lambda url, params: httpx.Response(200, json={"serverTime": 1})
        )

        await rest.get("/api/v3/time")

        statuses = fetcher.retry_statuses[0]
        assert statuses is not None
        assert 429 not in statuses
        assert 503 in statuses

    async def test_the_cooldown_lifts_by_itself(self) -> None:
        state = {"fail": True}

        def handler(url: str, params: dict[str, Any]) -> httpx.Response:
            if state["fail"]:
                return httpx.Response(429, headers={"Retry-After": "1"})
            return httpx.Response(200, json={"serverTime": 42})

        rest, _ = rest_with(handler)
        with pytest.raises(BinanceRateLimited):
            await rest.get("/api/v3/time")

        state["fail"] = False
        rest._cooldown_until = now_utc() - timedelta(seconds=1)
        payload, _ = await rest.get("/api/v3/time")

        assert payload == {"serverTime": 42}


class TestRefusals:
    async def test_a_blocked_jurisdiction_is_named_as_one(self) -> None:
        """451 and "the venue is down" call for completely different actions,
        and reporting the second for the first names a cause nobody measured
        (SPEC 49)."""
        rest, _ = rest_with(lambda url, params: httpx.Response(451, text="unavailable"))

        with pytest.raises(BinanceBlocked, match="451"):
            await rest.get("/api/v3/time")

    async def test_a_jurisdiction_block_is_not_retried_on_every_host(self) -> None:
        """All three hosts answer a 451 identically; asking each of them costs
        three times the weight to learn the same thing."""
        rest, fetcher = rest_with(lambda url, params: httpx.Response(451))

        with pytest.raises(BinanceBlocked):
            await rest.get("/api/v3/time")

        assert len(fetcher.calls) == 1

    async def test_an_edge_refusal_is_distinguished_from_the_api(self) -> None:
        rest, _ = rest_with(lambda url, params: httpx.Response(403, text="<html>waf"))

        with pytest.raises(BinanceBlocked, match="403"):
            await rest.get("/api/v3/time")

    async def test_the_venues_own_error_code_is_surfaced(self) -> None:
        """-1121 is "no such symbol" and -1120 is "no such interval"; both read
        very differently from an outage."""
        rest, _ = rest_with(
            lambda url, params: httpx.Response(
                400, json={"code": -1121, "msg": "Invalid symbol."}
            )
        )

        with pytest.raises(DataSourceUnavailableError) as exc:
            await rest.get("/api/v3/klines")

        # The message, and the code alongside it.  Asserting only on the text
        # passes even when the body is dumped raw, because the raw body happens
        # to contain the same words.
        assert "Invalid symbol" in str(exc.value)
        assert "code -1121" in str(exc.value)

    async def test_a_bad_request_is_not_retried_on_every_host(self) -> None:
        rest, fetcher = rest_with(
            lambda url, params: httpx.Response(400, json={"code": -1121, "msg": "no"})
        )

        with pytest.raises(DataSourceUnavailableError):
            await rest.get("/api/v3/klines")

        assert len(fetcher.calls) == 1

    async def test_non_json_from_a_200_tries_the_alternates_before_giving_up(
        self,
    ) -> None:
        """It used to be terminal.  A 200 carrying HTML is the signature of an
        interception, and an interception is per host: reporting the venue as
        unavailable after one host left two working alternates untried, in
        exactly the scenario ``BinanceBlocked`` was written for."""
        rest, fetcher = rest_with(lambda url, params: httpx.Response(200, text="<html>"))

        with pytest.raises(BinanceBlocked, match="bukan API"):
            await rest.get("/api/v3/time")

        assert len(fetcher.calls) == len(HOSTS)


class TestTheShippingStack:
    """Request counts, retries and redirects - measured through ``HttpFetcher``.

    Everything in this class is a property of the real client.  On
    :class:`FakeFetcher` these tests would all pass while the shipping code did
    the opposite, which is precisely what happened: the 429 assertion below read
    ``== 1`` for a whole phase while production sent four.
    """

    BLOCK_PAGE = (
        "<html><head><title>Internet Positif</title></head>"
        "<body>Situs ini diblokir</body></html>"
    )

    async def test_a_429_costs_exactly_one_request(self) -> None:
        """PASAL 5, rate limit.  Retried, a 429 spends the budget it is
        complaining about: with ``Retry-After: 30`` one ``fetch_candles`` slept
        90 seconds and fired four requests into an active limit, and the
        cooldown branch that was supposed to prevent that only ran after the
        last of them.

        **One host and the full retry budget, on purpose.**  With the default
        three hosts the rotation already caps the first host at one attempt, so
        this test would stay green with the 429 opt-out ripped out - passing for
        a reason that has nothing to do with what it claims to check.  A single
        host removes that cover: here the only thing between a 429 and three
        more requests is ``RETRY_STATUSES - {429}``.
        """
        rest, sent = live_rest(
            lambda request: httpx.Response(
                429, headers={"Retry-After": "2"}, json={"code": -1003}
            ),
            hosts=(HOSTS[0],),
            max_retries=3,
        )

        with pytest.raises(BinanceRateLimited, match="429"):
            await rest.get("/api/v3/klines", {"symbol": "BTCUSDT"})
        await rest.close()

        assert len(sent) == 1
        assert rest.cooldown_until is not None

    async def test_a_418_costs_exactly_one_request(self) -> None:
        """Also with the budget available, for the same reason: 418 has never
        been in the retry set, and this is what would notice if it were added."""
        rest, sent = live_rest(
            lambda request: httpx.Response(418, headers={"Retry-After": "120"}),
            hosts=(HOSTS[0],),
            max_retries=3,
        )

        with pytest.raises(BinanceRateLimited, match="418"):
            await rest.get("/api/v3/time")
        await rest.close()

        assert len(sent) == 1

    async def test_a_5xx_spends_its_retries_on_the_last_host_only(self) -> None:
        """Measured before the fix: 3 hosts x 4 attempts = 12 requests and 15.4
        seconds for one call, against a 5-second poll interval.  The rotation is
        the retry; the budget is kept for the host after which there is nowhere
        left to go."""
        rest, sent = live_rest(
            lambda request: httpx.Response(503, text="down"), max_retries=1
        )

        with pytest.raises(DataSourceUnavailableError):
            await rest.get("/api/v3/time")
        await rest.close()

        assert len(sent) == (len(HOSTS) - 1) + 2
        assert {str(r.url).split("/api/")[0] for r in sent} == set(HOSTS)

    async def test_a_transient_5xx_is_still_retried_where_it_matters(self) -> None:
        """Narrowing the retry policy must not become deleting it: on the last
        host a 503 is worth one more ask, and this is what would go red if the
        budget were dropped everywhere."""
        state = {"fail": 1}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                state["fail"] -= 1
                return httpx.Response(503, text="down")
            return httpx.Response(200, json={"serverTime": 3})

        rest, sent = live_rest(handler, hosts=(HOSTS[0],), max_retries=2)

        payload, _ = await rest.get("/api/v3/time")
        await rest.close()

        assert payload == {"serverTime": 3}
        assert len(sent) == 2

    async def test_a_redirect_is_a_signal_not_a_route(self) -> None:
        """PASAL 41.  The allowlist can only check the URL this adapter builds,
        so a followed 302 lands on a path it never saw - ``GET /api/v3/order``
        really was sent, and its body really was read back.  The venue's REST
        API does not redirect; something between us and it does."""

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(HOSTS[0]):
                return httpx.Response(
                    302,
                    headers={"Location": f"{HOSTS[2]}/api/v3/order?symbol=BTCUSDT"},
                )
            return httpx.Response(200, json={"serverTime": 4})

        rest, sent = live_rest(handler)

        payload, _ = await rest.get("/api/v3/time")
        await rest.close()

        assert payload == {"serverTime": 4}
        assert [request.url.path for request in sent] == ["/api/v3/time"] * 2
        assert not any("order" in str(request.url) for request in sent)

    async def test_a_block_page_on_one_host_is_not_the_venue_being_down(self) -> None:
        """The whole reason the alternates are configured.  One hijacked
        resolver does not hijack all three hosts."""

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(HOSTS[0]):
                return httpx.Response(
                    200, text=self.BLOCK_PAGE, headers={"content-type": "text/html"}
                )
            return httpx.Response(200, json={"serverTime": 8})

        rest, sent = live_rest(handler)

        payload, _ = await rest.get("/api/v3/time")
        await rest.close()

        assert payload == {"serverTime": 8}
        assert len(sent) == 2

    async def test_every_host_blocked_is_named_a_block_with_the_body_as_proof(
        self,
    ) -> None:
        """"Blocked" and "down" send an operator to different places, so the
        cause is only named when every host said the same thing - and the body
        travels with it as the measurement behind the claim (SPEC 49)."""
        rest, sent = live_rest(lambda request: httpx.Response(200, text=self.BLOCK_PAGE))

        with pytest.raises(BinanceBlocked) as exc:
            await rest.get("/api/v3/time")
        await rest.close()

        assert len(sent) == len(HOSTS)
        assert "diblokir" in str(exc.value)
        # Still a DataSourceUnavailableError, so every existing handler works.
        assert isinstance(exc.value, DataSourceUnavailableError)

    async def test_candles_survive_a_hijacked_primary_host(self) -> None:
        """End to end: block page on the primary, real data on the alternate,
        and the bar comes back settled against the venue's own clock."""
        open_ms = aligned_minute(now_utc() - timedelta(minutes=5))

        def handler(request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(HOSTS[0]):
                return httpx.Response(200, text=self.BLOCK_PAGE)
            if request.url.path == "/api/v3/time":
                return httpx.Response(
                    200, json={"serverTime": int(now_utc().timestamp() * 1000)}
                )
            return httpx.Response(200, json=[kline(open_ms)])

        provider, _ = live_provider(handler)

        candles = await provider.fetch_candles("BTC/USDT", Horizon.M1, limit=1)
        await provider.close()

        assert len(candles) == 1
        assert candles[0].is_closed is True

    async def test_an_unlisted_path_never_reaches_the_socket(self) -> None:
        """The allowlist, checked one layer lower than the pre-flight test:
        nothing at all was written to the wire."""
        rest, sent = live_rest(lambda request: httpx.Response(200, json={}))

        with pytest.raises(ForbiddenEndpoint):
            await rest.get("/api/v3/order", {"symbol": "BTCUSDT"})
        await rest.close()

        assert sent == []


class TestHostFailover:
    def handler_failing(self, *dead: str) -> Any:
        def handler(url: str, params: dict[str, Any]) -> Any:
            if any(url.startswith(host) for host in dead):
                return DataSourceUnavailableError(
                    "binance-spot unreachable after 4 attempt(s): ConnectError"
                )
            return httpx.Response(200, json={"serverTime": 7})

        return handler

    async def test_a_dead_primary_host_moves_to_the_alternate(self) -> None:
        rest, fetcher = rest_with(self.handler_failing(HOSTS[0]))

        payload, _ = await rest.get("/api/v3/time")

        assert payload == {"serverTime": 7}
        assert fetcher.calls[0].startswith(HOSTS[0])
        assert fetcher.calls[1].startswith(HOSTS[1])

    async def test_the_working_host_is_kept(self) -> None:
        """Otherwise the dead host is paid for on every single call."""
        rest, fetcher = rest_with(self.handler_failing(HOSTS[0]))

        await rest.get("/api/v3/time")
        await rest.get("/api/v3/time")

        assert rest.host == HOSTS[1]
        assert len(fetcher.calls) == 3

    async def test_every_host_dead_is_reported_with_all_of_them_named(self) -> None:
        rest, fetcher = rest_with(self.handler_failing(*HOSTS))

        with pytest.raises(DataSourceUnavailableError) as exc:
            await rest.get("/api/v3/time")

        assert len(fetcher.calls) == len(HOSTS)
        for host in HOSTS:
            assert host in str(exc.value)

    async def test_a_server_error_counts_as_a_host_fault(self) -> None:
        def handler(url: str, params: dict[str, Any]) -> httpx.Response:
            if url.startswith(HOSTS[0]):
                return httpx.Response(503, text="down")
            return httpx.Response(200, json={"serverTime": 9})

        rest, fetcher = rest_with(handler)

        payload, _ = await rest.get("/api/v3/time")

        assert payload == {"serverTime": 9}
        assert len(fetcher.calls) == 2

    async def test_a_404_is_a_host_fault_not_a_missing_resource(self) -> None:
        """Found live, not by imagining it.  A Binance subdomain that does not
        exist still resolves and answers with an nginx 404 page, so treating
        404 as terminal meant a retired or mistyped primary host took crypto
        down while two working alternates went untried.  Every path this
        adapter can send is on the allowlist, so a 404 can only mean this host
        is not the API."""

        def handler(url: str, params: dict[str, Any]) -> httpx.Response:
            if url.startswith(HOSTS[0]):
                return httpx.Response(404, text="<html>404 Not Found</html>")
            return httpx.Response(200, json={"serverTime": 11})

        rest, fetcher = rest_with(handler)

        payload, _ = await rest.get("/api/v3/time")

        assert payload == {"serverTime": 11}
        assert len(fetcher.calls) == 2
        assert rest.host == HOSTS[1]

    async def test_the_alternates_are_the_documented_ones(self) -> None:
        assert HOSTS[0] == "https://api.binance.com"
        assert set(HOSTS) == {
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
        }


class TestWiring:
    """The paths the live system actually walks."""

    async def test_open_and_close_reach_the_transport(self) -> None:
        provider, rest = provider_with({})

        await provider.open()
        await provider.close()

        assert (rest.opened, rest.closed) == (1, 1)

    def test_every_interval_offered_is_one_binance_names(self) -> None:
        known = {
            "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
            "6h", "8h", "12h", "1d", "3d", "1w", "1M",
        }
        assert set(INTERVALS.values()) <= known

    def test_the_declared_intervals_match_the_mapping(self) -> None:
        caps = BinanceSpotProvider(settings()).capabilities
        assert set(caps.supported_intervals) == set(INTERVALS)
