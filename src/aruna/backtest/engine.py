"""Backtest engine (SPEC 35, 36).

Replays ARUNA over history: at each settled bar, build the evidence that existed
*then*, convene the same council, apply the same lock rules, and score the
result with the same outcome engine.

The reuse is the design. A backtest that reimplemented the decision path would
measure a different system than the one that trades, and every conclusion drawn
from it would be about that other system. So this module contains no analysis,
no agents and no scoring of its own - it is a loop over :class:`Window` slices
feeding the production code.

What it *cannot* reproduce is stated rather than papered over:

* **No order book.** History has bar closes and nothing else, so fills pay no
  spread. Backtested costs are therefore lower than live costs would be.
* **No news, no fundamentals.** Those are stored with a fetch time, not a
  point-in-time history, so a replayed context carries neither rather than
  carrying today's.

Both make backtested results optimistic, and both are reported on every run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from aruna.agents.context import DecisionContext
from aruna.analysis.engine import AnalysisEngine
from aruna.analysis.series import InsufficientData
from aruna.backtest.window import Window, assert_no_leakage
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.council.session import Council
from aruna.signals.lock import build_signal, should_lock
from aruna.signals.models import PaperTrade, SignalOutcome
from aruna.signals.outcome import build_samples, exit_point, resolve
from aruna.signals.paper import DEFAULT_CAPITAL as PAPER_CAPITAL
from aruna.signals.paper import close_trade, default_capital, open_trade
from aruna.signals.paper import summarise as summarise_trades

log = get_logger("aruna.backtest")

#: Notional per simulated position, matching the live paper trader so the two
#: sets of figures are comparable - which now means matching it *per market*,
#: since crypto is quoted in USDT and IDX in IDR (PASAL 6). A single constant
#: here would have made every backtest disagree with the live trader it is
#: meant to be compared against, in one market only.
DEFAULT_CAPITAL = PAPER_CAPITAL

#: Caveats that apply to every backtest ARUNA can currently run. Attached to the
#: result rather than left in this docstring, so they travel with the numbers.
KNOWN_OPTIMISM: tuple[str, ...] = (
    "no historical order book: fills pay no spread, so costs are understated",
    "no point-in-time news or fundamentals: replayed decisions saw neither",
    "no market impact or partial fills: every order is assumed filled in full",
    "survivorship: only assets currently in the universe are replayed",
)


@dataclass(slots=True)
class BacktestResult:
    symbol: str
    interval: Horizon
    market: Market
    start: datetime | None = None
    end: datetime | None = None
    steps: int = 0

    #: Verdicts the lock would have published.
    published: int = 0
    #: Directional verdicts the lock declined, with reasons counted.
    withheld: dict[str, int] = field(default_factory=dict)
    waits: int = 0
    skipped: int = 0

    trades: list[PaperTrade] = field(default_factory=list)
    outcomes: list[SignalOutcome] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> int:
        return len(self.outcomes)

    @property
    def correct(self) -> int:
        return sum(1 for o in self.outcomes if o.direction_correct)

    def summary(self) -> str:
        parts = [
            f"steps={self.steps}",
            f"published={self.published}",
            f"resolved={self.resolved}",
        ]
        if self.withheld:
            parts.append(f"withheld={sum(self.withheld.values())}")
        if self.failures:
            parts.append(f"failures={len(self.failures)}")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        trades = summarise_trades(self.trades)
        return {
            "symbol": self.symbol,
            "interval": self.interval.value,
            "market": self.market.value,
            "period": {
                "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None,
            },
            "decisions_simulated": self.steps,
            "published": self.published,
            "withheld": self.withheld,
            "waits": self.waits,
            "resolved": self.resolved,
            "direction_correct": self.correct,
            "direction_accuracy": (
                round(self.correct / self.resolved, 4) if self.resolved else None
            ),
            "paper_trades": trades,
            "known_optimism": list(KNOWN_OPTIMISM),
            "note": (
                "a backtest measures the rules against recorded history; it is "
                "not a forecast, and the caveats above all push results in the "
                "flattering direction"
            ),
        }


class BacktestEngine:
    """Replays the live decision path over a historical window.

    ``exit_at_target`` and ``stop_loss`` select the exit rule so the variants
    can be measured against each other. Both off is the live behaviour: hold to
    horizon expiry and close at whatever price is there.

    These are the only parameterised rules in ARUNA, and they exist as
    parameters rather than edits precisely so the comparison can go through the
    SPEC 44 gate. Changing the live rule silently would produce exactly the
    unvalidated, unattributed model change PHASE 10 was built to prevent.
    """

    def __init__(
        self,
        *,
        council: Council | None = None,
        exit_at_target: bool = False,
        stop_loss: bool = False,
    ) -> None:
        self._council = council or Council()
        self._analysis = AnalysisEngine()
        self._exit_at_target = exit_at_target
        self._stop_loss = stop_loss

    def run(
        self,
        window: Window,
        *,
        start: datetime,
        end: datetime,
        every: int = 1,
        capital: Decimal | None = None,
        model_version: str = "backtest",
        guard: Callable[[datetime], None] | None = None,
    ) -> BacktestResult:
        """Decide at every settled bar in the period, then score each decision.

        ``guard`` is called with every decision instant before anything is read.
        The out-of-sample holdout passes its own check here, which is what makes
        SPEC 38 a guarantee rather than a note in a docstring: a bounds error
        anywhere in the caller's date arithmetic raises instead of quietly
        spending the reserved data.

        ``capital`` defaults to the window's own market rather than to a
        constant, because the markets are quoted in different currencies. A
        caller that passes one explicitly is responsible for it being in the
        right unit; the default cannot be wrong.
        """
        if capital is None:
            capital = default_capital(window.market)
        result = BacktestResult(
            symbol=window.symbol, interval=window.interval, market=window.market
        )
        moments = window.steps(start=start, end=end, every=every)
        if not moments:
            result.failures.append(
                f"no settled bars for {window.symbol} {window.interval.value} "
                f"between {start.isoformat()} and {end.isoformat()}"
            )
            return result

        result.start, result.end = moments[0], moments[-1]
        result.steps = len(moments)

        for moment in moments:
            if guard is not None:
                # Deliberately outside the try: a holdout violation must reach
                # the caller, not be filed as one more step that failed.
                guard(moment)
            try:
                self._step(window, moment, result, capital, model_version)
            except InsufficientData:
                result.skipped += 1
            except ArunaError as exc:
                result.failures.append(f"{moment.isoformat()}: {exc}")

        log.info(
            "backtest.completed",
            symbol=window.symbol,
            interval=window.interval.value,
            steps=result.steps,
            published=result.published,
            resolved=result.resolved,
        )
        return result

    def _step(
        self,
        window: Window,
        moment: datetime,
        result: BacktestResult,
        capital: Decimal,
        model_version: str,
    ) -> None:
        series = window.series_at(moment)
        technical = self._analysis.analyse(series)
        assert_no_leakage(technical.as_of, moment, window.symbol)

        context = DecisionContext(
            market=window.market,
            symbol=window.symbol,
            interval=window.interval,
            as_of=technical.as_of,
            state=window.state_at(moment),
            technical=technical,
            # Neither is available point-in-time; passing today's would be
            # look-ahead of the worst kind, since news is why prices move.
            news=(),
            fundamentals=None,
            valuation=None,
            trading_allowed=True,
        )

        verdict = self._council.convene(context)
        signal = build_signal(
            verdict, context, model_version=model_version, locked_at=moment
        )
        publishable, reason = should_lock(signal)

        if not signal.is_directional:
            result.waits += 1
            return
        if not publishable:
            key = _reason_key(reason)
            result.withheld[key] = result.withheld.get(key, 0) + 1
            return

        result.published += 1

        # Score it from bars that closed strictly after the decision.
        prices = window.prices_after(moment, signal.resolves_at)
        if not prices:
            return
        samples = build_samples(signal, prices)
        if not samples:
            return

        outcome = resolve(signal, samples, resolved_at=signal.resolves_at)
        result.outcomes.append(outcome)

        try:
            trade = open_trade(signal, capital=capital, opened_at=moment)
            distance = (
                abs(signal.target_price - signal.entry_price)
                if signal.target_price
                else None
            )
            if self._exit_at_target or self._stop_loss:
                exit_price, exit_at, _ = exit_point(
                    signal,
                    samples,
                    take_profit=self._exit_at_target,
                    stop_loss=self._stop_loss,
                )
            else:
                exit_price, exit_at = outcome.final_price, outcome.resolved_at
            result.trades.append(
                close_trade(
                    trade,
                    exit_price,
                    closed_at=exit_at,
                    target_distance=distance,
                )
            )
        except ValueError as exc:
            result.failures.append(f"{moment.isoformat()}: {exc}")


def _reason_key(reason: str) -> str:
    """Bucket a withholding reason so counts stay readable."""
    if "stale" in reason:
        return "stale_evidence"
    if "below the" in reason:
        return "below_confidence_floor"
    if "not a position" in reason:
        return "not_directional"
    return "other"


def combine(results: list[BacktestResult]) -> dict[str, Any]:
    """Aggregate across assets, keeping the caveats attached."""
    resolved = sum(r.resolved for r in results)
    correct = sum(r.correct for r in results)
    trades = [t for r in results for t in r.trades]
    return {
        "assets": len(results),
        "decisions_simulated": sum(r.steps for r in results),
        "published": sum(r.published for r in results),
        "resolved": resolved,
        "direction_correct": correct,
        "direction_accuracy": round(correct / resolved, 4) if resolved else None,
        "paper_trades": summarise_trades(trades),
        "known_optimism": list(KNOWN_OPTIMISM),
    }


__all__ = [
    "DEFAULT_CAPITAL",
    "KNOWN_OPTIMISM",
    "BacktestEngine",
    "BacktestResult",
    "combine",
]
