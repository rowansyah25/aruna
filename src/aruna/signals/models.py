"""Locked predictions and their outcomes (SPEC 20, 22, 23, 34).

SPEC 20 calls the prediction lock a **core feature**, and it is the one place
in ARUNA where immutability is the whole point. Once a signal is locked, its
direction, confidence, entry, target, reasoning, timestamp and horizon may
never change. A revised view is a *new* signal that supersedes the old one; the
original stays exactly as it was written.

That is enforced three ways, deliberately overlapping:

* :class:`LockedSignal` is a frozen dataclass, so Python cannot mutate it;
* ``signal_snapshots`` carries an append-only trigger, so SQL cannot either;
* :attr:`LockedSignal.fingerprint` hashes the locked fields, so any tampering
  that got past both is still detectable afterwards.

The reason for the belt and braces is SPEC 22: an outcome is only meaningful
when compared against what was *actually* predicted beforehand. A system that
can quietly adjust its own past predictions cannot be evaluated at all, and
every later phase - autopsy, calibration, backtest - would be measuring
nothing.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import Decision, Horizon, Market

#: Scale of every price column in ``signal_snapshots`` - DECIMAL(30,12).
PRICE_SCALE = Decimal("0.000000000001")


def _canonical_price(value: Decimal | None) -> str:
    """A price string that survives storage and reload unchanged."""
    if value is None:
        return "None"
    return format(Decimal(value).quantize(PRICE_SCALE, rounding=ROUND_HALF_EVEN), "f")


class SignalStatus(StrEnum):
    LOCKED = "LOCKED"
    #: The horizon has elapsed and the outcome has been recorded.
    RESOLVED = "RESOLVED"
    #: Replaced by a later signal before its horizon elapsed.
    SUPERSEDED = "SUPERSEDED"
    #: Retracted for a reason that invalidates it, e.g. detected leakage.
    INVALIDATED = "INVALIDATED"
    #: The horizon elapsed and no market data exists inside it, so no outcome
    #: can ever be computed.
    #:
    #: **Not a result.** There is no outcome row, no paper trade, no win and no
    #: loss - scoring it either way would be inventing one. It records that the
    #: question cannot be answered from the data that exists, with the measured
    #: reason kept in ``signals.withheld_reason``.
    #:
    #: Reached only through a proof, never a timeout: every sampling series
    #: that has rows already runs past the end of the horizon, and not one bar
    #: falls inside it, so no later refresh can change the answer. A prediction
    #: merely waiting on a slow feed stays LOCKED.
    UNSCOREABLE = "UNSCOREABLE"


class OutcomeClass(StrEnum):
    """SPEC 23's purpose: distinguish *how* a prediction went wrong."""

    WRONG_FROM_START = "WRONG_FROM_START"
    RIGHT_THEN_REVERSED = "RIGHT_THEN_REVERSED"
    RIGHT_DIRECTION_BAD_TIMING = "RIGHT_DIRECTION_BAD_TIMING"
    TARGET_REACHED = "TARGET_REACHED"
    TARGET_NOT_REACHED = "TARGET_NOT_REACHED"
    HORIZON_MISMATCH = "HORIZON_MISMATCH"
    #: A WAIT that was correct to stand aside, or one that missed a move
    #: (SPEC 28 ghost signals).
    NO_POSITION = "NO_POSITION"


