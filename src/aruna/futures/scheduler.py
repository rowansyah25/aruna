"""The futures loop (FUTURES SPEC 48).

Runs a planning cycle on a timer and writes a daily account of what it did.

**Nothing here executes anything.** It calls read-only market data, runs the
council, builds plans, and stores rows. No order is placed, no leverage or
margin setting is changed, no funds move (FUTURES SPEC 3, 50). Stopping this
process stops analysis and nothing else - there is no position for it to leave
behind.

Three properties the loop is built around:

**A failed tick does not end the run.** An unattended loop that dies on the
first network hiccup is worse than no loop, because the operator believes it is
still watching. Failures are counted, logged and carried; the loop keeps its
schedule.

**A tick that produces nothing is a tick.** The overwhelming majority will
refuse, and the count of refusals is the output SPEC 48 asks for. A loop that
only recorded its successes would describe a different system from the one that
ran.

**It stops when told.** ``until`` is honoured exactly, so a run asked for
twenty-four hours ends after twenty-four hours rather than needing to be killed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger

log = get_logger("aruna.futures.scheduler")

#: Shortest interval the loop will accept. Below this the venue's rate limits
#: become the binding constraint rather than anything about the market, and a
#: 4h horizon re-planned every minute is 240 looks at one setup.
MIN_INTERVAL_SEC = 60


@dataclass(slots=True)
class LoopStats:
    """What the run did, kept whether or not it produced anything."""

    started_at: datetime
    ticks: int = 0
    failed_ticks: int = 0
    considered: int = 0
    plans: int = 0
    refused: int = 0
    waited: int = 0
    no_signal: int = 0
    stored: int = 0
    errors: list[str] = field(default_factory=list)
    last_tick_at: datetime | None = None

    def absorb(self, counts: dict[str, Any]) -> None:
        self.considered += counts.get("considered", 0)
        self.plans += counts.get("plans", 0)
        self.refused += counts.get("refused", 0)
        self.waited += counts.get("waited", 0)
        self.no_signal += counts.get("no_signal", 0)
        self.stored += counts.get("stored", 0)
        # Bounded: an unattended day of failures must not grow without limit.
        for problem in counts.get("errors", []):
            if len(self.errors) < 200:
                self.errors.append(problem)

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": isoformat(self.started_at),
            "last_tick_at": (
                isoformat(self.last_tick_at) if self.last_tick_at else None
            ),
            "ticks": self.ticks,
            "failed_ticks": self.failed_ticks,
            "considered": self.considered,
            "plans": self.plans,
            "refused": self.refused,
            "waited": self.waited,
            "no_signal": self.no_signal,
            "stored": self.stored,
            "errors": list(self.errors),
            "note": (
                "ARUNA analysed and stored; it placed no order, changed no "
                "leverage or margin setting, and moved no funds "
                "(FUTURES SPEC 3, 50)"
            ),
        }

    def summary(self) -> str:
        if not self.ticks:
            return "no tick completed"
        head = (
            f"{self.ticks} tick(s), {self.considered} considered, "
            f"{self.plans} plan(s), {self.refused} refused, "
            f"{self.waited} waited, {self.no_signal} no-signal, "
            f"{self.stored} stored"
        )
        if self.failed_ticks:
            head += f", {self.failed_ticks} tick(s) failed"
        if not self.plans and self.considered:
            head += ". No plan was issued - an output, not a failure to produce one"
        return head


class FuturesScheduler:
    def __init__(
        self,
        *,
        service: Any,
        symbols: list[str],
        horizon: Any,
        equity: Decimal,
        interval_sec: int,
        risk_pct: Decimal | None = None,
        notifier: Any = None,
        daily_report: Any = None,
        resolver: Any = None,
    ) -> None:
        if interval_sec < MIN_INTERVAL_SEC:
            raise ArunaError(
                f"interval {interval_sec}s is below the {MIN_INTERVAL_SEC}s "
                "floor: re-planning faster than that measures the venue's rate "
                "limit rather than the market"
            )
        self._service = service
        self._symbols = symbols
        self._horizon = horizon
        self._equity = equity
        self._interval = interval_sec
        self._risk_pct = risk_pct
        self._notifier = notifier
        self._daily_report = daily_report
        self._resolver = resolver

    async def tick(self) -> dict[str, Any]:
        # Scoring comes BEFORE planning, deliberately. A tick that plans first
        # and then runs out of time leaves the backlog growing, and the backlog
        # is the part that cannot be recovered later: a plan whose horizon
        # passed unscored can still be scored from history, but only while the
        # venue still serves those bars. New plans can always wait a tick.
        if self._resolver is not None:
            try:
                resolved = await self._resolver.resolve_due()
                if resolved.scored or resolved.ghosts or resolved.tampered:
                    log.info(
                        "futures.resolved",
                        scored=resolved.scored,
                        ghosts=resolved.ghosts,
                        liquidations=resolved.liquidations,
                        tampered=resolved.tampered,
                    )
                # PASAL 14.31. Dilaporkan operator: "saat signal dikirim ke tele
                # gaada resultnya, hilang semua". Sebelum baris ini rencana
                # dikabarkan, diskor, disimpan - dan nasibnya tidak pernah
                # sampai ke siapa pun.
                #
                # Dibungkus sendiri: kabar yang gagal terkirim tidak boleh
                # membatalkan penilaian yang sudah tersimpan.
                if self._notifier is not None and resolved.results:
                    try:
                        await self._notifier.results(
                            list(resolved.results), now=now_utc()
                        )
                    except Exception:
                        log.exception("futures.result_notify_failed")
            except Exception:
                log.exception("futures.resolve_pass_failed")

        run = await self._service.plan(
            self._symbols,
            horizon=self._horizon,
            equity=self._equity,
            risk_pct=self._risk_pct,
        )

        # Notification is a side effect of the tick, never a condition of it.
        # The plans are already stored by this point, so a failure here loses a
        # message and not a record - and the loop must survive that, because a
        # notifier that can end the run makes the analysis depend on the phone
        # network.
        if self._notifier is not None:
            try:
                await self._notifier.announce(
                    list(run.plans), now=now_utc(), notes=run.notes
                )
            except Exception:
                log.exception("futures.notify_failed")

        return run.to_dict()

    async def run_until(
        self,
        until: datetime,
        *,
        stats: LoopStats | None = None,
        sleep: Any = None,
    ) -> LoopStats:
        """Plan on a timer until ``until``.

        ``sleep`` is injectable so the loop can be tested without waiting; the
        default is real time.
        """
        rest = sleep or asyncio.sleep
        stats = stats or LoopStats(started_at=now_utc())

        log.info(
            "futures.loop_started",
            symbols=",".join(self._symbols),
            interval_sec=self._interval,
            until=isoformat(until),
        )

        while now_utc() < until:
            try:
                counts = await self.tick()
                stats.absorb(counts)
                log.info(
                    "futures.tick",
                    considered=counts.get("considered", 0),
                    plans=counts.get("plans", 0),
                    refused=counts.get("refused", 0),
                    stored=counts.get("stored", 0),
                )
            except Exception as exc:
                # Deliberately broad. An unattended loop that dies on the first
                # unexpected error is worse than no loop: the operator believes
                # it is still watching, and the gap in the record looks exactly
                # like a quiet market.
                stats.failed_ticks += 1
                message = f"{type(exc).__name__}: {exc}"[:200]
                if len(stats.errors) < 200:
                    stats.errors.append(message)
                log.exception("futures.tick_failed")
            finally:
                stats.ticks += 1
                stats.last_tick_at = now_utc()

            # Ditanyakan tiap tick, tapi hanya berangkat di penutup hari WIB.
            # `PlanNotifier.daily` yang memegang syaratnya - jendela waktu,
            # sekali per hari, dan penanda yang bertahan melewati restart.
            #
            # Laporannya **tidak** dibangun di sini. Versi sebelumnya menyusun
            # laporan lengkap dari database pada setiap tick lalu menyerahkannya
            # untuk kemungkinan besar dibuang: sembilan puluh lima kali sehari
            # membaca seluruh hari untuk satu kali pengiriman. Sekarang yang
            # diserahkan adalah cara membangunnya, dan itu baru dipanggil kalau
            # laporannya memang akan dikirim.
            if self._notifier is not None and self._daily_report is not None:
                try:
                    await self._notifier.daily(self._daily_report, now=now_utc())
                except Exception:
                    log.exception("futures.daily_report_failed")

            remaining = (until - now_utc()).total_seconds()
            if remaining <= 0:
                break
            await rest(min(self._interval, remaining))

        log.info("futures.loop_finished", **{
            k: v for k, v in stats.to_dict().items() if k != "note"
        })
        return stats


def next_run_window(hours: float, *, reference: datetime | None = None) -> datetime:
    return (reference or now_utc()) + timedelta(hours=hours)


__all__ = ["MIN_INTERVAL_SEC", "FuturesScheduler", "LoopStats", "next_run_window"]
