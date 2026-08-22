"""Health probes for the maintenance loop (SPEC 5, 49).

Two components, reading two different sources on purpose.

:class:`UpkeepCheck` reads the loop's own counters, which is the only way to
see a task that has died or a resolution queue that has stopped draining.

:class:`CandleFreshnessCheck` reads ``max(open_time)`` straight out of MySQL and
never looks at the loop at all. That is the whole point: the failure this
project keeps producing is code that runs, reports success, and reaches nothing
- a loop spinning happily while writing no rows. A component that read the
loop's statistics would call that UP. This one calls it DOWN.

That paragraph was written here first and then broken here first, which is the
uncomfortable part. Three separate ways:

* :class:`UpkeepCheck` read only ``failed_cycles``, and ``cycle()`` catches both
  phases itself, so that counter stays at zero by construction. Fifty cycles in
  which every provider call failed and no candle was stored reported **UP**;
* the freshness reference for IDX was the *previous* session's close even while
  the exchange was open, so during the only hours in which dead IDX ingestion
  can be seen at all, this component was looking at the wrong day;
* freshness collapsed every asset in a market into ``max()``, so one live symbol
  hid any number of frozen ones, and the evidence for that was not kept either.

Every rule below exists because of one of those. What is not measured is not
claimed (SPEC 4, 49): an unanswerable question is reported as unanswered, never
as clean.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

from aruna.core.clock import (
    IDX_CALENDAR,
    JAKARTA,
    isoformat,
    now_utc,
    to_jakarta,
    to_utc,
)
from aruna.core.enums import HealthStatus, Horizon, Market
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.health.models import ComponentHealth
from aruna.upkeep.loop import UpkeepLoop

#: How many days back to look for the last trading session before giving up.
#: Long enough for a run of public holidays, short enough to stay a loop.
_IDX_LOOKBACK_DAYS = 14

#: Consecutive failed attempts on one ``(market, interval)`` before it is named
#: in the health message. Three is one more than a retry pair, so a single blip
#: and its immediate retry stay quiet.
FAILING_INTERVAL_STREAK = 3

#: Consecutive cycles an interval may lose to the request ceiling before it is
#: reported. Matches the threshold the refresher warns at: "deferred, not
#: dropped" is true for a cycle or two and false for ever after.
STARVED_DEFERRALS = 2

#: Cycles the loop must have completed before "no candle has ever been stored"
#: is treated as a verdict rather than as a loop that has only just started.
MIN_CYCLES_FOR_VERDICT = 3


def last_idx_trading_instant(moment: datetime) -> datetime:
    """The most recent instant IDX was matching orders, never later than ``moment``.

    Freshness for a market that is shut most of the week cannot be measured
    from the wall clock. Measured from now, every Sunday reports the feed as
    two days broken, and an operator told the system is broken every weekend
    stops reading the message.

    But this used to return the *previous session's* close unconditionally,
    which is the opposite mistake and a much more expensive one: all through a
    trading day - the only hours in which dead IDX ingestion is visible at all
    - the reference sat hours or days in the past, so a feed that had written
    nothing since Friday compared as favourably as a perfect one. Measured on a
    Monday afternoon the reference was 70 hours behind now.

    So: while the exchange is trading, that instant is *now*; while it is shut,
    it is the end of the last window in which it traded. Both halves come from
    the same calendar, which is what makes the lunch break come out right too -
    a predicate that only knew "open or closed" would call 12:00-13:30 closed
    and hand back Friday's close, and the morning's missing bars would go
    unreported until 13:30 every single day (measured: 90 minutes of blindness
    per session, on top of the whole morning).
    """
    local = to_jakarta(moment)
    for _ in range(_IDX_LOOKBACK_DAYS):
        if IDX_CALENDAR.is_trading_day(local):
            for opens, closes in reversed(_idx_sessions(local)):
                if opens <= local:
                    return to_utc(min(closes, local))
        local = (local - timedelta(days=1)).replace(
            hour=23, minute=59, second=59, microsecond=0
        )
    return to_utc(moment)


def _idx_sessions(day: datetime) -> tuple[tuple[datetime, datetime], ...]:
    """The two regular-market windows of one WIB trading day, as instants."""
    windows = IDX_CALENDAR.windows
    is_friday = day.weekday() == 4
    s1_end = windows.session1_end_fri if is_friday else windows.session1_end_mon_thu
    s2_start = (
        windows.session2_start_fri if is_friday else windows.session2_start_mon_thu
    )

    def at(clock: Any) -> datetime:
        return day.replace(
            hour=clock.hour,
            minute=clock.minute,
            second=clock.second,
            microsecond=0,
        )

    return (
        (at(windows.session1_start), at(s1_end)),
        (at(s2_start), at(windows.session2_end)),
    )


def idx_trading_seconds(start: datetime, end: datetime) -> float:
    """Seconds the IDX regular market was open between two instants.

    Wall-clock age is meaningless for a venue that trades six hours a day, and
    it is meaningless in *both* directions. Measured from the previous close, a
    feed that died at ten in the morning looks perfect until the evening -
    that is the hole this replaces. Measured from now, the first minutes of
    every session look three days broken, because the last bar really is from
    Friday and no bar of today could exist yet; the lunch break says the same
    thing again every afternoon. An operator told the system is broken every
    morning stops reading the message, which is the failure
    :func:`last_idx_trading_instant` was written to avoid.

    Counting only open time answers the question actually being asked: has the
    exchange produced bars that were not stored? Five minutes into a session it
    is five minutes, whatever day the last bar came from.

    Crypto never closes, so it never needs this - see :func:`market_age`.
    """
    if end <= start:
        return 0.0
    first = to_jakarta(start)
    last = to_jakarta(end)
    total = 0.0
    day: date = first.date()
    while day <= last.date():
        # Anchored at midday so the date arithmetic cannot land inside a
        # transition; WIB is a fixed offset, but the anchor costs nothing.
        anchor = datetime.combine(day, IDX_CALENDAR.windows.session1_start, tzinfo=JAKARTA)
        if IDX_CALENDAR.is_trading_day(anchor):
            for opens, closes in _idx_sessions(anchor):
                lower = max(opens, first)
                upper = min(closes, last)
                if upper > lower:
                    total += (upper - lower).total_seconds()
        day += timedelta(days=1)
    return total


def market_age(closed_at: datetime, reference: datetime, *, market: Market) -> float:
    """How old a bar is, counted in the time its venue was actually trading."""
    if market is Market.IDX:
        return idx_trading_seconds(to_utc(closed_at), to_utc(reference))
    return max(0.0, (to_utc(reference) - to_utc(closed_at)).total_seconds())


def _clock_seconds(clock: Any) -> float:
    return clock.hour * 3600 + clock.minute * 60 + clock.second


def idx_session_seconds() -> float:
    """Open seconds in one regular IDX session, both halves, Monday to Thursday.

    Derived from the calendar so a change of exchange hours moves the tolerance
    with it, rather than leaving a number here that used to be right.
    """
    windows = IDX_CALENDAR.windows
    return (
        _clock_seconds(windows.session1_end_mon_thu)
        - _clock_seconds(windows.session1_start)
        + _clock_seconds(windows.session2_end)
        - _clock_seconds(windows.session2_start_mon_thu)
    )


def bar_market_seconds(interval: Horizon, *, market: Market) -> float:
    """How much *trading* time one bar of this interval covers.

    The tolerance is expressed in bar lengths, and once the age is counted in
    open time the length has to be as well. An IDX daily bar spans a session,
    not twenty-four hours: leaving it at 86,400 would have made "three bars
    late" mean thirteen trading days of silence for the very series that scores
    every 1d IDX prediction - the check loosened by the same change that fixed
    it, which is not a fix.

    Only 1d is refreshed at a day or longer today; anything coarser is scaled
    the same way and is a rough bound rather than a session count.
    """
    span = interval.duration.total_seconds()
    if market is not Market.IDX or span < 86_400.0:
        return span
    return span / 86_400.0 * idx_session_seconds()


def lag_text(seconds: float) -> str:
    """A lag an operator can read at a glance, in Indonesian."""
    total = int(max(0.0, seconds))
    days, rest = divmod(total, 86_400)
    hours, rest = divmod(rest, 3_600)
    minutes = rest // 60
    if days:
        return f"{days} hari {hours} jam"
    if hours:
        return f"{hours} jam {minutes} menit"
    if minutes:
        return f"{minutes} menit"
    return f"{total} detik"


class CandleFreshnessCheck:
    """Whether this market's stored candles are recent enough to score from."""

    def __init__(
        self,
        store: MarketDataRepository,
        *,
        market: Market,
        intervals: tuple[Horizon, ...],
        factor: float = 3.0,
        locked_horizons: Callable[[], Awaitable[list[str]]] | None = None,
        supported: tuple[Horizon, ...] | None = None,
    ) -> None:
        self._store = store
        self._market = market
        self._intervals = tuple(intervals)
        self._factor = factor
        self._locked_horizons = locked_horizons
        self._supported = supported
        self.name = f"candles:{market.value}"

    async def check(self) -> ComponentHealth:
        if not self._intervals:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message="tidak ada interval yang perlu disegarkan untuk market ini",
            )

        try:
            newest = await self._store.newest_closed_candles(self._market, self._intervals)
        except Exception as exc:  # noqa: BLE001 - a probe must never propagate
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=f"tidak bisa membaca candle terbaru: {type(exc).__name__}: {exc}",
            )

        moment = now_utc()
        reference = self._reference(moment)
        details: dict[str, object] = {
            "market": self._market.value,
            "reference": isoformat(reference),
            "tolerance_factor": self._factor,
            # Which clock the ages below are counted on. IDX ages are in open
            # trading time, so the number in the message is not the number a
            # reader would get by subtracting the timestamps themselves.
            "age_basis": (
                "idx_trading_time" if self._market is Market.IDX else "wall_clock"
            ),
            "intervals": {},
        }
        per_interval: dict[str, object] = details["intervals"]  # type: ignore[assignment]

        missing: list[Horizon] = []
        lagging: list[tuple[Horizon, float, int, int]] = []
        for interval in self._intervals:
            closes = {
                asset_id: stamp + interval.duration
                for (asset_id, code), stamp in newest.items()
                if code == interval.value
            }
            if not closes:
                missing.append(interval)
                per_interval[interval.value] = None
                continue
            # Per asset, not ``max()`` over the market. Collapsing them meant
            # one live symbol hid any number of frozen ones - and that is the
            # likeliest partial failure there is, because backfill reports its
            # failures per asset. The worst asset decides the verdict, and both
            # ends are kept in the details so the verdict can be argued with.
            ages = {
                asset_id: market_age(close, reference, market=self._market)
                for asset_id, close in closes.items()
            }
            tolerance = self._factor * bar_market_seconds(interval, market=self._market)
            stale = sorted(
                asset_id for asset_id, age in ages.items() if age > tolerance
            )
            worst = max(ages.values())
            per_interval[interval.value] = {
                "assets": len(ages),
                "newest_close": isoformat(max(closes.values())),
                "oldest_close": isoformat(min(closes.values())),
                "worst_age_sec": round(worst, 1),
                "best_age_sec": round(min(ages.values()), 1),
                "stale_assets": stale,
            }
            if stale:
                lagging.append((interval, worst, len(stale), len(ages)))

        unrefreshed, locked_error = await self._unrefreshed_locked_horizons()
        if locked_error is not None:
            details["locked_horizons_error"] = locked_error

        if missing:
            names = ", ".join(interval.value for interval in missing)
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"tidak ada candle {names} tersimpan sama sekali - "
                    "resolusi sinyal untuk horizon yang memakainya tidak mungkin. "
                    f"Jalankan: aruna fetch --market {self._market.value}"
                ),
                details=details,
            )

        notes: list[str] = []
        clock_note = " waktu perdagangan" if self._market is Market.IDX else ""
        if lagging:
            notes += [
                f"candle {interval.value} tertinggal {lag_text(age)}{clock_note} "
                f"pada {stale} dari {total} aset"
                for interval, age, stale, total in lagging
            ]
            notes.append("resolusi sinyal ditunda sampai bar menyusul")
        if locked_error is not None:
            # An unanswered question, said as one. Returning [] here made "no
            # horizon is being left behind" indistinguishable from "nobody
            # asked", and this component's whole job is to refuse that trade.
            notes.append(
                "pemeriksaan horizon sinyal LOCKED tidak bisa dijalankan "
                f"({locked_error}) - jadi tidak ada yang bisa dikatakan tentang "
                "horizon yang tidak disegarkan"
            )
        if unrefreshed:
            details["locked_horizons_not_refreshed"] = unrefreshed
            notes.append(
                "sinyal LOCKED memakai horizon "
                + ", ".join(unrefreshed)
                + " yang tidak disegarkan - evidence untuk horizon itu akan basi "
                "(setel ARUNA_UPKEEP_CANDLE_INTERVALS kalau memang dipakai)"
            )

        if notes:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                message="; ".join(notes),
                details=details,
            )

        fresh = ", ".join(interval.value for interval in self._intervals)
        return ComponentHealth(
            name=self.name,
            status=HealthStatus.UP,
            message=f"candle {fresh} mutakhir",
            details=details,
        )

    def _reference(self, moment: datetime) -> datetime:
        """What "current" is measured against right now.

        Crypto never closes, so the reference is the wall clock. IDX gets the
        last instant it was actually trading - see
        :func:`last_idx_trading_instant` for why that is neither "now" nor
        "the previous close".
        """
        if self._market is not Market.IDX:
            return moment
        return last_idx_trading_instant(moment)

    async def _unrefreshed_locked_horizons(self) -> tuple[list[str], str | None]:
        """Horizons carrying LOCKED signals that nothing keeps current.

        A prediction locked on a 4h horizon draws its evidence from 4h bars. If
        those bars are outside the refresh set they go stale under it, and the
        next verdict on that horizon is built on old evidence. Bounded to what
        the provider could actually serve, so it names something the operator
        can act on rather than every horizon that has no candles by design.

        Returns the horizons *and* why the answer is empty when it is. Swallowing
        the exception and returning ``[]`` made a failed query read exactly like
        a clean one - the discipline engine's ``clean=true`` for a question it
        never asked, reproduced in the component that exists to catch that. The
        caller records it the same way ``UpkeepCheck`` already records
        ``due_signals_error``.
        """
        if self._locked_horizons is None:
            return [], None
        try:
            codes = await self._locked_horizons()
        except Exception as exc:  # noqa: BLE001 - a probe must never propagate
            return [], f"{type(exc).__name__}: {exc}"

        unrefreshed: list[str] = []
        for code in codes:
            try:
                horizon = Horizon(code)
            except ValueError:
                continue
            if horizon in self._intervals:
                continue
            if self._supported is not None and horizon not in self._supported:
                continue
            unrefreshed.append(horizon.value)
        return sorted(set(unrefreshed)), None


