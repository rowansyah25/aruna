"""Yahoo fundamentals via ``yfinance`` (SPEC 7).

Yahoo's ``quoteSummary`` endpoint now answers ``401 Invalid Crumb`` to plain
HTTP clients. ``yfinance`` performs the cookie/crumb handshake Yahoo's own site
uses, so it is used here rather than reimplementing that flow - which would
amount to working around an access control we were not granted.

That is the swap the PHASE 2 report anticipated. It costs pandas and numpy as
transitive dependencies, which is a real price and is stated in the PHASE 4
report rather than glossed over. Candles still come from the plain chart
endpoint; only fundamentals need this path.

``yfinance`` is synchronous and does network I/O, so every call is pushed to a
worker thread - blocking the event loop would stall ingestion and the health
monitor alongside it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aruna.core.clock import now_utc
from aruna.core.errors import DataSourceUnavailableError
from aruna.core.logging import get_logger
from aruna.fundamental.models import METRIC_FIELDS, Fundamentals

log = get_logger("aruna.fundamental.yahoo")

SOURCE = "yahoo"

#: Yahoo key -> (our field, multiplier). Ratios arrive as fractions where we
#: store percentages.
_MAPPING: dict[str, tuple[str, float]] = {
    "revenueGrowth": ("revenue_growth_pct", 100.0),
    "earningsGrowth": ("earnings_growth_pct", 100.0),
    "trailingEps": ("eps", 1.0),
    "returnOnEquity": ("roe_pct", 100.0),
    "returnOnAssets": ("roa_pct", 100.0),
    "debtToEquity": ("debt_to_equity", 1.0),
    "freeCashflow": ("free_cash_flow", 1.0),
    "totalDebt": ("total_debt", 1.0),
    "trailingPE": ("price_to_earnings", 1.0),
    "priceToBook": ("price_to_book", 1.0),
    "bookValue": ("book_value_per_share", 1.0),
    "profitMargins": ("profit_margin_pct", 100.0),
    "marketCap": ("market_cap", 1.0),
}


class YahooFundamentalProvider:
    """Fundamentals for IDX tickers."""

    name = SOURCE

    def __init__(self, *, timeout_sec: float = 30.0) -> None:
        self._timeout = timeout_sec

    @property
    def regulatory_note(self) -> str:
        return (
            "Yahoo Finance via yfinance. Unofficial; terms permit personal, "
            "non-commercial use. Not an IDX-licensed fundamental feed."
        )

    @property
    def limitations(self) -> tuple[str, ...]:
        return (
            "figures are Yahoo's aggregation, not filed IDX statements",
            "trailing twelve months, so a fresh quarterly is reflected late",
            "banks often report no debt/equity - the ratio is not meaningful there",
            "no earnings-quality or management fields (SPEC 7 lists both)",
            "restatements and corporate actions can change history silently",
        )

    def provider_symbol(self, symbol: str) -> str:
        ticker = symbol.strip().upper()
        return ticker if ticker.endswith(".JK") else f"{ticker}.JK"

    async def fetch(self, symbol: str) -> Fundamentals:
        try:
            info = await asyncio.wait_for(
                asyncio.to_thread(self._fetch_blocking, symbol), timeout=self._timeout
            )
        except TimeoutError as exc:
            raise DataSourceUnavailableError(
                f"yahoo fundamentals for {symbol} timed out after {self._timeout}s"
            ) from exc
        except Exception as exc:
            raise DataSourceUnavailableError(
                f"yahoo fundamentals for {symbol} unavailable: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        if not info:
            raise DataSourceUnavailableError(
                f"yahoo returned no fundamental data for {symbol}"
            )
        return self._to_fundamentals(symbol, info)

    def _fetch_blocking(self, symbol: str) -> dict[str, Any]:
        import yfinance

        return yfinance.Ticker(self.provider_symbol(symbol)).info or {}

    def _to_fundamentals(self, symbol: str, info: dict[str, Any]) -> Fundamentals:
        values: dict[str, Any] = {}
        for yahoo_key, (field_name, multiplier) in _MAPPING.items():
            raw = info.get(yahoo_key)
            number = _number(raw)
            if number is not None:
                values[field_name] = number * multiplier

        # Yahoo has reported dividendYield as both a fraction and a percent
        # over time. Anything above 100 is certainly already a percentage.
        dividend = _number(info.get("dividendYield"))
        if dividend is not None:
            values["dividend_yield_pct"] = dividend if dividend <= 100 else None

        missing = tuple(name for name in METRIC_FIELDS if values.get(name) is None)
        if missing:
            log.debug(
                "fundamental.partial", symbol=symbol, missing=len(missing)
            )

        return Fundamentals(
            symbol=symbol,
            source=SOURCE,
            fetched_at=now_utc(),
            currency=info.get("currency"),
            sector=info.get("sector"),
            industry=info.get("industry"),
            missing=missing,
            **values,
        )


def _number(value: Any) -> float | None:
    """Coerce to float, or None. Never substitutes zero for a missing figure."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


__all__ = ["SOURCE", "YahooFundamentalProvider"]
