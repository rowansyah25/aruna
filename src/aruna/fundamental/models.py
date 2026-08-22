"""Fundamental records (SPEC 7).

Every metric is optional. A provider that does not report ROA must leave it
``None``, never zero - a missing figure and a figure of zero mean opposite
things, and conflating them would quietly corrupt any valuation built on top
(SPEC 4).

:attr:`Fundamentals.coverage` is how much of the SPEC 7 set was actually
available, so a verdict from three metrics is distinguishable from one built on
twelve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aruna.core.clock import isoformat, now_utc

#: The SPEC 7 metric set, in the order a reader would expect it.
METRIC_FIELDS: tuple[str, ...] = (
    "revenue_growth_pct",
    "earnings_growth_pct",
    "eps",
    "roe_pct",
    "roa_pct",
    "debt_to_equity",
    "free_cash_flow",
    "total_debt",
    "price_to_earnings",
    "price_to_book",
    "book_value_per_share",
    "dividend_yield_pct",
    "profit_margin_pct",
    "market_cap",
)


@dataclass(frozen=True, slots=True)
class Fundamentals:
    """One company's reported fundamentals, as retrieved."""

    symbol: str
    source: str
    fetched_at: datetime = field(default_factory=now_utc)
    currency: str | None = None
    sector: str | None = None
    industry: str | None = None

    revenue_growth_pct: float | None = None
    earnings_growth_pct: float | None = None
    eps: float | None = None
    roe_pct: float | None = None
    roa_pct: float | None = None
    debt_to_equity: float | None = None
    free_cash_flow: float | None = None
    total_debt: float | None = None
    price_to_earnings: float | None = None
    price_to_book: float | None = None
    book_value_per_share: float | None = None
    dividend_yield_pct: float | None = None
    profit_margin_pct: float | None = None
    market_cap: float | None = None

    #: Fields the provider did not supply, kept explicit for audit (SPEC 4).
    missing: tuple[str, ...] = ()

    @property
    def available_metrics(self) -> tuple[str, ...]:
        return tuple(
            name for name in METRIC_FIELDS if getattr(self, name, None) is not None
        )

    @property
    def coverage(self) -> float:
        """Share of the SPEC 7 metric set that was actually reported, 0..1."""
        return len(self.available_metrics) / len(METRIC_FIELDS)

    @property
    def is_usable(self) -> bool:
        """Enough to say anything at all.

        Below a third of the metric set, a valuation would be a guess with a
        confident-looking label on it.
        """
        return self.coverage >= 0.33

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "source": self.source,
            "fetched_at": isoformat(self.fetched_at),
            "currency": self.currency,
            "sector": self.sector,
            "industry": self.industry,
            "coverage": round(self.coverage, 3),
            "missing": list(self.missing),
        }
        payload |= {name: getattr(self, name) for name in METRIC_FIELDS}
        return payload


__all__ = ["METRIC_FIELDS", "Fundamentals"]
