"""The futures loop (FUTURES SPEC 48).

The property worth the most here is that a failing tick does not end the run.
An unattended loop that dies on the first network hiccup is worse than no loop,
because the operator believes it is still watching and the gap in the record
looks exactly like a quiet market.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.errors import ArunaError
from aruna.futures.scheduler import (
    MIN_INTERVAL_SEC,
    FuturesScheduler,
    LoopStats,
    next_run_window,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


class _Run:
    def __init__(self, **counts):
        self._counts = {
            "considered": 1,
            "plans": 0,
            "refused": 1,
            "waited": 0,
            "no_signal": 0,
            "stored": 1,
            "errors": [],
            **counts,
        }

    def to_dict(self):
        return dict(self._counts)


class _Service:
    """Records what it was asked for and answers from a script."""

    def __init__(self, script=None):
        self.calls = 0
        self._script = script or []

    async def plan(self, symbols, **kwargs):
        self.calls += 1
        if self._script:
            step = self._script[min(self.calls - 1, len(self._script) - 1)]
            if isinstance(step, Exception):
                raise step
            return step
        return _Run()


def _scheduler(service, *, interval=60):
    return FuturesScheduler(
        service=service,
        symbols=["BTCUSDT"],
        horizon="4h",
        equity=Decimal(10_000),
        interval_sec=interval,
    )


class TestTheLoopRefusesUnsafeSettings:
    def test_an_interval_below_the_floor_is_refused(self) -> None:
        with pytest.raises(ArunaError) as exc:
            _scheduler(_Service(), interval=5)
        assert "rate limit" in str(exc.value)

    def test_the_floor_itself_is_accepted(self) -> None:
        """Guards against a constant nothing can reach."""
        assert _scheduler(_Service(), interval=MIN_INTERVAL_SEC) is not None


class TestAFailingTickDoesNotEndTheRun:
    @pytest.mark.asyncio
    async def test_the_loop_survives_an_exception_and_keeps_going(self) -> None:
        service = _Service([RuntimeError("network went away"), _Run(), _Run()])
        stats = LoopStats(started_at=NOW)
        slept: list[float] = []

        async def sleep(seconds):
            slept.append(seconds)
            # Three ticks, then let the window close.
            if len(slept) >= 3:
                stats.started_at = NOW  # keep the field stable for the assert
                raise _Stop()

        with pytest.raises(_Stop):
            await _scheduler(service).run_until(
                _far_future(), stats=stats, sleep=sleep
            )

        assert service.calls >= 3
        assert stats.failed_ticks == 1
        assert stats.ticks >= 3
        assert any("network went away" in e for e in stats.errors)

    @pytest.mark.asyncio
    async def test_a_failed_tick_is_still_counted_as_a_tick(self) -> None:
        """Otherwise a loop that fails every time looks like a loop that never
        ran, and the two need telling apart."""
        service = _Service([RuntimeError("boom")])
        stats = LoopStats(started_at=NOW)

        async def sleep(_seconds):
            raise _Stop()

        with pytest.raises(_Stop):
            await _scheduler(service).run_until(
                _far_future(), stats=stats, sleep=sleep
            )
        assert stats.ticks == 1
        assert stats.failed_ticks == 1


class TestItStopsWhenTold:
    @pytest.mark.asyncio
    async def test_a_window_already_past_runs_no_tick(self) -> None:
        from aruna.core.clock import now_utc

        service = _Service()
        stats = await _scheduler(service).run_until(now_utc() - timedelta(hours=1))
        assert service.calls == 0
        assert stats.ticks == 0
        assert stats.summary() == "no tick completed"

    def test_the_window_is_measured_from_now(self) -> None:
        assert next_run_window(24, reference=NOW) == NOW + timedelta(hours=24)


class TestTheTallyIsHonest:
    def test_counts_accumulate_across_ticks(self) -> None:
        stats = LoopStats(started_at=NOW)
        stats.absorb(_Run(considered=3, refused=3, stored=3).to_dict())
        stats.absorb(_Run(considered=2, plans=1, refused=1, stored=2).to_dict())
        assert stats.considered == 5
        assert stats.refused == 4
        assert stats.plans == 1
        assert stats.stored == 5

    def test_a_run_with_no_plan_says_that_is_an_output(self) -> None:
        stats = LoopStats(started_at=NOW, ticks=4)
        stats.absorb(_Run(considered=4, refused=4).to_dict())
        assert "not a failure to produce one" in stats.summary()

    def test_a_run_with_plans_does_not_say_that(self) -> None:
        stats = LoopStats(started_at=NOW, ticks=1)
        stats.absorb(_Run(considered=1, plans=1, refused=0).to_dict())
        assert "not a failure" not in stats.summary()

    def test_the_error_list_is_bounded(self) -> None:
        """An unattended day of failures must not grow without limit."""
        stats = LoopStats(started_at=NOW)
        for _ in range(50):
            stats.absorb(_Run(errors=[f"problem {i}" for i in range(20)]).to_dict())
        assert len(stats.errors) <= 200

    def test_the_stats_state_that_nothing_was_executed(self) -> None:
        note = LoopStats(started_at=NOW).to_dict()["note"]
        assert "placed no order" in note
        assert "moved no funds" in note


class _Stop(Exception):
    """Ends a test loop deterministically, without waiting on a clock."""


def _far_future():
    from aruna.core.clock import now_utc

    return now_utc() + timedelta(days=365)