class UpkeepCheck:
    """Whether the maintenance loop is alive, and whether it reaches anything.

    Two questions, not one. "Is the task running" was the only one this asked,
    and a loop whose every provider call failed answers it with a cheerful yes.
    The counters that record the failures - ``refresh_failures``,
    ``resolve_failures`` - were already being kept by ``cycle()``; nothing read
    them, and ``failed_cycles``, the one thing that *was* read, only moves when
    something outside both guarded phases throws, which is close to never.
    """

    def __init__(
        self,
        loop: UpkeepLoop | None,
        *,
        background: bool,
        due_count: Callable[[], Awaitable[int]] | None = None,
    ) -> None:
        self._loop = loop
        self._background = background
        self._due_count = due_count
        self._seen_failed_cycles = 0
        self._seen_refresh_failures = 0
        self._seen_resolve_failures = 0
        #: ``resolve_failures`` as it stood the last time a signal was actually
        #: scored. Failures above this line have accrued *since* the loop last
        #: made progress, so they are current rather than historical.
        #:
        #: The first version gated on ``not stats.resolved`` instead, and that
        #: only caught a resolver broken from the first tick. One signal scored
        #: at startup silenced the branch for the life of the process - so a
        #: resolver that died after scoring once went back to announcing UP on
        #: half the sweeps, which is the exact fault this branch was added to
        #: close. Measured: 21 UP / 19 DEGRADED against 19 failures in 20 tries.
        self._quiet_resolve_failures = 0
        self._seen_resolved = 0
        self._seen_unavailable = 0
        self.name = "upkeep"

    async def check(self) -> ComponentHealth:
        # Three states, not two - the same distinction TelegramCheck draws. A
        # one-shot CLI command deliberately runs no loop, and reporting that as
        # a fault would make every `aruna signal` run report itself broken for
        # doing exactly what it was asked to do.
        if not self._background:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message=(
                    "dinonaktifkan - perintah sekali jalan tidak menjalankan "
                    "loop periodik"
                ),
            )
        # Two states that used to share one sentence, and only one of them was
        # measured. ``self._loop`` is also None when app startup found no market
        # data provider and logged ``upkeep.not_wired`` - telling that operator
        # about an env var they never set is naming a cause that is not the
        # cause (SPEC 49). The CLI already words the ambiguity honestly at
        # ``aruna upkeep``; this follows it rather than guessing.
        if self._loop is None:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message=(
                    "tidak aktif: tidak ada provider market data yang "
                    "terkonfigurasi, atau ARUNA_UPKEEP_ENABLED=false - candle "
                    "tidak disegarkan dan sinyal jatuh tempo tidak dinilai otomatis"
                ),
            )
        if not self._loop.settings.enabled:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message=(
                    "dinonaktifkan lewat ARUNA_UPKEEP_ENABLED=false - candle "
                    "tidak disegarkan dan sinyal jatuh tempo tidak dinilai otomatis"
                ),
            )

        loop = self._loop
        stats = loop.stats
        details: dict[str, object] = {
            "stats": stats.to_dict(),
            "intervals": loop.refresher.state(),
            "resolve_limit": loop.settings.resolve_limit,
        }

        due: int | None = None
        if self._due_count is not None:
            try:
                due = await self._due_count()
            except Exception as exc:  # noqa: BLE001 - a probe must never propagate
                details["due_signals_error"] = f"{type(exc).__name__}: {exc}"
        details["due_signals"] = due

        if not loop.running:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    "loop upkeep tidak berjalan - candle tidak disegarkan dan "
                    "sinyal jatuh tempo tidak akan dinilai"
                ),
                details=details,
            )

        if stats.last_cycle_at is None:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.UP,
                message="berjalan, menunggu siklus pertama",
                details=details,
            )

        # Four ticks of slack: one missed cycle is a hiccup, four in a row is a
        # task that is stuck rather than slow.
        stale_after = loop.settings.tick_sec * 4
        age = (now_utc() - stats.last_cycle_at).total_seconds()
        if age > stale_after:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"siklus terakhir {lag_text(age)} lalu, lebih lama dari "
                    f"batas {stale_after:.0f} detik - loop macet"
                ),
                details=details,
            )

        # Deltas first, and the marks moved before any verdict returns, so an
        # early DOWN cannot leave a counter to be reported twice.
        new_failed_cycles = stats.failed_cycles - self._seen_failed_cycles
        new_refresh_failures = stats.refresh_failures - self._seen_refresh_failures
        new_resolve_failures = stats.resolve_failures - self._seen_resolve_failures
        self._seen_failed_cycles = stats.failed_cycles
        self._seen_refresh_failures = stats.refresh_failures
        self._seen_resolve_failures = stats.resolve_failures
        # `unavailable_interval` no longer gates anything - the sentence about
        # it is level-triggered on the per-pass count now - but the mark is
        # still advanced so a later delta-based reader cannot inherit a stale
        # baseline.
        self._seen_unavailable = stats.unavailable_interval
        if stats.resolved > self._seen_resolved:
            # Progress. Everything that failed before this point is history,
            # and history must not keep raising an alarm about now.
            self._seen_resolved = stats.resolved
            self._quiet_resolve_failures = stats.resolve_failures

        schedule: dict[str, dict[str, Any]] = details["intervals"]  # type: ignore[assignment]
        failing = self._streaks(schedule, "consecutive_failures", FAILING_INTERVAL_STREAK)
        starved = self._streaks(schedule, "deferrals", STARVED_DEFERRALS)

        # The scenario the module docstring promised to catch, stated as the
        # arithmetic that catches it: the loop turned, provider calls failed,
        # and not one candle was ever stored. Measured against the run rather
        # than against the gap between two health sweeps, because a healthy 1m
        # market can legitimately store nothing inside a thirty-second window.
        if (
            stats.refresh_failures
            and not stats.candles
            and stats.cycles >= MIN_CYCLES_FOR_VERDICT
        ):
            # Name the shut market when there is one. "Loop berputar tanpa
            # menyegarkan apa pun" is true either way, but it reads as a fault
            # in ARUNA, and `last_skipped_markets` - sitting in this same
            # details dict - is often the whole explanation: an exchange was
            # closed. Stating the wrong cause is the failure SPEC 49 is about.
            # Two different skips, said as two different things. Merged, a
            # 24/7 market whose interval list was misconfigured got announced
            # as "CRYPTO dilewati karena bursa tutup" - a cause that cannot be
            # true - while the real fault sat in `stats.errors` beside it.
            # Naming no cause was the old behaviour; naming the wrong one is
            # worse than either (SPEC 49).
            reasons = []
            if stats.last_skipped_markets:
                reasons.append(
                    f"{', '.join(stats.last_skipped_markets)} dilewati karena "
                    "bursa tutup"
                )
            if stats.last_skipped_no_intervals:
                reasons.append(
                    f"{', '.join(stats.last_skipped_no_intervals)} dilewati karena "
                    "tidak ada interval yang disegarkan - periksa "
                    "ARUNA_UPKEEP_CANDLE_INTERVALS"
                )
            shut = f" ({'; '.join(reasons)})" if reasons else ""
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    f"{stats.cycles} siklus berjalan, {stats.refresh_failures} "
                    "refresh gagal, dan 0 candle tersimpan - loop berputar tanpa "
                    f"menyegarkan apa pun{shut}"
                ),
                details=details,
            )
        if failing and len(failing) == len(schedule):
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DOWN,
                message=(
                    "tidak satu pun interval berhasil disegarkan: "
                    + self._streak_text(failing)
                ),
                details=details,
            )

        problems: list[str] = []
        if new_failed_cycles > 0:
            problems.append(f"{new_failed_cycles} siklus gagal sejak pemeriksaan terakhir")
        if new_refresh_failures > 0:
            problems.append(
                f"{new_refresh_failures} refresh gagal sejak pemeriksaan terakhir"
            )
        if new_resolve_failures > 0:
            problems.append(
                f"{new_resolve_failures} resolusi gagal sejak pemeriksaan terakhir"
            )
        elif (
            stats.resolve_enabled
            and stats.resolve_failures > self._quiet_resolve_failures
            and stats.cycles >= MIN_CYCLES_FOR_VERDICT
        ):
            # Level-triggered, matching the refresh side above. The delta test
            # alone only fires on sweeps that happen to straddle a failure, and
            # health sweeps every 30s against a 60s resolve cadence - so half of
            # them saw no new failure and reported UP "0 sinyal dinilai" for a
            # resolver that had thrown on all twenty of its attempts. A fault
            # that is still true has to still be said.
            problems.append(
                f"{stats.resolve_failures - self._quiet_resolve_failures} resolusi "
                "gagal sejak terakhir kali ada sinyal yang berhasil dinilai"
            )
        if failing:
            problems.append(
                "interval gagal berturut-turut: " + self._streak_text(failing)
            )
        if starved:
            problems.append(
                "interval selalu ditunda karena batas request: "
                + self._streak_text(starved)
                + " - naikkan ARUNA_UPKEEP_MAX_REQUESTS_PER_CYCLE"
            )
        if stats.clogged_passes >= 3:
            # Say what the queue is made of, not only that it is not moving.
            # The SPEC 22 freshness guard now holds a stale series back instead
            # of scoring it from a single close, so a backlog waiting for
            # candles to catch up raises this counter exactly like a backlog
            # that will never clear - and the two need opposite responses from
            # the operator: one is "wait", the other is "act". All three
            # figures are counted, so naming them claims nothing extra.
            problems.append(
                f"{stats.clogged_passes} putaran resolusi tersumbat - antrean sinyal "
                f"jatuh tempo tidak berkurang (putaran terakhir: "
                f"{stats.last_awaiting_candles} menunggu candle menyusul, "
                f"{stats.last_no_prices} tanpa harga tersimpan, "
                f"{stats.last_unavailable_interval} tanpa interval yang bisa disampel)"
            )
        if stats.last_unavailable_interval:
            # Gated on the SAME quantity it speaks. It used to be gated on a
            # delta over the cumulative observation counter while printing the
            # per-pass signal count - two different measures on one sentence -
            # and the two came apart whenever more than one resolve pass fell
            # between two sweeps that reached this block. The "loop macet"
            # verdict above returns early, so the delta mark freezes during an
            # incident and thaws wrong the moment the loop recovers: ARUNA
            # announced "0 sinyal tidak punya interval yang bisa disampel -
            # antrean itu tidak akan terkuras sendiri". A nought spoken as a
            # fault, about a queue that had just drained (SPEC 4, 49).
            problems.append(
                f"{stats.last_unavailable_interval} sinyal tidak punya interval yang "
                "bisa disampel - antrean itu tidak akan terkuras sendiri"
            )
        if due is not None and due > loop.settings.resolve_limit:
            # Only promise draining when draining is what will happen. A
            # backlog that is partly unscoreable for ever needs an operator,
            # and "perlu beberapa putaran" is a claim about the future.
            # `last_unavailable_interval`, never the run total: this sentence
            # says "N of the M due signals", so both numbers have to be counts
            # of signals. The run total counts observations - the same stuck
            # prediction re-read every pass - and printing it here produced
            # "10 sinyal jatuh tempo ... 36 di antaranya".
            tail = (
                f"{stats.last_unavailable_interval} di antaranya tidak punya interval "
                "yang bisa disampel dan tidak akan terkuras tanpa tindakan"
                if stats.last_unavailable_interval
                else "backlog perlu beberapa putaran untuk dikuras"
            )
            problems.append(
                f"{due} sinyal jatuh tempo, di atas batas "
                f"{loop.settings.resolve_limit} per putaran - {tail}"
            )

        # Said in both branches: with resolution switched off, "0 sinyal
        # dinilai" is not a measurement, it is a question nobody asked.
        switch = (
            ""
            if loop.settings.resolve_enabled
            else "; resolusi otomatis dimatikan lewat "
            "ARUNA_UPKEEP_RESOLVE_ENABLED=false"
        )

        if problems:
            return ComponentHealth(
                name=self.name,
                status=HealthStatus.DEGRADED,
                message="; ".join(problems) + switch,
                details=details,
            )

        scored = (
            f"{stats.resolved} sinyal dinilai"
            if loop.settings.resolve_enabled
            else "resolusi otomatis dimatikan lewat ARUNA_UPKEEP_RESOLVE_ENABLED=false"
        )
        return ComponentHealth(
            name=self.name,
            status=HealthStatus.UP,
            message=f"{stats.cycles} siklus, terakhir {lag_text(age)} lalu, {scored}",
            details=details,
        )

    @staticmethod
    def _streaks(
        schedule: dict[str, dict[str, Any]], field: str, threshold: int
    ) -> dict[str, int]:
        """``(market, interval)`` pairs stuck on one counter, and how far.

        Read defensively from :meth:`~aruna.upkeep.candles.CandleRefresher.state`
        because that mapping is health *details* - data, shaped by another
        module. A missing key must degrade this to silence, never to a crash in
        the component whose job is to still be answering when things break.
        """
        found: dict[str, int] = {}
        for key, entry in schedule.items():
            try:
                value = int(entry.get(field) or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if value >= threshold:
                found[key] = value
        return found

    @staticmethod
    def _streak_text(streaks: dict[str, int]) -> str:
        return ", ".join(f"{key} ({count}x)" for key, count in sorted(streaks.items()))


__all__ = [
    "FAILING_INTERVAL_STREAK",
    "MIN_CYCLES_FOR_VERDICT",
    "STARVED_DEFERRALS",
    "CandleFreshnessCheck",
    "UpkeepCheck",
    "bar_market_seconds",
    "idx_session_seconds",
    "idx_trading_seconds",
    "lag_text",
    "last_idx_trading_instant",
    "market_age",
]
