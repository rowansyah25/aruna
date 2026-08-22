"""Paper trading (SPEC 34, 46).

**No real orders exist anywhere in ARUNA.** SPEC 46 keeps the MVP paper-only,
and this module simulates fills rather than placing them.

SPEC 34 requires net PnL, not gross, so every cost is modelled explicitly:
exchange fees, the spread actually paid on entry and exit, and slippage. Those
numbers are **assumptions, not measurements**. They are stated here rather than
buried, because a backtest that quietly omits costs is how a losing strategy
looks profitable.

Fee models below reflect published retail schedules at the time of writing.
They are configurable and should be checked against the operator's own account
before any figure from this module is trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal

from aruna.core.clock import now_utc
from aruna.core.enums import Decision, Market
from aruna.signals.models import LockedSignal, PaperTrade, TradeResult

_CENT = Decimal("0.01")

#: Skala kolom ``paper_trades.quantity``: ``DECIMAL(30,12)``.
#:
#: ``capital / fill`` menghasilkan Decimal dengan dua puluh delapan digit
#: berarti, dan MySQL memotongnya saat menyimpan - satu baris peringatan "Data
#: truncated for column 'quantity'" pada **setiap** paper trade. Peringatan
#: yang selalu muncul berhenti dibaca, dan yang hilang berikutnya adalah
#: peringatan yang benar-benar penting.
_QTY = Decimal("0.000000000001")


@dataclass(frozen=True, slots=True)
class CostModel:
    """Assumed trading costs for one market.

    ``slippage_bps`` is applied on top of the spread. It stands for the part of
    a fill that moves against you beyond the quoted touch - unmeasurable
    without real fills, so it is a deliberately conservative guess.
    """

    taker_fee_pct: Decimal
    sell_fee_pct: Decimal
    slippage_bps: Decimal
    #: Half-spread is charged on each side when a quote is available.
    charge_spread: bool = True
    note: str = ""

    def entry_fee(self, notional: Decimal) -> Decimal:
        return (notional * self.taker_fee_pct / 100).quantize(
            _CENT, rounding=ROUND_HALF_EVEN
        )

    def exit_fee(self, notional: Decimal) -> Decimal:
        return (notional * self.sell_fee_pct / 100).quantize(
            _CENT, rounding=ROUND_HALF_EVEN
        )


#: Binance spot retail taker fee is 0.10% on both sides at the time of writing,
#: without the BNB fee discount - which ARUNA does not model, because whether
#: the operator holds BNB is not something this code can observe.
#:
#: **This number changed, and it breaks comparability.** Every ``paper_results``
#: row written before 2026-08-17 was computed at 0.30% per side against a venue
#: ARUNA no longer uses. Those rows were deleted with the rest of the IDR
#: history in migration 0020, so nothing currently stored mixes the two - but
#: any figure quoted from a report generated earlier is on the old schedule and
#: is not comparable to a new one. A strategy that looks 0.4% per round trip
#: better after this change did not improve; the fee did.
CRYPTO_COSTS = CostModel(
    taker_fee_pct=Decimal("0.10"),
    sell_fee_pct=Decimal("0.10"),
    slippage_bps=Decimal("10"),
    note="Binance spot retail taker schedule, no BNB discount; "
    "verify against your own account",
)

#: IDX retail brokerage is asymmetric: selling carries additional levies.
IDX_COSTS = CostModel(
    taker_fee_pct=Decimal("0.20"),
    sell_fee_pct=Decimal("0.30"),
    slippage_bps=Decimal("15"),
    note="typical Indonesian retail brokerage including sell-side levies",
)

#: Notional per simulated position, **per market, in that market's own quote
#: currency**. Fixed so PnL is comparable across signals within a market;
#: position sizing is a risk-model question no phase has built yet.
#:
#: One constant covered both markets until PASAL 6 moved crypto to USDT. It
#: cannot any more: 1,000,000 is a sane IDR position on IDX and an absurd one
#: as USDT, and the arithmetic would have produced million-dollar PnL figures
#: on ``/performance`` without a single test failing - the number is only ever
#: multiplied and divided, so nothing internal contradicts it.
#:
#: The two markets' figures are not addable and never were; they are now not
#: even the same unit, which at least makes that visible.
DEFAULT_CAPITAL: dict[Market, Decimal] = {
    Market.CRYPTO: Decimal("1000"),  # USDT
    Market.IDX: Decimal("1000000"),  # IDR
}


def cost_model(market: Market) -> CostModel:
    return CRYPTO_COSTS if market is Market.CRYPTO else IDX_COSTS


def default_capital(market: Market) -> Decimal:
    """Notional for one simulated position in ``market``'s quote currency.

    Deliberately a lookup rather than a default argument: a caller that forgets
    to pass the market gets a ``KeyError`` here, not an IDR-sized position
    silently opened against a USDT price.
    """
    return DEFAULT_CAPITAL[market]


def open_trade(
    signal: LockedSignal,
    *,
    capital: Decimal,
    costs: CostModel | None = None,
    opened_at: datetime | None = None,
) -> PaperTrade:
    """Simulate entering a position on a locked signal.

    Refuses non-directional signals: there is no position to open on a WAIT,
    and creating a zero-size trade would pollute the performance record with
    entries that were never predictions.
    """
    if not signal.is_directional:
        raise ValueError(
            f"signal {signal.signal_id} is {signal.direction.value}; "
            "there is no position to open"
        )
    if capital <= 0 or signal.entry_price <= 0:
        raise ValueError("capital and entry price must both be positive")

    model = costs or cost_model(signal.market)
    moment = opened_at or now_utc()

    # Entry is filled at the ask when buying and the bid when selling - the
    # side that costs you. Using the mid would understate the cost of every
    # single trade.
    fill = _entry_fill(signal, model)

    # **Dibulatkan di sini, bukan di tempat penyimpanan.** Notional, spread,
    # slippage, dan seluruh PnL diturunkan dari angka ini; membulatkannya hanya
    # saat menulis akan menyimpan kuantitas yang tidak lagi cocok dengan PnL
    # yang tersimpan di baris yang sama. Ketidakcocokan itu tidak berbunyi,
    # tidak terlihat, dan membuat setiap rekonsiliasi nanti gagal karena sebab
    # yang tidak bisa ditebak siapa pun.
    #
    # ``ROUND_DOWN``, bukan ``ROUND_HALF_EVEN`` seperti uang di atas: kuantitas
    # yang dibulatkan ke atas berarti posisi yang sedikit lebih besar daripada
    # yang dibeli modalnya. Selisihnya sepersetriliun dan tidak akan pernah
    # terasa - tapi arah pembulatan yang benar tidak berbiaya apa pun, dan
    # "jangan pernah mengaku memegang lebih dari yang dibeli" adalah disiplin
    # yang sama dengan pembulatan lot di venue.
    quantity = (capital / fill).quantize(_QTY, rounding=ROUND_DOWN)
    notional = quantity * fill

    spread_cost = _half_spread_cost(signal, quantity, model)
    slippage = (notional * model.slippage_bps / Decimal(10_000)).quantize(
        _CENT, rounding=ROUND_HALF_EVEN
    )

    return PaperTrade(
        signal_id=signal.signal_id,
        market=signal.market,
        symbol=signal.symbol,
        direction=signal.direction,
        quantity=quantity,
        entry_price=fill,
        entry_at=moment,
        entry_fee=model.entry_fee(notional),
        slippage_cost=slippage,
        spread_cost=spread_cost,
        result=TradeResult.OPEN,
    )


def close_trade(
    trade: PaperTrade,
    exit_price: Decimal,
    *,
    closed_at: datetime | None = None,
    costs: CostModel | None = None,
    target_distance: Decimal | None = None,
) -> PaperTrade:
    """Close a simulated position and compute net PnL (SPEC 34).

    ``target_distance`` is the per-unit distance from entry to the modelled
    target. It yields :attr:`PaperTrade.target_multiple`, which is *not* an
    R-multiple - see the note on that field.
    """
    if not trade.is_open:
        raise ValueError(f"trade for {trade.signal_id} is already closed")
    if exit_price <= 0:
        raise ValueError("exit price must be positive")

    model = costs or cost_model(trade.market)
    moment = closed_at or now_utc()
    exit_notional = trade.quantity * exit_price
    exit_fee = model.exit_fee(exit_notional)

    if trade.direction is Decision.BUY:
        gross = (exit_price - trade.entry_price) * trade.quantity
    else:
        gross = (trade.entry_price - exit_price) * trade.quantity

    costs_total = trade.entry_fee + exit_fee + trade.slippage_cost + trade.spread_cost
    net = gross - costs_total

    invested = trade.entry_price * trade.quantity
    net_pct = float(net / invested * 100) if invested > 0 else 0.0

    target_multiple = None
    if target_distance and target_distance > 0:
        target_total = target_distance * trade.quantity
        if target_total > 0:
            target_multiple = round(float(net / target_total), 4)

    if net > 0:
        result = TradeResult.WIN
    elif net < 0:
        result = TradeResult.LOSS
    else:
        result = TradeResult.BREAKEVEN

    return replace(
        trade,
        exit_price=exit_price,
        exit_at=moment,
        exit_fee=exit_fee,
        gross_pnl=gross.quantize(_CENT, rounding=ROUND_HALF_EVEN),
        net_pnl=net.quantize(_CENT, rounding=ROUND_HALF_EVEN),
        net_pnl_pct=round(net_pct, 6),
        target_multiple=target_multiple,
        result=result,
    )


def _entry_fill(signal: LockedSignal, model: CostModel) -> Decimal:
    """The price actually paid, not the mid."""
    if not model.charge_spread:
        return signal.entry_price
    if signal.direction is Decision.BUY and signal.ask and signal.ask > 0:
        return signal.ask
    if signal.direction is Decision.SELL and signal.bid and signal.bid > 0:
        return signal.bid
    return signal.entry_price


def _half_spread_cost(
    signal: LockedSignal, quantity: Decimal, model: CostModel
) -> Decimal:
    """Half the quoted spread, charged once for the round trip.

    The entry fill already crossed the spread; this accounts for crossing back
    on exit. Zero when the venue publishes no quote, which is honest rather
    than assuming a spread that was never observed.
    """
    if not model.charge_spread or not signal.bid or not signal.ask:
        return Decimal(0)
    spread = signal.ask - signal.bid
    if spread <= 0:
        return Decimal(0)
    return (spread / 2 * quantity).quantize(_CENT, rounding=ROUND_HALF_EVEN)


def summarise(trades: list[PaperTrade]) -> dict[str, object]:
    """Aggregate closed trades (SPEC 41 daily report inputs).

    Reports net figures only. SPEC 34 is explicit that gross PnL is not the
    measure, and a summary that led with it would flatter every result.
    """
    closed = [t for t in trades if not t.is_open]
    if not closed:
        return {
            "trades": 0,
            "note": "no closed paper trades yet",
        }

    wins = [t for t in closed if t.result is TradeResult.WIN]
    losses = [t for t in closed if t.result is TradeResult.LOSS]
    net_total = sum((t.net_pnl for t in closed), Decimal(0))
    gross_total = sum((t.gross_pnl for t in closed), Decimal(0))
    cost_total = sum((t.total_costs for t in closed), Decimal(0))

    won = sum((t.net_pnl for t in wins), Decimal(0))
    lost = abs(sum((t.net_pnl for t in losses), Decimal(0)))
    profit_factor = float(won / lost) if lost > 0 else None

    return {
        "trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(closed), 4),
        "net_pnl": str(net_total),
        "gross_pnl": str(gross_total),
        "total_costs": str(cost_total),
        # How much of the gross was eaten by costs - the figure that decides
        # whether a marginal edge survives contact with reality.
        "cost_ratio": (
            round(float(cost_total / abs(gross_total)), 4) if gross_total else None
        ),
        "expectancy": round(float(net_total / len(closed)), 4),
        "profit_factor": round(profit_factor, 4) if profit_factor else None,
        "trading_mode": "PAPER",
    }


__all__ = [
    "CRYPTO_COSTS",
    "DEFAULT_CAPITAL",
    "IDX_COSTS",
    "CostModel",
    "close_trade",
    "cost_model",
    "default_capital",
    "open_trade",
    "summarise",
]
