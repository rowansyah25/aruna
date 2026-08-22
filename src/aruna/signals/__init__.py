"""Prediction lock, paper trading, and outcomes (PHASE 7).

The service lives in its own module: it depends on the repository layer, which
imports these types, so exporting it here would close an import cycle.
"""

from aruna.signals.lock import (
    ImmutabilityError,
    LeakageError,
    build_signal,
    should_lock,
    supersede,
)
from aruna.signals.models import (
    LockedSignal,
    OutcomeClass,
    OutcomeSample,
    PaperTrade,
    SignalOutcome,
    SignalStatus,
    TradeResult,
)
from aruna.signals.multihorizon import MultiHorizonView, build_view
from aruna.signals.outcome import resolve
from aruna.signals.paper import close_trade, open_trade

__all__ = [
    "ImmutabilityError",
    "LeakageError",
    "LockedSignal",
    "MultiHorizonView",
    "OutcomeClass",
    "OutcomeSample",
    "PaperTrade",
    "SignalOutcome",
    "SignalStatus",
    "TradeResult",
    "build_signal",
    "build_view",
    "close_trade",
    "open_trade",
    "resolve",
    "should_lock",
    "supersede",
]
