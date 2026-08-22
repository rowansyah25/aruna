"""Backtest, walk-forward and replay against stored data (PHASE 9).

Loads bars once per asset, replays the live decision path over them, and stores
the run. Replay (SPEC 39) reconstructs a stored decision from its own recorded
inputs, including the SPEC 16 factors as they stood at the time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import InsufficientData
from aruna.backtest.engine import BacktestEngine, BacktestResult, combine
from aruna.backtest.replay import ReplayResult, compare
from aruna.backtest.replay import summarise as summarise_replays
from aruna.backtest.walkforward import (
    Fold,
    FoldResult,
    Split,
    WalkForwardReport,
    split_period,
)
from aruna.backtest.window import Window, assert_no_leakage
from aruna.core.enums import Decision, Horizon, Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.council.session import Council

log = get_logger("aruna.backtest.service")

#: Bars to load per asset. A backtest wants everything available.
LOAD_LIMIT = 5000


@dataclass(slots=True)
class BacktestRun:
    market: Market
    interval: Horizon
    results: list[BacktestResult] = field(default_factory=list)
    walk_forward: WalkForwardReport | None = None
    split: Split | None = None
    #: Whether this run was allowed into the reserved tail (SPEC 38). Stored
    #: with the run: PHASE 10 chooses between variants on these numbers, and a
    #: record that could not say whether a result had seen the holdout would
    #: make that choice unauditable.
    holdout_included: bool = False
    failures: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"assets={len(self.results)}",
            f"published={sum(r.published for r in self.results)}",
            f"resolved={sum(r.resolved for r in self.results)}",
        ]
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market.value,
            "interval": self.interval.value,
            "per_asset": [r.to_dict() for r in self.results],
            "combined": combine(self.results),
            "walk_forward": (
                self.walk_forward.to_dict() if self.walk_forward else None
            ),
            "split": self.split.to_dict() if self.split else None,
            "holdout_included": self.holdout_included,
            "failures": self.failures,
        }


class BacktestService:
    def __init__(
        self,
        *,
        universe: Any,
        market_data: Any,
        store: Any = None,
        learning: Any = None,
    ) -> None:
        self._universe = universe
        self._market_data = market_data
        self._store = store
        # Used by replay to rebuild the SPEC 16 factors as they were.
        self._learning = learning
        self._analysis = AnalysisEngine()

    # ---- backtest --------------------------------------------------------

    async def run(
        self,
        market: Market,
        interval: Horizon,
        *,
        symbols: tuple[str, ...] | None = None,
        every: int = 1,
        folds: int = 4,
        include_holdout: bool = False,
        exit_at_target: bool = False,
        stop_loss: bool = False,
    ) -> BacktestRun:
        """Replay the decision path over every stored bar for a market."""
        run = BacktestRun(market=market, interval=interval)
        assets = await self._universe.assets(market=market, enabled_only=True)
        if symbols:
            wanted = {s.upper() for s in symbols}
            assets = [a for a in assets if a.symbol.upper() in wanted]
        if not assets:
            run.failures.append(f"no enabled assets for {market.value}")
            return run

        engine = BacktestEngine(
            exit_at_target=exit_at_target, stop_loss=stop_loss
        )
        windows: list[Window] = []

        for asset in assets:
            rows = await self._market_data.candles(
                asset.id, interval, limit=LOAD_LIMIT, closed_only=True
            )
            if len(rows) < 40:
                run.failures.append(
                    f"{asset.symbol}: {len(rows)} stored bar(s) is too few to "
                    "backtest - run `aruna fetch` first"
                )
                continue
            windows.append(
                Window.from_rows(
                    rows, market=market, symbol=asset.symbol, interval=interval
                )
            )

        if not windows:
            return run

        span = _overall_span(windows)
        if span is None:
            run.failures.append("no usable bars")
            return run
        start, end = span

        try:
            run.split = split_period(start, end, folds=folds)
        except (ValueError, ArunaError) as exc:
            run.failures.append(f"cannot split the period: {exc}")
            return run

        # Evaluated region only, unless the holdout was explicitly requested.
        run.holdout_included = include_holdout
        evaluation_end = (
            run.split.holdout_end if include_holdout else run.split.holdout_start
        )
        # The guard is what makes SPEC 38 real. Without it the holdout is
        # protected only by the date arithmetic above being correct, and a
        # boundary error would spend the reserved data silently.
        guard = None if include_holdout else run.split.check_within_evaluation

        for window in windows:
            run.results.append(
                engine.run(
                    window,
                    start=start,
                    end=evaluation_end,
                    every=every,
                    guard=guard,
                )
            )

        run.walk_forward = self._walk_forward(
            engine, windows, run.split, every=every, include_holdout=include_holdout
        )
        if self._store is not None:
            await self._store.record_backtest(run)
        return run

    def _walk_forward(
        self,
        engine: BacktestEngine,
        windows: list[Window],
        split: Split,
        *,
        every: int,
        include_holdout: bool,
    ) -> WalkForwardReport:
        report = WalkForwardReport()
        for fold in split.folds:
            # Every evaluation fold sits before the holdout by construction;
            # the guard proves it rather than assuming it.
            report.results.append(
                self._fold_result(
                    engine,
                    windows,
                    fold,
                    every=every,
                    guard=split.check_within_evaluation,
                )
            )
        if include_holdout:
            report.holdout = self._fold_result(
                engine, windows, split.holdout, every=every, guard=None
            )
        return report

    def _fold_result(
        self,
        engine: BacktestEngine,
        windows: list[Window],
        fold: Fold,
        *,
        every: int,
        guard: Any = None,
    ) -> FoldResult:
        result = FoldResult(fold=fold)
        net = Decimal(0)
        for window in windows:
            outcome = engine.run(
                window, start=fold.start, end=fold.end, every=every, guard=guard
            )
            result.published += outcome.published
            result.resolved += outcome.resolved
            result.correct += outcome.correct
            net += sum((t.net_pnl for t in outcome.trades), Decimal(0))
        result.net_pnl = str(net)
        return result

    # ---- replay (SPEC 39) -------------------------------------------------

    async def replay(self, *, limit: int = 20) -> list[ReplayResult]:
        """Re-run stored decisions from their recorded inputs."""
        if self._store is None:
            return []
        results: list[ReplayResult] = []
        for record in await self._store.replayable(limit=limit):
            results.append(await self._replay_one(record))
        return results

    async def _replay_one(self, record: dict[str, Any]) -> ReplayResult:
        signal_id = record["signal_id"]
        symbol = record["symbol"]

        asset = await self._universe.find(Market(record["market_code"]), symbol)
        if asset is None:
            return ReplayResult(
                signal_id=signal_id,
                symbol=symbol,
                unavailable="the asset is no longer in the universe",
            )

        interval = Horizon(record["horizon_code"])
        rows = await self._market_data.candles(
            asset.id, interval, limit=LOAD_LIMIT, closed_only=True
        )
        window = Window.from_rows(
            rows, market=asset.market, symbol=symbol, interval=interval
        )

        as_of = record["as_of"]
        try:
            series = window.series_at(as_of)
        except InsufficientData as exc:
            return ReplayResult(
                signal_id=signal_id,
                symbol=symbol,
                unavailable=f"the bars behind this decision are gone: {exc}",
            )

        technical = self._analysis.analyse(series)
        assert_no_leakage(technical.as_of, record["locked_at"], symbol)

        history = None
        if self._learning is not None:
            # SPEC 16 factors as they stood then, not as they stand now. Weights
            # move as outcomes accumulate, so today's would diverge for reasons
            # unrelated to determinism.
            history = await self._learning.history_as_of(record["locked_at"])

        from aruna.agents.context import DecisionContext

        context = DecisionContext(
            market=asset.market,
            symbol=symbol,
            interval=interval,
            as_of=technical.as_of,
            state=window.state_at(as_of),
            technical=technical,
            news=(),
            fundamentals=None,
            valuation=None,
            trading_allowed=True,
        )
        verdict = Council(history=history).convene(context)

        return compare(
            signal_id=signal_id,
            symbol=symbol,
            stored_decision=Decision(record["direction"]),
            stored_confidence=float(record["confidence"]),
            stored_as_of=as_of,
            replayed_decision=verdict.decision,
            replayed_confidence=verdict.confidence,
            replayed_as_of=technical.as_of,
        )

    @staticmethod
    def summarise_replays(results: list[ReplayResult]) -> dict[str, Any]:
        return summarise_replays(results)


def _overall_span(windows: list[Window]) -> tuple[datetime, datetime] | None:
    spans = [w.span for w in windows if w.span is not None]
    if not spans:
        return None
    return min(s[0] for s in spans), max(s[1] for s in spans)


__all__ = ["LOAD_LIMIT", "BacktestRun", "BacktestService"]
