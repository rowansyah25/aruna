"""Perpetual futures market records (FUTURES SPEC 2, 4, 24, 27, 28, 29).

Spot needed a price and a candle. A perpetual needs six more things that each
have their own timestamp and their own way of being stale:

* **mark price** - what liquidation is measured against, not what trades;
* **index price** - the spot basket the mark is anchored to;
* **funding** - the periodic payment between longs and shorts;
* **open interest** - how much position is actually open;
* **order book** - what a fill would really cost;
* **liquidations** - who was forced out, and how violently.

Every one carries :class:`~aruna.data.models.Provenance`, and every one is
timestamped separately, because they arrive from different endpoints at
different times. FUTURES SPEC 46 exists precisely because a signal built from a
fresh price and a twenty-minute-old funding rate is not a fresh signal - and
nothing about the price alone would reveal that.

**Perpetuals are CRYPTO instruments, not a third market.** ARUNA has two
markets and that is a project invariant, enforced in the database. A perpetual
is a derivative *instrument* on the crypto market, so it carries an instrument
type rather than a market of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.core.clock import isoformat
from aruna.data.models import Provenance, quantize_bps, quantize_pct


class InstrumentType(StrEnum):
    """What kind of contract this is."""

    PERPETUAL = "PERPETUAL"
    #: Dated futures. Not supported yet; declared so an adapter that meets one
    #: reports it rather than silently treating it as a perpetual, whose
    #: funding mechanics it does not have.
    DATED_FUTURE = "DATED_FUTURE"


class MarginMode(StrEnum):
    """FUTURES SPEC 31. ARUNA recommends; it never changes an account setting."""

    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class PositionSide(StrEnum):
    """The futures vocabulary for a direction.

    ARUNA's council reasons in BUY/SELL and every phase of the spot system is
    built on that, so this is a *presentation* of the same decision rather than
    a parallel one - see :func:`side_of`. Two enums meaning the same thing would
    drift apart, and the learning layer would end up unable to compare a spot
    call with a futures one.
    """

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


def side_of(decision: Any) -> PositionSide:
    """Render a council :class:`~aruna.core.enums.Decision` as a futures side."""
    value = getattr(decision, "value", decision)
    if value == "BUY":
        return PositionSide.LONG
    if value == "SELL":
        return PositionSide.SHORT
    return PositionSide.FLAT


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """What the exchange says this contract is (FUTURES SPEC 19, 24).

    Position size and liquidation price are both wrong without it: an order
    rounded to the wrong tick is rejected, and a liquidation computed on a
    guessed maintenance margin rate is a number nobody should act on.
    """

    symbol: str
    instrument: InstrumentType
    base_asset: str
    quote_asset: str
    #: Smallest price increment the venue accepts.
    tick_size: Decimal
    #: Smallest quantity increment.
    step_size: Decimal
    min_notional: Decimal
    #: The venue's leverage cap, or ``None`` when it could not be fetched.
    #:
    #: ``None`` means *unknown*, and downstream must treat it that way. A
    #: fabricated cap is worse than no cap: this field defaulted to ``1`` once,
    #: which silently emptied the leverage ladder and reported "no safe
    #: leverage" for a reason that had nothing to do with safety.
    max_leverage: int | None = None
    #: Maintenance margin rate at the smallest notional bracket. Liquidation
    #: uses the bracket that matches the position, so this is the floor, not the
    #: whole story - see :attr:`margin_brackets`.
    maintenance_margin_rate: Decimal = Decimal("0.004")
    #: (notional floor, maintenance rate, maintenance amount), ascending.
    margin_brackets: tuple[tuple[Decimal, Decimal, Decimal], ...] = ()
    taker_fee_pct: Decimal = Decimal("0.05")
    maker_fee_pct: Decimal = Decimal("0.02")
    #: What the venue would not supply, in words, so it travels with the spec.
    notes: tuple[str, ...] = ()
    provenance: Provenance | None = None

    @property
    def leverage_cap_known(self) -> bool:
        return self.max_leverage is not None

    def bracket_for(self, notional: Decimal) -> tuple[Decimal, Decimal]:
        """Maintenance rate and amount for a notional (FUTURES SPEC 24).

        Falls back to the base rate with a zero maintenance amount when the
        venue published no brackets.

        **That fallback errs toward optimism, not caution.** A venue's brackets
        only ever raise the maintenance rate as the position grows, so the base
        rate is a floor: it understates the requirement at every notional above
        the first tier, which places the liquidation price further from entry
        than it really is. Measured on BTCUSDT's real tiers, a 1,000,000
        notional long liquidates 151 in price *before* this fallback says it
        will. The error grows with size, and it grows in the direction that
        makes a position look safer than it is.

        This docstring said the opposite for a while, and so did the runtime
        finding and the contract notes. Three copies of one inverted claim, all
        reassuring - which is the direction a wrong claim tends to travel.
        """
        rate, amount = self.maintenance_margin_rate, Decimal(0)
        for floor, bracket_rate, bracket_amount in self.margin_brackets:
            if notional >= floor:
                rate, amount = bracket_rate, bracket_amount
            else:
                break
        return rate, amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "instrument": self.instrument.value,
            "base": self.base_asset,
            "quote": self.quote_asset,
            "tick_size": str(self.tick_size),
            "step_size": str(self.step_size),
            "min_notional": str(self.min_notional),
            "max_leverage": self.max_leverage,
            "maintenance_margin_rate": str(self.maintenance_margin_rate),
            "brackets": len(self.margin_brackets),
            "taker_fee_pct": str(self.taker_fee_pct),
            "maker_fee_pct": str(self.maker_fee_pct),
        }


@dataclass(frozen=True, slots=True)
class MarkPrice:
    """Mark, index and basis at one instant (FUTURES SPEC 2, 24).

    The mark price is what liquidation is measured against; the last traded
    price is not. Confusing the two is how a position gets liquidated at a level
    the trader believed was safe, so both are carried and neither substitutes
    for the other.
    """

    symbol: str
    mark_price: Decimal
    index_price: Decimal | None
    last_price: Decimal | None
    as_of: datetime
    provenance: Provenance

    @property
    def basis_pct(self) -> Decimal | None:
        """Mark against index, in percent. Positive means perpetual over spot."""
        if self.index_price is None or self.index_price <= 0:
            return None
        return quantize_pct(
            (self.mark_price - self.index_price) / self.index_price * 100
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "mark_price": str(self.mark_price),
            "index_price": str(self.index_price) if self.index_price else None,
            "last_price": str(self.last_price) if self.last_price else None,
            "basis_pct": str(self.basis_pct) if self.basis_pct is not None else None,
            "as_of": isoformat(self.as_of),
            "source": self.provenance.source,
        }


@dataclass(frozen=True, slots=True)
class FundingRate:
    """One funding observation (FUTURES SPEC 27).

    ``rate`` is the fraction paid per interval, not annualised and not a
    percentage - a 0.0001 rate is 0.01% per interval. Annualising it here would
    invite the figure to be compared against yields it is nothing like, so the
    conversion is left to whoever wants it and is labelled where it happens.
    """

    symbol: str
    rate: Decimal
    #: When this rate applies, or applied.
    funding_time: datetime
    next_funding_time: datetime | None
    interval_hours: int
    provenance: Provenance
    #: True when this is a settled historical payment rather than a forecast.
    settled: bool = False

    @property
    def rate_pct(self) -> Decimal:
        return quantize_pct(self.rate * 100)

    def cost_pct(self, holding_hours: float) -> Decimal:
        """Funding cost over a holding period, as a percent of notional.

        Assumes the current rate persists, which it will not. Stated rather than
        hidden: it is a projection from one observation, and FUTURES SPEC 27
        wants the *estimated* cost in expected net PnL, not a promise.
        """
        if self.interval_hours <= 0:
            return Decimal(0)
        intervals = Decimal(str(holding_hours)) / Decimal(self.interval_hours)
        return quantize_pct(self.rate * intervals * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "rate": str(self.rate),
            "rate_pct": str(self.rate_pct),
            "funding_time": isoformat(self.funding_time),
            "next_funding_time": (
                isoformat(self.next_funding_time) if self.next_funding_time else None
            ),
            "interval_hours": self.interval_hours,
            "settled": self.settled,
            "source": self.provenance.source,
        }


@dataclass(frozen=True, slots=True)
class OpenInterest:
    """Open interest at one instant (FUTURES SPEC 28)."""

    symbol: str
    open_interest: Decimal
    #: Notional value, when the venue publishes it.
    notional: Decimal | None
    as_of: datetime
    provenance: Provenance

    def change_pct(self, earlier: OpenInterest | None) -> Decimal | None:
        if earlier is None or earlier.open_interest <= 0:
            return None
        delta = self.open_interest - earlier.open_interest
        return quantize_pct(delta / earlier.open_interest * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open_interest": str(self.open_interest),
            "notional": str(self.notional) if self.notional else None,
            "as_of": isoformat(self.as_of),
            "source": self.provenance.source,
        }


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    """One forced close (FUTURES SPEC 26)."""

    symbol: str
    side: PositionSide
    price: Decimal
    quantity: Decimal
    notional: Decimal
    occurred_at: datetime
    provenance: Provenance

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "price": str(self.price),
            "quantity": str(self.quantity),
            "notional": str(self.notional),
            "occurred_at": isoformat(self.occurred_at),
        }


@dataclass(frozen=True, slots=True)
class LongShortRatio:
    """Positioning, when the venue publishes it (FUTURES SPEC 2)."""

    symbol: str
    long_share: Decimal
    short_share: Decimal
    as_of: datetime
    provenance: Provenance

    @property
    def ratio(self) -> Decimal | None:
        if self.short_share <= 0:
            return None
        return quantize_pct(self.long_share / self.short_share)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "long_share": str(self.long_share),
            "short_share": str(self.short_share),
            "ratio": str(self.ratio) if self.ratio is not None else None,
            "as_of": isoformat(self.as_of),
        }


@dataclass(frozen=True, slots=True)
class FuturesSnapshot:
    """Everything a futures decision needs, and what is missing from it.

    Bundled deliberately. FUTURES SPEC 46 asks whether the *set* of inputs is
    coherent, and that question cannot be asked of six records held separately -
    it needs one object that knows which of them arrived and when.

    A field left ``None`` means the venue did not supply it. Nothing here
    substitutes a default, and :attr:`missing` names the gaps so a caller can
    refuse rather than guess (FUTURES SPEC 52).
    """

    symbol: str
    captured_at: datetime
    mark: MarkPrice | None = None
    funding: FundingRate | None = None
    open_interest: OpenInterest | None = None
    long_short: LongShortRatio | None = None
    order_book: Any = None
    recent_liquidations: tuple[LiquidationEvent, ...] = field(default_factory=tuple)
    contract: ContractSpec | None = None

    @property
    def missing(self) -> tuple[str, ...]:
        gaps = [
            name
            for name, value in (
                ("mark_price", self.mark),
                ("funding", self.funding),
                ("open_interest", self.open_interest),
                ("order_book", self.order_book),
                ("contract", self.contract),
            )
            if value is None
        ]
        return tuple(gaps)

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def reference_price(self) -> Decimal | None:
        """The price a decision should be measured against.

        The mark, not the last trade: it is what liquidation uses, and a
        prediction whose reference disagrees with the venue's own risk engine is
        measuring something the trader does not experience.
        """
        return self.mark.mark_price if self.mark else None

    def spread_bps(self) -> Decimal | None:
        book = self.order_book
        if book is None:
            return None
        spread = getattr(book, "spread_bps", None)
        return quantize_bps(spread) if spread is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "captured_at": isoformat(self.captured_at),
            "mark": self.mark.to_dict() if self.mark else None,
            "funding": self.funding.to_dict() if self.funding else None,
            "open_interest": (
                self.open_interest.to_dict() if self.open_interest else None
            ),
            "long_short": self.long_short.to_dict() if self.long_short else None,
            "spread_bps": (
                str(self.spread_bps()) if self.spread_bps() is not None else None
            ),
            "recent_liquidations": len(self.recent_liquidations),
            "contract": self.contract.to_dict() if self.contract else None,
            "missing": list(self.missing),
            "complete": self.complete,
        }


__all__ = [
    "ContractSpec",
    "FundingRate",
    "FuturesSnapshot",
    "InstrumentType",
    "LiquidationEvent",
    "LongShortRatio",
    "MarginMode",
    "MarkPrice",
    "OpenInterest",
    "PositionSide",
    "side_of",
]