class TradeResult(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"
    OPEN = "OPEN"


@dataclass(frozen=True, slots=True)
class LockedSignal:
    """An immutable prediction (SPEC 20).

    Every field below is part of the lock. The fingerprint covers exactly the
    fields SPEC 20 forbids changing, so a later phase can prove the record it
    is scoring is the record that was written.
    """

    signal_id: str
    market: Market
    symbol: str
    horizon: Horizon
    direction: Decision
    confidence: float

    #: The price the prediction is measured against (SPEC 20 reference price).
    reference_price: Decimal
    entry_price: Decimal
    target_price: Decimal | None
    expected_move_pct: float | None

    locked_at: datetime
    #: Newest settled bar behind the decision. Proves nothing later leaked in.
    as_of: datetime
    #: When the horizon elapses and the outcome becomes measurable.
    resolves_at: datetime

    #: Keyakinan model **sebelum** kalibrasi (bagian 9), atau ``None`` untuk
    #: prediksi yang dikunci sebelum kalibrator ada.
    #:
    #: Keduanya disimpan dengan sengaja. ``confidence`` adalah yang dinyatakan
    #: kepada operator dan karenanya harus sesuai kenyataan; yang ini adalah
    #: keluaran model, dan pengukuran kalibrasi berikutnya HARUS memakai yang
    #: ini. Kalibrasi yang mengukur keluarannya sendiri akan melaporkan bahwa
    #: semuanya baik-baik saja pada putaran kedua.
    confidence_raw: float | None = None

    bid: Decimal | None = None
    ask: Decimal | None = None
    spread_bps: Decimal | None = None

    reasoning: tuple[str, ...] = field(default_factory=tuple)
    regime: str | None = None
    news_state: str | None = None
    risk_level: str | None = None
    data_source: str = ""
    data_timestamp: datetime | None = None
    model_version: str = ""
    council_session_id: int | None = None

    status: SignalStatus = SignalStatus.LOCKED
    supersedes: str | None = None

    @property
    def fingerprint(self) -> str:
        """Hash of the fields SPEC 20 forbids changing.

        Recomputing this later and comparing against the stored value detects
        any edit that bypassed both the frozen dataclass and the SQL trigger.

        Every number is canonicalised to the scale of the column it is stored
        in, because the hash has to survive a database round trip to be worth
        anything. ``Decimal("1000")`` comes back from ``DECIMAL(30,12)`` as
        ``1000.000000000000``; hashing the raw ``str`` would make an untouched
        record look tampered with, and ``verify_integrity`` would then reject
        every prediction it was built to protect.
        """
        basis = "|".join(
            [
                self.signal_id,
                self.market.value,
                self.symbol,
                self.horizon.value,
                self.direction.value,
                f"{self.confidence:.3f}",
                _canonical_price(self.reference_price),
                _canonical_price(self.entry_price),
                _canonical_price(self.target_price),
                (
                    f"{self.expected_move_pct:.6f}"
                    if self.expected_move_pct is not None
                    else "None"
                ),
                isoformat(self.locked_at),
                isoformat(self.as_of),
                "".join(self.reasoning),
            ]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @property
    def is_directional(self) -> bool:
        return self.direction.is_directional

    def is_due(self, *, reference: datetime | None = None) -> bool:
        return (reference or now_utc()) >= self.resolves_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "fingerprint": self.fingerprint,
            "market": self.market.value,
            "symbol": self.symbol,
            "horizon": self.horizon.value,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 3),
            "reference_price": str(self.reference_price),
            "entry_price": str(self.entry_price),
            "target_price": str(self.target_price) if self.target_price else None,
            "expected_move_pct": self.expected_move_pct,
            "locked_at": isoformat(self.locked_at),
            "as_of": isoformat(self.as_of),
            "resolves_at": isoformat(self.resolves_at),
            "bid": str(self.bid) if self.bid else None,
            "ask": str(self.ask) if self.ask else None,
            "spread_bps": str(self.spread_bps) if self.spread_bps else None,
            "reasoning": list(self.reasoning),
            "regime": self.regime,
            "news_state": self.news_state,
            "risk_level": self.risk_level,
            "data_source": self.data_source,
            "data_timestamp": (
                isoformat(self.data_timestamp) if self.data_timestamp else None
            ),
            "model_version": self.model_version,
            "council_session_id": self.council_session_id,
            "status": self.status.value,
            "supersedes": self.supersedes,
            "trading_mode": "PAPER",
        }


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    """One observation of how a prediction is faring (SPEC 23).

    Samples are taken *during* the horizon as well as at its end, because
    SPEC 23 wants to distinguish a call that was wrong from the start from one
    that was right and then reversed. The end price alone cannot tell them
    apart.
    """

    signal_id: str
    offset_label: str
    sampled_at: datetime
    price: Decimal
    move_pct: float
    #: True while the move is in the predicted direction.
    favourable: bool
    is_final: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "offset": self.offset_label,
            "sampled_at": isoformat(self.sampled_at),
            "price": str(self.price),
            "move_pct": round(self.move_pct, 6),
            "favourable": self.favourable,
            "is_final": self.is_final,
        }


