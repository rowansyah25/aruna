"""Backtest, walk-forward, out-of-sample and decision replay (PHASE 9).

The service lives in its own module because it depends on the repository layer,
which imports these types.
"""

from aruna.backtest.engine import (
    KNOWN_OPTIMISM,
    BacktestEngine,
    BacktestResult,
    combine,
)
from aruna.backtest.replay import Divergence, ReplayResult, compare
from aruna.backtest.walkforward import (
    Fold,
    FoldResult,
    HoldoutViolation,
    Split,
    WalkForwardReport,
    split_period,
)
from aruna.backtest.window import LeakageError, Window, assert_no_leakage

__all__ = [
    "KNOWN_OPTIMISM",
    "BacktestEngine",
    "BacktestResult",
    "Divergence",
    "Fold",
    "FoldResult",
    "HoldoutViolation",
    "LeakageError",
    "ReplayResult",
    "Split",
    "WalkForwardReport",
    "Window",
    "assert_no_leakage",
    "combine",
    "compare",
    "split_period",
]
