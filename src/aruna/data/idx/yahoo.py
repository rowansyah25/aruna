"""IDX market data from Yahoo Finance (SPEC 3, 4, 5).

Uses Yahoo's chart endpoint directly rather than the ``yfinance`` package.
Same data source and same endpoint ``yfinance`` wraps, but async-native and
without pulling pandas/numpy into a runtime that needs neither.  If Yahoo ever
tightens access to this endpoint, swapping in ``yfinance`` is a change confined
to this file.

**This feed is not realtime and this adapter never claims otherwise.**

Two facts, both verified against the live API rather than assumed:

* Yahoo does *not* return ``exchangeDataDelayedBy`` for Jakarta symbols, so the
  real delay cannot be read from the response.  ARUNA applies the conventional
  15 minutes from ``ARUNA_DATA_IDX_DECLARED_DELAY_SEC`` and treats it as a
  floor - the true delay may be larger, never smaller.
* ``regularMarketTime`` is the last trade the feed knows about.  Outside
  trading hours that is the previous close, which is why every snapshot carries
  the session and ``market_open`` so nothing downstream mistakes it for a live
  price (SPEC 3).

Licensing: Yahoo's terms permit personal, non-commercial use.  ARUNA records
the source on every row so this is auditable, and it is the operator's call
whether that licence fits their use (SPEC 4, 47).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import Any

from aruna.core.clock import IDX_CALENDAR, now_utc
from aruna.core.config import DataSettings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.data.http import HttpFetcher
from aruna.data.models import Candle, Provenance, Quote, Snapshot, quantize_pct
from aruna.data.provider import (
    MarketDataProvider,
    ProviderCapabilities,
    ProviderStatus,
    Transport,
)

log = get_logger("aruna.data.idx.yahoo")

BASE_URL = "https://query1.finance.yahoo.com"
SOURCE = "yahoo"

#: ARUNA interval -> Yahoo ``interval``.  Verified against the live endpoint.
_INTERVALS: dict[Horizon, str] = {
    Horizon.M1: "1m",
    Horizon.M5: "5m",
    Horizon.M15: "15m",
    Horizon.M30: "30m",
    Horizon.H1: "1h",
    Horizon.D1: "1d",
    Horizon.W1: "1wk",
}

#: Yahoo caps how far back each intraday interval reaches.  This is the
#: ceiling, never the default request - see :func:`_chart_range`.
_MAX_RANGE: dict[Horizon, str] = {
    Horizon.M1: "7d",
    Horizon.M5: "60d",
    Horizon.M15: "60d",
    Horizon.M30: "60d",
    Horizon.H1: "730d",
    Horizon.D1: "10y",
    Horizon.W1: "10y",
}

#: Calendar days behind each ``_MAX_RANGE`` entry, so the ladder below can be
#: clamped to the cap without parsing the strings back.
_MAX_RANGE_DAYS: dict[Horizon, int] = {
    Horizon.M1: 7,
    Horizon.M5: 60,
    Horizon.M15: 60,
    Horizon.M30: 60,
    Horizon.H1: 730,
    Horizon.D1: 3653,
    Horizon.W1: 3653,
}

#: The ``range`` values Yahoo documents, smallest first, with the calendar span
#: each one covers.  The endpoint rejects ranges outside this set except for
#: the ``Nd`` forms already proven live in ``_MAX_RANGE``, so the chooser picks
#: from this ladder or falls back to the cap - it never invents a range string.
_RANGE_LADDER: tuple[tuple[str, int], ...] = (
    ("1d", 1),
    ("5d", 5),
    ("1mo", 30),
    ("3mo", 91),
    ("6mo", 182),
    ("1y", 365),
    ("2y", 730),
    ("5y", 1826),
    ("10y", 3653),
)

#: Bars one IDX trading day yields per interval, deliberately understated.
#: The regular market matches orders 09:00-16:00 WIB minus a lunch break, so
#: 15m really returns 21-28 bars a day; counting 20 makes the window slightly
#: *wider* than needed.  Erring that way costs a few rows once.  Erring the
#: other way hands the caller fewer bars than it asked for, which is how a
#: refresh silently stops covering the hole it was called to close.
_BARS_PER_TRADING_DAY: dict[Horizon, float] = {
    Horizon.M1: 300.0,
    Horizon.M5: 60.0,
    Horizon.M15: 20.0,
    Horizon.M30: 10.0,
    Horizon.H1: 5.0,
    Horizon.D1: 1.0,
    Horizon.W1: 0.2,
}

#: Calendar days per trading day: 7/5 for weekends plus roughly a tenth for
#: exchange holidays.  ARUNA holds no IDX holiday calendar yet (see
#: :class:`aruna.core.clock.IdxCalendar`), so this is a cushion, not a lookup -
#: which is also why :meth:`YahooIdxProvider.fetch_candles` widens once when a
#: narrow window comes back empty instead of trusting the arithmetic.
_CALENDAR_DAYS_PER_TRADING_DAY = 1.55


def _chart_range(
    interval: Horizon,
    limit: int,
    start: datetime | None,
    end: datetime | None,
    now: datetime,
) -> str:
    """Smallest Yahoo ``range`` that still covers what the caller asked for.

    Yahoo's chart endpoint has no bar count: ``range`` alone decides how much
    comes back.  This adapter used to ask for ``_MAX_RANGE`` no matter what
    ``limit`` said, so a routine four-bar refresh downloaded a 60-day window of
    15m bars or a two-year window of hourly bars - about 1200 and 3600 rows at
    Jakarta session hours - built a ``Candle`` for every row, and returned
    four.  The upkeep loop's premise, refresh a few bars and not five hundred,
    had no effect on IDX at all; this function is where it takes effect.
    ``tests/test_yahoo_range.py`` holds the measurement and the reduction.

    ``range`` is anchored at *now*, so the span must reach from the oldest
    moment the caller wants all the way back to now: an explicit ``start``
    widens it, and so does an ``end`` that lies in the past.  The result is
    clamped to ``_MAX_RANGE``, because Yahoo refuses intraday windows beyond
    its own cap - a first backfill of 500 15m bars still gets the full 60 days.
    """
    cap_days = _MAX_RANGE_DAYS[interval]
    trading_days = ceil(max(limit, 1) / _BARS_PER_TRADING_DAY[interval])
    # Clamped before it becomes a date: an absurd limit (500k weekly bars) would
    # otherwise overflow the subtraction below rather than land on the cap.
    horizon = timedelta(
        days=min(cap_days, ceil(trading_days * _CALENDAR_DAYS_PER_TRADING_DAY))
    )

    # An ``end`` past now buys nothing - Yahoo has no future bars - and letting
    # it push the window forward would shorten the span that actually matters.
    finish = min(end, now) if end else now
    earliest = start or (finish - horizon)
    span_days = max(1, ceil((now - earliest).total_seconds() / 86_400))

    wanted = min(span_days, cap_days)
    for name, days in _RANGE_LADDER:
        if days > cap_days:
            break
        if days >= wanted:
            # At the ceiling, name it the way ``_MAX_RANGE`` does ('730d', not
            # the equally long '2y'): fetch_candles decides whether a widened
            # retry is still available by comparing those strings.
            return _MAX_RANGE[interval] if days >= cap_days else name
    return _MAX_RANGE[interval]


class YahooIdxProvider(MarketDataProvider):
    def __init__(self, settings: DataSettings, *, base_url: str = BASE_URL) -> None:
        self._settings = settings
        self._delay_sec = settings.idx_declared_delay_sec
        self._http = HttpFetcher(
            source=SOURCE,
            base_url=base_url,
            timeout_sec=settings.request_timeout_sec,
            max_retries=settings.max_retries,
            # Yahoo rejects requests without a browser-like agent.
            headers={"User-Agent": "Mozilla/5.0 (compatible; ARUNA/0.1)"},
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=SOURCE,
            market=Market.IDX,
            transport=Transport.POLL,
            is_realtime=False,
            expected_delay_sec=self._delay_sec,
            supports_order_book=False,
            supported_intervals=tuple(_INTERVALS),
            max_candles_per_request=2_000,
            requires_credentials=False,
            regulatory_note=(
                "Yahoo Finance public chart endpoint. Unofficial; terms permit "
                "personal, non-commercial use. Not an IDX-licensed feed."
            ),
            limitations=(
                f"delayed by an assumed {self._delay_sec}s - Yahoo does not "
                "declare the real figure for Jakarta symbols",
                "no order book, no bid/ask depth",
                "1m history limited to the last 7 days",
                # SPEC 3's 3D/5D are trading-day prediction horizons, not bar
                # sizes. Calendar resampling of daily bars cannot express them
                # - weekends make the buckets uneven - so they are evaluated
                # over daily candles in PHASE 7 instead of faked here.
                "no 3d/5d intervals: those are trading-day horizons, not bars",
                "corporate actions may restate history without notice",
            ),
        )

    # ---- lifecycle ------------------------------------------------------

    async def open(self) -> None:
        await self._http.open()

    async def close(self) -> None:
        await self._http.close()

    async def status(self) -> ProviderStatus:
        try:
            payload, latency = await self._http.get_json(
                "/v8/finance/chart/BBCA.JK", params={"range": "1d", "interval": "1d"}
            )
        except DataSourceUnavailableError as exc:
            return ProviderStatus(reachable=False, detail=str(exc))

        meta = _meta(payload)
        if meta is None:
            return ProviderStatus(reachable=False, detail="chart response had no metadata")
        return ProviderStatus(
            reachable=True,
            latency_ms=latency,
            detail=f"{meta.get('fullExchangeName', 'Jakarta')} reachable",
            server_time=_epoch_to_utc(meta.get("regularMarketTime")),
        )

    # ---- symbols --------------------------------------------------------

    def provider_symbol(self, symbol: str) -> str:
        """``BBCA`` -> ``BBCA.JK``."""
        ticker = symbol.strip().upper()
        return ticker if ticker.endswith(".JK") else f"{ticker}.JK"

    # ---- data -----------------------------------------------------------

    async def _chart(
        self, symbol: str, *, interval: str, range_: str
    ) -> tuple[dict[str, Any], float]:
        payload, latency = await self._http.get_json(
            f"/v8/finance/chart/{self.provider_symbol(symbol)}",
            params={"range": range_, "interval": interval, "includePrePost": "false"},
        )
        chart = payload.get("chart") if isinstance(payload, dict) else None
        if not isinstance(chart, dict):
            raise DataSourceUnavailableError(f"yahoo returned no chart for {symbol}")
        if chart.get("error"):
            raise DataSourceUnavailableError(
                f"yahoo error for {symbol}: {chart['error'].get('description', chart['error'])}"
            )
        results = chart.get("result")
        if not results:
            raise DataSourceUnavailableError(f"yahoo returned no data for {symbol}")
        return results[0], latency

    async def fetch_quote(self, symbol: str) -> Quote:
        result, latency = await self._chart(symbol, interval="1d", range_="1d")
        meta = result.get("meta") or {}

        price = _optional_decimal(meta.get("regularMarketPrice"))
        if price is None:
            raise DataSourceUnavailableError(
                f"yahoo returned no market price for {symbol}"
            )

        return Quote(
            market=Market.IDX,
            symbol=symbol,
            price=price,
            provenance=Provenance(
                source=SOURCE,
                server_timestamp=now_utc(),
                provider_timestamp=_epoch_to_utc(meta.get("regularMarketTime")),
                latency_ms=latency,
                declared_delay_sec=self._delay_sec,
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
        yahoo_interval = _INTERVALS.get(interval)
        if yahoo_interval is None:
            raise ValueError(
                f"yahoo does not provide {interval.value} candles for IDX; "
                f"available: {', '.join(h.value for h in _INTERVALS)}"
            )

        widest = _MAX_RANGE[interval]
        chosen = _chart_range(interval, limit, start, end, now_utc())
        # A narrow window can land on nothing through no fault of the symbol:
        # ask for five days on the Monday after a week-long exchange holiday
        # and Yahoo has no trading day to hand back.  Empty would reach the
        # ingestor as "provider returned none" - a failure that is ours, not
        # the venue's - so widen once to the cap before believing it.
        attempts = (chosen,) if chosen == widest else (chosen, widest)

        candles: list[Candle] = []
        for range_ in attempts:
            result, latency = await self._chart(
                symbol, interval=yahoo_interval, range_=range_
            )
            candles = self._candles_from(
                result, symbol, interval, latency, start=start, end=end
            )
            if candles:
                break
            if range_ != widest:
                log.info(
                    "yahoo.range_widened",
                    symbol=symbol,
                    interval=interval.value,
                    tried=range_,
                    retry=widest,
                )

        return candles[-limit:] if limit and len(candles) > limit else candles

    def _candles_from(
        self,
        result: dict[str, Any],
        symbol: str,
        interval: Horizon,
        latency: float,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """One chart response into candles, oldest first and untruncated.

        Split out of :meth:`fetch_candles` so the same parse serves both the
        narrow request and the widened retry.
        """
        received = now_utc()
        meta = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        quote_block = ((result.get("indicators") or {}).get("quote") or [{}])[0]

        opens = quote_block.get("open") or []
        highs = quote_block.get("high") or []
        lows = quote_block.get("low") or []
        closes = quote_block.get("close") or []
        volumes = quote_block.get("volume") or []

        candles: list[Candle] = []
        for index, epoch in enumerate(timestamps):
            open_time = _epoch_to_utc(epoch)
            if open_time is None:
                continue
            if start and open_time < start:
                continue
            if end and open_time > end:
                continue

            values = [
                _optional_decimal(_at(opens, index)),
                _optional_decimal(_at(highs, index)),
                _optional_decimal(_at(lows, index)),
                _optional_decimal(_at(closes, index)),
            ]
            # Yahoo emits nulls for non-trading slots. A hole is reported by
            # absence, never filled in (SPEC 4).
            if any(v is None for v in values):
                continue

            open_, high, low, close = values  # type: ignore[misc]
            close_time = open_time + interval.duration
            candles.append(
                Candle(
                    market=Market.IDX,
                    symbol=symbol,
                    interval=interval,
                    open_time=open_time,
                    close_time=close_time,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=_optional_decimal(_at(volumes, index)) or Decimal(0),
                    is_closed=close_time <= received,
                    provenance=Provenance(
                        source=SOURCE,
                        server_timestamp=received,
                        provider_timestamp=open_time,
                        latency_ms=latency,
                        declared_delay_sec=self._delay_sec,
                    ),
                )
            )

        candles.sort(key=lambda c: c.open_time)
        if meta.get("currency") not in (None, "IDR"):
            raise DataSourceUnavailableError(
                f"yahoo returned {symbol} priced in {meta.get('currency')}, expected IDR"
            )
        return candles

    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        result, latency = await self._chart(symbol, interval="1d", range_="5d")
        received = now_utc()
        meta = result.get("meta") or {}

        last = _optional_decimal(meta.get("regularMarketPrice"))
        if last is None:
            raise DataSourceUnavailableError(f"yahoo returned no price for {symbol}")

        previous_close = _optional_decimal(meta.get("previousClose")) or _optional_decimal(
            meta.get("chartPreviousClose")
        )
        change_pct: Decimal | None = None
        if previous_close and previous_close > 0:
            change_pct = quantize_pct((last - previous_close) / previous_close * Decimal(100))

        # SPEC 3: the exchange calendar decides the session label, not the feed.
        market_open = IDX_CALENDAR.is_open(received)
        session = IDX_CALENDAR.session(received)

        return Snapshot(
            market=Market.IDX,
            symbol=symbol,
            captured_at=received,
            last_price=last,
            high_24h=_optional_decimal(meta.get("regularMarketDayHigh")),
            low_24h=_optional_decimal(meta.get("regularMarketDayLow")),
            volume_24h=_optional_decimal(meta.get("regularMarketVolume")),
            change_24h_pct=change_pct,
            session=session.value,
            market_open=market_open,
            provenance=Provenance(
                source=SOURCE,
                server_timestamp=received,
                provider_timestamp=_epoch_to_utc(meta.get("regularMarketTime")),
                latency_ms=latency,
                declared_delay_sec=self._delay_sec,
            ),
            raw={
                "exchange": meta.get("fullExchangeName"),
                "currency": meta.get("currency"),
                "regularMarketTime": meta.get("regularMarketTime"),
                "previousClose": str(previous_close) if previous_close else None,
            },
        )


# ---------------------------------------------------------------------------


def _meta(payload: Any) -> dict[str, Any] | None:
    chart = payload.get("chart") if isinstance(payload, dict) else None
    if not isinstance(chart, dict) or not chart.get("result"):
        return None
    return chart["result"][0].get("meta")


def _at(values: list[Any], index: int) -> Any:
    return values[index] if index < len(values) else None


def _optional_decimal(value: Any) -> Decimal | None:
    """Parse a Yahoo number, refusing the ones that are not finite.

    ``Decimal("NaN")`` and ``Decimal("Infinity")`` are legal constructions -
    ``Decimal(str(value))`` builds them without raising - so the ``except``
    below never sees them and every ``is None`` guard downstream answers False.
    They then reach code that cannot survive them, and not only at the quality
    gate: :meth:`YahooIdxProvider.fetch_snapshot` asks ``previous_close > 0``,
    and a Decimal NaN *signals* ``InvalidOperation`` on that comparison instead
    of quietly answering False the way a float NaN does, while an Infinity
    passes it and raises one line later inside
    ``(last - previous_close) / previous_close``.  The ingest loop's per-asset
    handler catches only ``DataSourceUnavailableError`` and ``ArunaError``, so
    either one ends the whole poll pass rather than the one symbol that carried
    the bad field.

    Returning None routes them into the refusals this module already has: a
    missing price raises ``DataSourceUnavailableError`` for that symbol, and a
    missing OHLC value drops that bar as a hole to be reported, never filled
    (SPEC 4).  It also repairs the ``previousClose or chartPreviousClose``
    fallback above, which a NaN used to win outright by being truthy.

    This is the same hole that was found and closed in the Binance adapter's
    ``_optional_decimal``; the code here was identical.  Remove the
    ``is_finite`` check and the crash comes back.
    """
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _epoch_to_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    return datetime.fromtimestamp(number, tz=UTC)


__all__ = ["BASE_URL", "SOURCE", "YahooIdxProvider"]