@dataclass(frozen=True, slots=True)
class SignalOutcome:
    """The verdict on a locked prediction (SPEC 22).

    Deliberately carries the original prediction alongside the result. SPEC 22
    requires the comparison to be against what was predicted, and keeping both
    in one record makes an accidental comparison against a revised figure
    impossible.
    """

    signal_id: str
    original_direction: Decision
    reference_price: Decimal
    predicted_move_pct: float | None
    actual_move_pct: float
    final_price: Decimal
    resolved_at: datetime

    direction_correct: bool
    outcome_class: OutcomeClass
    #: Signed difference between predicted and actual move.
    prediction_error: float | None
    #: Worst adverse excursion during the horizon, in percent. On a WAIT there
    #: is no position, so this is the market's own downward excursion instead.
    max_adverse_pct: float = 0.0
    #: Best favourable excursion during the horizon, in percent. On a WAIT this
    #: is the market's upward excursion - what standing aside passed up.
    max_favourable_pct: float = 0.0
    target_reached: bool = False
    samples: tuple[OutcomeSample, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "original_direction": self.original_direction.value,
            "reference_price": str(self.reference_price),
            "predicted_move_pct": self.predicted_move_pct,
            "actual_move_pct": round(self.actual_move_pct, 6),
            "final_price": str(self.final_price),
            "resolved_at": isoformat(self.resolved_at),
            "direction": "CORRECT" if self.direction_correct else "WRONG",
            "outcome_class": self.outcome_class.value,
            "prediction_error": (
                round(self.prediction_error, 6)
                if self.prediction_error is not None
                else None
            ),
            "max_adverse_pct": round(self.max_adverse_pct, 6),
            "max_favourable_pct": round(self.max_favourable_pct, 6),
            "target_reached": self.target_reached,
            "samples": [s.to_dict() for s in self.samples],
        }


@dataclass(frozen=True, slots=True)
class PaperTrade:
    """A simulated position (SPEC 34).

    **Paper only.** There is no execution path in ARUNA, and SPEC 46 keeps it
    that way for the MVP. Every cost below is a modelled assumption, not a
    measured fill - see the PHASE 7 report.
    """

    signal_id: str
    market: Market
    symbol: str
    direction: Decision
    quantity: Decimal
    entry_price: Decimal
    entry_at: datetime
    exit_price: Decimal | None = None
    exit_at: datetime | None = None

    entry_fee: Decimal = Decimal(0)
    exit_fee: Decimal = Decimal(0)
    slippage_cost: Decimal = Decimal(0)
    spread_cost: Decimal = Decimal(0)

    gross_pnl: Decimal = Decimal(0)
    #: SPEC 34 is explicit: use net, not gross.
    net_pnl: Decimal = Decimal(0)
    net_pnl_pct: float = 0.0
    #: Net PnL as a multiple of the distance to the modelled target.
    #:
    #: Deliberately *not* called an R-multiple. R is measured against the risk
    #: taken - the distance to a stop - and ARUNA has no stop loss, so there is
    #: no R to report. Naming it R would import a meaning the number does not
    #: carry and quietly flatter every trade that ran without a stop.
    target_multiple: float | None = None
    result: TradeResult = TradeResult.OPEN

    @property
    def total_costs(self) -> Decimal:
        return self.entry_fee + self.exit_fee + self.slippage_cost + self.spread_cost

    @property
    def holding_seconds(self) -> float | None:
        if self.exit_at is None:
            return None
        return (self.exit_at - self.entry_at).total_seconds()

    @property
    def is_open(self) -> bool:
        return self.exit_at is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "market": self.market.value,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "quantity": str(self.quantity),
            "entry_price": str(self.entry_price),
            "entry_at": isoformat(self.entry_at),
            "exit_price": str(self.exit_price) if self.exit_price else None,
            "exit_at": isoformat(self.exit_at) if self.exit_at else None,
            "entry_fee": str(self.entry_fee),
            "exit_fee": str(self.exit_fee),
            "slippage_cost": str(self.slippage_cost),
            "spread_cost": str(self.spread_cost),
            "total_costs": str(self.total_costs),
            "gross_pnl": str(self.gross_pnl),
            "net_pnl": str(self.net_pnl),
            "net_pnl_pct": round(self.net_pnl_pct, 6),
            "target_multiple": self.target_multiple,
            "holding_seconds": self.holding_seconds,
            "result": self.result.value,
            "trading_mode": "PAPER",
        }


__all__ = [
    "LockedSignal",
    "OutcomeClass",
    "OutcomeSample",
    "PaperTrade",
    "SignalOutcome",
    "SignalStatus",
    "TradeResult",
]
