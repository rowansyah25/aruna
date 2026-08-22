"""Keeping the stored candle series current enough to score a prediction from.

The whole of ARUNA's candle history used to arrive through ``aruna fetch`` -
a manual command. Nothing refreshed it on a schedule, so the finest intervals
simply stopped: 1m, 5m, 15m, 30m and 1h all froze at whatever minute the last
manual backfill happened to run, and outcome resolution, which reads those
bars, had nothing recent to read.

Three rules shape what this module does, and each one is a rate-limit rule as
much as a correctness one:

**Refresh a few bars, not five hundred.** A routine pass asks for one forming
bar plus a couple of closed ones. How far behind the series is decides the
rest, measured *per asset* against that asset's own newest stored bar - one
symbol that has never been fetched must not turn everybody else's three-bar
pass into a five-hundred-bar pass. What a provider then does with the count it
is handed is that adapter's contract rather than a promise made here: Binance
spot klines are count-based, so a request for three bars really is three bars
and this heading is literally true for crypto; the Yahoo IDX adapter picks its
HTTP range on its own, so read the adapter before quoting a payload size.

Binance spot caps klines at 1000 bars per request - the adapter splits anything
larger into several requests rather than silently returning a short series - so
a catch-up pass wide enough to need paging costs more than one call. Weight is
1 per klines request against a 6000/minute per-IP budget, which the numbers at
the end of this docstring stay well inside.

**Each interval has its own cadence, and it is its own length.** 1m is
refreshed once a minute, 15m once a quarter of an hour, 1d once a day, each
just after the bar it wants has closed. Refreshing every interval on every tick
would be a request bill several times larger for data that has not changed. A
failure spaces attempts out and *widens* them, never past the interval's own
length, and one dead symbol out of five does not drag the whole market down to
the retry floor - that combination is what turned a delisted pair into 28,800
requests a day where a healthy market spends 7,810.

**A closed exchange is not polled.** IDX trades roughly six hours a day, five
days a week; the rest of the time the last bar is final and re-fetching it buys
nothing. The one exception is the settlement sweep after post-trading close,
without which the day's own closing 15m and 1h bars would never be stored at
all - and the sweep is finished when those bars are *stored*, not when the gate
is opened, and not while a symbol is still missing from them. The sweep does
*not* pull 1d, because at 16:15 WIB it cannot produce a readable one; see
:meth:`CandleRefresher._sweep_yields_closed_bar` for the measurement.

One number this file owes the operator, because ``max_requests_per_cycle``
reads like a bound and is not one: the progress guarantee below lets each
market's first due interval through whatever the ceiling says, so a cycle
spends at most ``ceiling + the sum of the markets' asset counts`` provider
calls - measured, 41 assets against a ceiling of 40 spend 41, 300 spend 300,
and ``ARUNA_DATA_MAX_RETRIES`` multiplies each of those again underneath.
Starving a market is far worse than overshooting, so the guarantee stays; the
number is said out loud here and in ``upkeep.interval_starved`` rather than
left for an operator to infer from a knob labelled 40.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from aruna.core.clock import IDX_CALENDAR, isoformat, to_jakarta, to_utc
from aruna.core.config import UpkeepSettings
from aruna.core.enums import Horizon, Market, horizons_for_market
from aruna.core.logging import get_logger
from aruna.data.ingest import IngestService, MarketIngestor, idx_worth_polling
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.universe import AssetRecord
from aruna.signals.outcome import STORED_INTERVALS, sampling_intervals

log = get_logger("aruna.upkeep.candles")

#: How deep into a horizon's candidate sampling list is kept fresh.
#:
#: 2 = the first choice plus one fallback. The third candidate is only ever
#: reached when *both* better intervals fail to yield four observations, which
#: cannot happen while both are current. Refreshing the third and fourth
#: candidates would be requests for data with no reader.
SAMPLING_FALLBACK_DEPTH = 2

#: How many times ``candle_retry_sec`` may double before the interval's own
#: length takes over as the ceiling. Twelve is simply enough doublings for the
#: longest refreshed interval to reach its cap: 60s doubled twelve times is
#: nearly three days, so 1d saturates and a permanently broken daily pull costs
#: about eleven attempts a day instead of 1,440.
MAX_BACKOFF_DOUBLINGS = 12

#: How often a condition that persists repeats its warning - starvation at the
#: request ceiling, a symbol that keeps failing. Said once when it starts and
#: then every 240th pass: loud enough to be found, quiet enough not to become
#: the thing an operator filters out. A 1m interval reaches it after four
#: hours, and 1m is the only interval that could ever flood anything.
WARN_REPEAT_EVERY = 240

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: "This series has never been probed for holes", kept distinct from ``None``,
#: which is a real mark meaning "the series was empty the last time we asked".
#: Collapsing the two would make a never-fetched asset look permanently probed,
#: and its first five-hundred-bar backfill would run with gap detection off.
_UNPROBED = object()


def _idx_daily_bar_start(moment: datetime) -> datetime:
    """Start of the IDX daily bar in progress, taken from the session calendar.

    IDX daily bars are stamped at the opening auction, not at midnight UTC:
    every one of the 5,500 stored ``IDX 1d`` rows opens at 02:00 UTC (09:00
    WIB). Scheduling them from a midnight-UTC boundary makes the day's own bar
    look "already refreshed" from 07:00 WIB onwards - which is why the
    settlement sweep, the single pass that exists to capture that bar, used to
    find 1d not due and pull nothing.

    Derived from :data:`~aruna.core.clock.IDX_CALENDAR` rather than written as
    02:00 so that a change of exchange hours moves both together.
    """
    opening = IDX_CALENDAR.windows.session1_start
    local = to_jakarta(moment)
    start = local.replace(
        hour=opening.hour, minute=opening.minute, second=opening.second, microsecond=0
    )
    if local < start:
        start -= timedelta(days=1)
    return to_utc(start)


def bar_start(
    moment: datetime, interval: Horizon, *, market: Market | None = None
) -> datetime:
    """Start of the bar in progress at ``moment``, counted from the UTC epoch.

    Epoch-anchored rather than day-anchored so every interval lines up the way
    a venue publishes it: 15m bars at :00/:15/:30/:45, 1h on the hour, 1d at
    00:00 UTC. Intervals that do not divide a day evenly (1w lands on a
    Thursday, since that is what the epoch was) are not in the refresh set, and
    would need a real calendar rather than arithmetic if they ever were.

    ``market`` is that real calendar for the one case that needs it today: IDX
    daily bars, which open at the exchange's own opening auction. Left out, the
    arithmetic answer is returned - which is the right one for a venue that
    never closes.
    """
    if market is Market.IDX and interval is Horizon.D1:
        return _idx_daily_bar_start(moment)
    span = interval.duration.total_seconds()
    elapsed = (to_utc(moment) - _EPOCH).total_seconds()
    return _EPOCH + timedelta(seconds=math.floor(elapsed / span) * span)


def refresh_intervals(
    market: Market, supported: tuple[Horizon, ...]
) -> tuple[Horizon, ...]:
    """Intervals worth keeping current for a market - derived, never typed out.

    For every horizon this market can be analysed at, take the first
    :data:`SAMPLING_FALLBACK_DEPTH` intervals outcome sampling would try, keep
    the ones that are actually stored, and keep the ones this provider actually
    serves. The result today is ``(1m, 15m, 1h, 1d)`` for CRYPTO and
    ``(15m, 1h, 1d)`` for IDX.

    Two different reasons drop the rest, and they are worth telling apart. 4h
    is absent because sampling never reaches it within the depth above. 5m and
    30m *are* reached - they are the second candidate for the 5m and 30m
    horizons - and are dropped by the
    :data:`~aruna.signals.outcome.STORED_INTERVALS` filter below, which means a
    5m or 30m prediction rests entirely on 1m bars for its evidence.

    Deriving it is the point. A hand-written list would be free to fall out of
    step with :data:`~aruna.signals.outcome.STORED_INTERVALS`, and the symptom
    of that drift is not an error message: it is predictions that quietly
    cannot be scored.

    Returned finest first, which is also decay order.
    """
    wanted: list[Horizon] = []
    for horizon in horizons_for_market(market):
        for interval in sampling_intervals(horizon)[:SAMPLING_FALLBACK_DEPTH]:
            if interval not in STORED_INTERVALS or interval not in supported:
                continue
            if interval not in wanted:
                wanted.append(interval)
    wanted.sort(key=lambda interval: interval.duration)
    return tuple(wanted)


@dataclass(slots=True)
class IntervalState:
    """The schedule for one ``(market, interval)`` pair.

    ``last_success_at`` advances when the pull came back with data for at least
    one asset, and ``last_attempt_at`` on every try. Keeping them apart is what
    stops one failed daily pull from parking 1d for twenty-four hours: the
    schedule has not been satisfied, so it is still due, and the retry spacing
    is all that separates the attempts.

    ``settled_on`` is the WIB trading day whose settlement sweep this interval
    has completed - and it is only set once *every* asset answered. Recording
    it here, per interval and only after a pull actually returned, is the
    difference between "the gate was opened" and "the work was done";
    conflating the two is how the sweep used to burn its one chance per day on
    a tick where nothing was even due yet, and requiring only *one* asset to
    answer is how a single failing symbol used to lose its closing bar for the
    whole day even though the sweep window stays open until WIB midnight.
    """

    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    consecutive_failures: int = 0
    #: Consecutive cycles this interval was due and lost to the request ceiling.
    deferrals: int = 0
    #: Consecutive passes that came back with some assets missing. Counted so
    #: the warning about it can be said once rather than on every 1m pass.
    partial_passes: int = 0
    settled_on: date | None = None
    #: Sweep passes already spent on the day named by ``sweeping_on``. The sweep
    #: is the only pass that waives the once-per-bar rule, so without a counter
    #: of its own an interval that keeps coming back incomplete would be pulled
    #: on every tick from 16:15 WIB to midnight - 1,860 pulls per asset in one
    #: evening at the default tick. It spaces the retries exactly the way
    #: ``consecutive_failures`` spaces a failed pull's.
    sweep_attempts: int = 0
    #: The trading day ``sweep_attempts`` is counting, so yesterday's spacing
    #: cannot delay today's first sweep attempt.
    sweeping_on: date | None = None


@dataclass(frozen=True, slots=True)
class MarketGate:
    """Whether a market may be polled right now, and on whose behalf.

    ``intervals`` empty means the venue is shut and nothing is owed. A
    ``sweep_day`` means the gate is open *only* because that trading day's
    settlement bars have not been stored yet - so only those intervals may be
    pulled, and they are forced due regardless of where they sit in their own
    bar cadence.

    A value rather than a bool because the caller needs both halves: answering
    only "may I poll?" is what made the sweep pass ask for intervals that were
    not due and then mark the day done anyway.
    """

    intervals: tuple[Horizon, ...] = ()
    sweep_day: date | None = None


@dataclass(frozen=True, slots=True)
class _Pull:
    """One ``backfill()`` call: a bar count and the assets that need it.

    ``probe`` is where each of those assets' series stood when this pull was
    planned. It travels on the pull rather than being written at planning time
    because "we asked about holes here" is only true once ``backfill`` has
    actually returned - see :meth:`CandleRefresher._gap_probe_is_new`.
    """

    bars: int
    symbols: tuple[str, ...]
    detect_gaps: bool
    probe: tuple[tuple[str, datetime | None], ...] = ()


@dataclass(slots=True)
class RefreshResult:
    """What one refresh phase achieved. Deferred work is not lost work.

    There is deliberately no ``summary()`` here. This object used to carry its
    own Indonesian one-liner, and nothing in ``src/aruna`` ever called it: the
    loop reads the raw fields, and ``aruna upkeep`` prints
    :meth:`~aruna.upkeep.loop.UpkeepStats.summary`. Its two facts that were
    otherwise invisible - ``deferred`` and ``skipped_closed`` - now travel on
    ``UpkeepStats`` and reach the screen from there, so keeping a second
    rendering of them would be exactly the pattern this review kept finding:
    prose that is written, exported and unit-tested, and that no operator has
    ever read.
    """

    requests: int = 0
    candles: int = 0
    refreshed: list[tuple[Market, Horizon]] = field(default_factory=list)
    #: Due, but over the per-cycle request ceiling. The schedule was not
    #: advanced for these, so they come due again on the next tick.
    deferred: list[tuple[Market, Horizon]] = field(default_factory=list)
    #: Markets whose exchange was shut, so nothing was owed.
    skipped_closed: list[Market] = field(default_factory=list)
    #: Markets with no refresh set at all - a configuration fault, never a
    #: closed venue. Kept apart from ``skipped_closed`` because health names
    #: the reason, and naming the wrong one is worse than naming none (SPEC 49).
    skipped_no_intervals: list[Market] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


class CandleRefresher:
    """Pulls the few most recent bars of every interval that is due.

    Writes nothing itself. Every candle still goes through
    :meth:`~aruna.data.ingest.MarketIngestor.backfill`, which means one quality
    gate, one upsert, and one place that decides what a gap is (SPEC 4, 5).
    """

    def __init__(
        self,
        *,
        ingest: IngestService,
        store: MarketDataRepository,
        settings: UpkeepSettings,
    ) -> None:
        self._ingest = ingest
        self._store = store
        self._settings = settings
        self._intervals: dict[Market, tuple[Horizon, ...]] = {}
        self._states: dict[tuple[Market, Horizon], IntervalState] = {}
        #: Where each asset's series stood the last time this pass asked
        #: ``backfill`` to look for holes, so the same hole is not re-reported
        #: from a window that has not moved.
        self._gap_probed: dict[tuple[Market, Horizon, str], datetime | None] = {}
        #: Where each asset's series stood when a catch-up was last clamped to
        #: the ceiling, so the shortfall is said once and not every tick.
        self._capped_at: dict[tuple[Market, Horizon, int], datetime] = {}

    # ---- schedule -------------------------------------------------------

    def intervals_for(self, market: Market) -> tuple[Horizon, ...]:
        """What this market's refresh set is, resolved once and remembered."""
        cached = self._intervals.get(market)
        if cached is not None:
            return cached

        ingestor = self._ingest.ingestor(market)
        supported = (
            ingestor.provider.capabilities.supported_intervals
            if ingestor is not None
            else ()
        )
        override = self._settings.candle_interval_overrides
        if override:
            chosen = tuple(interval for interval in override if interval in supported)
            dropped = [i.value for i in override if i not in supported]
            if dropped:
                # Said out loud rather than silently narrowed: an operator who
                # asked for 4h on a provider that has none should learn that
                # from a log line, not from bars that never appear.
                log.warning(
                    "upkeep.interval_not_offered",
                    market=market.value,
                    intervals=dropped,
                    provider=ingestor.provider.name if ingestor else None,
                )
        else:
            chosen = refresh_intervals(market, supported)

        self._intervals[market] = chosen
        return chosen

    def _state(self, market: Market, interval: Horizon) -> IntervalState:
        key = (market, interval)
        state = self._states.get(key)
        if state is None:
            state = IntervalState()
            self._states[key] = state
        return state

    def is_due(
        self, market: Market, interval: Horizon, *, now: datetime, sweep: bool = False
    ) -> bool:
        """Whether this interval's newest bar is worth asking for yet.

        Due once per bar, a settle delay after the boundary. Requesting a bar
        the instant it closes frequently returns one the venue has not
        finalised, which then has to be rewritten on the following pass.

        ``sweep`` waives only the once-per-bar rule, and only for the IDX
        settlement pass: that pass exists precisely to re-pull bars that were
        already refreshed earlier in the session, because their final values
        are only fixed after post-trading closes. The settle delay and the
        retry spacing still apply - a sweep that ignored the settle delay would
        store the 16:00-16:15 WIB bar before the exchange has finished it - and
        a sweep that has already been tried today gets spacing of its own,
        because waiving the once-per-bar rule is otherwise a licence to pull on
        every tick for the eight hours the window stays open.
        """
        settings = self._settings
        start = bar_start(now, interval, market=market)
        if (now - start).total_seconds() < settings.candle_settle_sec:
            return False

        state = self._state(market, interval)
        if not sweep and state.last_success_at is not None and state.last_success_at >= start:
            return False
        if (
            sweep
            and state.sweep_attempts
            and state.last_attempt_at is not None
            and (now - state.last_attempt_at).total_seconds()
            < self._backoff(interval, state.sweep_attempts)
        ):
            return False
        # A failed interval keeps its place in the schedule and is simply spaced
        # out. That is what stops one failed 1d pull from waiting a full day.
        backing_off = (
            state.consecutive_failures
            and state.last_attempt_at is not None
            and (now - state.last_attempt_at).total_seconds()
            < self.retry_delay(market, interval)
        )
        return not backing_off

    def _backoff(self, interval: Horizon, attempts: int) -> float:
        """``candle_retry_sec`` doubled per attempt, never past one bar.

        One function for both spacings - a failed pull's and an incomplete
        sweep's - so the two cannot drift apart, and so the arithmetic is
        written once. ``attempts <= 1`` is the first retry and gets the flat
        ``candle_retry_sec``; the clamp to the interval's own length is what
        keeps a permanently broken 1d pull at about eleven attempts a day
        instead of 1,440.
        """
        doublings = min(max(attempts - 1, 0), MAX_BACKOFF_DOUBLINGS)
        delay = self._settings.candle_retry_sec * float(2**doublings)
        return min(interval.duration.total_seconds(), delay)

    def retry_delay(self, market: Market, interval: Horizon) -> float:
        """How long a failed interval waits before it is worth asking again.

        ``candle_retry_sec`` doubling per consecutive failure, capped at the
        interval's own length. Flat 60s retries were the whole of the brake
        before, and a symbol that fails for ever turned that into 1,440 daily
        pulls of a 1d bar - 720 times its natural cadence, measured. Widening
        keeps the original promise (one network blip does not park 1d for
        twenty-four hours: the first retry is still 60s later) while making a
        permanent failure cost roughly one bar-length per attempt.

        There is deliberately no ``consecutive_failures <= 0`` short-circuit.
        The only production caller is :meth:`is_due`, whose ``and`` chain never
        evaluates this unless the counter is already above zero, so that branch
        was reachable for negative values alone - a value nothing writes. The
        arithmetic answers the clean case on its own, and the answer is the
        same ``candle_retry_sec`` clamped to one bar.
        """
        return self._backoff(interval, self._state(market, interval).consecutive_failures)

    def bars_needed(
        self,
        newest: datetime | None,
        interval: Horizon,
        *,
        now: datetime,
        market: Market | None = None,
    ) -> int:
        """How many bars to ask for, given the newest one already stored.

        Measured against the last bar that *should* have closed by now, not
        against the wall clock, so a series that is up to date asks for exactly
        the overlap and a series ninety minutes behind on 1m asks for ninety
        plus the overlap.

        An empty series asks for the ceiling: there is no way to tell "never
        fetched" from "fetched and empty" without asking. Everything is clamped,
        because an asset that has been out of the universe for a year must not
        turn one tick into a half-million-bar request.
        """
        overlap = self._settings.candle_overlap_bars
        cap = self._settings.candle_max_bars
        if newest is None:
            return cap
        needed = overlap + self._bars_behind(newest, interval, now=now, market=market)
        return max(1, min(cap, needed))

    def _bars_behind(
        self,
        newest: datetime,
        interval: Horizon,
        *,
        now: datetime,
        market: Market | None = None,
    ) -> int:
        """Whole bars between the newest stored one and the last that closed."""
        span = interval.duration
        latest_expected = bar_start(now, interval, market=market) - span
        behind = (latest_expected - to_utc(newest)).total_seconds()
        if behind <= 0:
            return 0
        return math.ceil(behind / span.total_seconds())

    def _routine_bars(self) -> int:
        """What an on-cadence pass asks for: the overlap plus the new bar.

        ``is_due`` lets an interval through once per bar and the previous pass
        stored everything up to the bar that was forming then, so a routine
        pass is always exactly one bar behind. That makes ``overlap + 1``, not
        ``overlap``, the size of a pass that has found nothing new - which is
        why a "wider than the overlap" test for gap reporting never once said
        no, and rewrote the same GAP_DETECTED row every minute.
        """
        return self._settings.candle_overlap_bars + 1

    def market_gate(self, market: Market, *, now: datetime) -> MarketGate:
        """Which intervals this market is worth spending requests on right now.

        Crypto never closes, so its whole refresh set is always allowed. IDX is
        gated by the same predicate the quote poll uses, plus one exception.

        **The settlement sweep.** The day's closing 15m and 1h bars are only
        final after post-trading ends at 16:15 WIB, and by then
        :func:`~aruna.data.ingest.idx_worth_polling` is already false - it goes
        false at 16:00. Without a sweep, that day's last bars would not be
        stored until the next open, and an IDX prediction resolving overnight
        would lose its final observation. Those two intervals are the whole of
        what the sweep can deliver: 1d is filtered out by
        :meth:`_sweep_yields_closed_bar`, which explains the measurement.

        This method answers, and changes nothing. The sweep is marked done one
        interval at a time, in :meth:`_refresh_one`, once the pull has actually
        returned *complete* - so the gate stays open until the bars are in,
        instead of closing on the first tick that merely asked the question.
        That difference is the whole defect: the window opened at exactly
        16:15:00 WIB, which is the 15m boundary itself, so nothing had settled
        yet, and by 16:15:15 the day was already marked done.
        """
        intervals = self.intervals_for(market)
        if market is not Market.IDX or idx_worth_polling(now):
            return MarketGate(intervals=intervals)

        day = self._sweep_day(now)
        if day is None:
            return MarketGate()
        owed = tuple(
            interval
            for interval in intervals
            if self._state(market, interval).settled_on != day
            and self._sweep_yields_closed_bar(interval, now=now)
        )
        if not owed:
            return MarketGate()
        return MarketGate(intervals=owed, sweep_day=day)

    def _sweep_yields_closed_bar(self, interval: Horizon, *, now: datetime) -> bool:
        """Whether a sweep at ``now`` can store a bar any reader will ever see.

        Two facts decide it, and neither is a guess. The IDX adapter stamps a
        bar closed by arithmetic - ``close_time = open_time + interval.duration``
        and ``is_closed = close_time <= received`` (``data/idx/yahoo.py``) - and
        every reader on the resolution path asks for closed bars only
        (``MarketDataRepository.candles(closed_only=True)`` and
        ``newest_closed_candles``, which adds ``AND is_closed = TRUE``). So an
        interval whose newest *closed* bar still opens before today's session
        began has nothing from today to give the sweep.

        IDX 1d is exactly that interval, and this is why its leg of the sweep
        was pure cost. Its bars open at the opening auction, 02:00 UTC, so
        today's bar is only stamped closed at 02:00 UTC *tomorrow* - the next
        session's open, where the ordinary cadence pulls it anyway, with or
        without a sweep. Pulling it at 16:15 WIB stored a row with
        ``is_closed = 0`` that no reader could read: one provider call per asset
        per trading day for nothing. Overnight resolutions are not left worse
        off - ``sampling_intervals(1d)`` reaches for 1h and 15m first, and those
        two the sweep really does close.

        Derived rather than a hard-coded "skip 1d" so a change of exchange hours
        or of the stored interval set moves this with it.
        """
        session_start = _idx_daily_bar_start(now)
        newest_closed_open = (
            bar_start(now, interval, market=Market.IDX) - interval.duration
        )
        return newest_closed_open >= session_start

    def _sweep_day(self, now: datetime) -> date | None:
        """The trading day whose settlement sweep is open at ``now``.

        The window is post-trading close to the end of the WIB day rather than
        a single instant, because the bars it wants have not settled at 16:15:00
        and because a tick can be lost to a request ceiling or a database blip.
        It closes at WIB midnight on its own: the next local date is either a
        different trading day or not a trading day at all.
        """
        if not IDX_CALENDAR.is_trading_day(now):
            return None
        local = to_jakarta(now)
        if local.timetz().replace(tzinfo=None) < IDX_CALENDAR.windows.post_trading_end:
            return None
        return local.date()

    # ---- the pass -------------------------------------------------------

    async def refresh(self, *, now: datetime) -> RefreshResult:
        """One pass over every market, refreshing only what is due."""
        result = RefreshResult()
        spent = 0

        for market in self._ingest.markets:
            # ``IngestService.markets`` is ``tuple(self._ingestors)`` - the
            # lookup's own key set - so this always answers. Stated as an
            # invariant rather than guarded with an ``if ingestor is None:
            # continue``, which was a branch no test could reach and no caller
            # could produce: unreachable branches are the shape of defect this
            # module was reviewed for.
            ingestor = self._ingest.ingestor(market)
            assert ingestor is not None, f"no ingestor for {market.value}"
            gate = self.market_gate(market, now=now)
            if not gate.intervals:
                # Two different reasons produce an empty gate, and only one of
                # them is "the exchange is shut". The other is a market whose
                # refresh set came out empty - an operator override naming
                # intervals this provider does not serve, say - and that is a
                # configuration fault, not a closed venue.
                #
                # Kept apart because health speaks the difference out loud. Put
                # in one bucket, a 24/7 market with a misconfigured interval
                # list was announced as "CRYPTO dilewati karena bursa tutup",
                # which is a cause that cannot be true (SPEC 49).
                if self.intervals_for(market):
                    result.skipped_closed.append(market)
                else:
                    result.skipped_no_intervals.append(market)
                continue

            sweeping = gate.sweep_day is not None
            if sweeping:
                # A new trading day starts its sweep with a clean retry count.
                # Done here, before ``is_due`` reads it, because yesterday's
                # spacing would otherwise hold today's first attempt back - for
                # 1d that clamp is a full day, which would silence the sweep
                # entirely.
                for interval in gate.intervals:
                    state = self._state(market, interval)
                    if state.sweeping_on != gate.sweep_day:
                        state.sweeping_on = gate.sweep_day
                        state.sweep_attempts = 0
            due = [
                interval
                for interval in gate.intervals
                if self.is_due(market, interval, now=now, sweep=sweeping)
            ]
            if not due:
                continue

            try:
                assets = await ingestor.assets()
                newest = await self._store.newest_closed_candles(market, tuple(due))
            except Exception as exc:
                # Reading the universe or the newest stored bar can fail on its
                # own, and one market's database blip must not end the pass for
                # the other. Nothing is marked done here, so a sweep that lands
                # on the failing tick is still owed on the next one.
                log.exception("upkeep.market_pass_failed", market=market.value)
                result.failures.append(
                    f"{market.value}: gagal menyiapkan putaran refresh: "
                    f"{type(exc).__name__}: {exc}"[:200]
                )
                continue

            if not assets:
                result.failures.append(
                    f"{market.value}: tidak ada aset aktif; jalankan: aruna seed"
                )
                continue

            # Longest-neglected first, finest interval breaking the tie: under
            # the request ceiling the work that gets deferred should be the work
            # that has waited least.
            due.sort(key=lambda interval: self._priority(market, interval, now=now))

            # backfill() already iterates the assets, so one interval costs one
            # request per asset however the call is split. "Request" here means
            # one provider call, not one HTTP round trip: HttpFetcher retries a
            # 429 or a 502 up to ``max_retries`` times underneath, so the real
            # HTTP count can be several times this - most of all in the moment
            # the ceiling is supposed to matter.
            cost = len(assets)
            # The progress guarantee, and it is ``market_spent`` rather than
            # ``spent`` on purpose. Without any guarantee, a market holding more
            # assets than the ceiling defers *every* interval on *every* tick
            # for ever - 41 assets against the default 40 refreshed nothing in
            # 120 ticks while the loop reported healthy cycles. Written against
            # the cycle-wide total instead, the free slot belongs to whichever
            # market is iterated first and the *second* market starves in its
            # place. Six hours at CRYPTO 45 / IDX 5 against a ceiling of 40 with
            # a 60s tick, measured three ways: no guarantee CRYPTO 0 / IDX 24;
            # per cycle CRYPTO 360 / IDX 0 (deferrals 270, last_success_at
            # None); per market CRYPTO 360 / IDX 24. The middle row cured one
            # market's starvation by moving it onto the other. Per market, both
            # make progress; the ceiling still shapes everything after each
            # market's first interval.
            market_spent = 0
            for interval in due:
                state = self._state(market, interval)
                if market_spent and spent + cost > self._settings.max_requests_per_cycle:
                    # Deferred, not dropped: last_success_at is untouched, so
                    # this interval is due again on the very next tick.
                    result.deferred.append((market, interval))
                    self._note_deferral(
                        market, interval, state, cost=cost, spent=spent
                    )
                    continue
                state.deferrals = 0
                await self._refresh_one(
                    ingestor,
                    market,
                    interval,
                    newest=newest,
                    assets=assets,
                    now=now,
                    result=result,
                    sweep_day=gate.sweep_day,
                )
                spent += cost
                market_spent += cost
                result.requests += cost

        return result

    async def _refresh_one(
        self,
        ingestor: MarketIngestor,
        market: Market,
        interval: Horizon,
        *,
        newest: dict[tuple[int, str], datetime],
        assets: list[AssetRecord],
        now: datetime,
        result: RefreshResult,
        sweep_day: date | None = None,
    ) -> None:
        state = self._state(market, interval)
        problems: list[str] = []
        candles = 0
        failed_assets = 0
        pulls = self._plan(market, interval, newest=newest, assets=assets, now=now)

        for pull in pulls:
            try:
                outcome = await ingestor.backfill(
                    (interval,),
                    limit=pull.bars,
                    symbols=pull.symbols,
                    quiet=True,
                    detect_gaps=pull.detect_gaps,
                )
            except Exception as exc:
                # Deliberately broad, and it does not end the pass. One venue
                # failing must not stop the other intervals - or the other
                # market - from being refreshed on this same cycle.
                log.exception(
                    "upkeep.interval_refresh_failed",
                    market=market.value,
                    interval=interval.value,
                    symbols=list(pull.symbols),
                )
                failed_assets += len(pull.symbols)
                problems.append(f"{type(exc).__name__}: {exc}")
            else:
                candles += outcome.candles
                # backfill() reports per asset, so this counts symbols, not
                # calls - which is what decides whether the whole pair failed.
                failed_assets += min(len(outcome.failures), len(pull.symbols))
                problems.extend(outcome.failures)
                # Only now is "we asked about holes at this position" true. The
                # mark used to be written while *planning*, so a pull that blew
                # up before fetching anything still burned the probe, and the
                # retry that finally succeeded - the one carrying the whole
                # catch-up window - ran with detect_gaps off. One network blip
                # bought one gap detection, and a zero meaning "not asked" read
                # as a zero meaning "no holes" (SPEC 4).
                if pull.detect_gaps:
                    for symbol, mark in pull.probe:
                        self._gap_probed[(market, interval, symbol)] = mark

        result.candles += candles
        for problem in problems[:5]:
            result.failures.append(f"{market.value} {interval.value}: {problem}"[:200])
        state.last_attempt_at = now
        if sweep_day is not None:
            state.sweep_attempts += 1

        if failed_assets >= len(assets):
            state.consecutive_failures += 1
            state.partial_passes = 0
            return

        # At least one asset answered, so the bar was fetched and the schedule
        # is satisfied. Holding the whole (market, interval) pair back for one
        # dead symbol is what dropped every interval of a five-asset market to
        # the 60s retry floor - 1d pulled 1,440 times a day instead of once.
        # The dead symbol is still reported, every bar, through result.failures.
        state.last_success_at = now
        state.consecutive_failures = 0
        if sweep_day is not None and not failed_assets:
            # The sweep is the day's only chance at a final bar, so "some asset
            # answered" is not enough to close the gate on it: a symbol that
            # failed at 16:15 used to lose its closing bar until the next
            # session even though the window stays open until WIB midnight.
            # Left owed, the next tick tries again - spaced by
            # ``sweep_attempts`` so a symbol that is simply gone cannot turn
            # eight hours of window into one pull per tick.
            state.settled_on = sweep_day
        result.refreshed.append((market, interval))
        if failed_assets:
            state.partial_passes += 1
            if state.partial_passes == 1 or state.partial_passes % WARN_REPEAT_EVERY == 0:
                log.warning(
                    "upkeep.interval_partially_refreshed",
                    market=market.value,
                    interval=interval.value,
                    failed_assets=failed_assets,
                    assets=len(assets),
                    passes=state.partial_passes,
                    detail=(
                        "the interval keeps its bar cadence; the failing symbols "
                        "are reported every bar and do not slow the healthy ones "
                        "down"
                    ),
                )
        else:
            state.partial_passes = 0
            log.debug(
                "upkeep.interval_refreshed",
                market=market.value,
                interval=interval.value,
                bars_requested=[pull.bars for pull in pulls],
                candles=candles,
                sweep=sweep_day is not None,
            )

    def _plan(
        self,
        market: Market,
        interval: Horizon,
        *,
        newest: dict[tuple[int, str], datetime],
        assets: list[AssetRecord],
        now: datetime,
    ) -> list[_Pull]:
        """Group the assets by how many bars each of them is actually missing.

        Sizing per market instead of per asset was cheap to write and expensive
        to run: ``max()`` over the assets meant one symbol with no stored
        candles asked for the 500-bar ceiling on behalf of everybody, every
        pass, for ever - the same request count carrying a payload 167 times
        larger, and a window wide enough to re-report every old hole in it.
        Grouping costs one extra universe query per distinct size and not one
        extra provider request: ``backfill`` charges one request per asset
        either way. In the steady state every asset needs the same count and
        there is exactly one group.
        """
        cap = self._settings.candle_max_bars
        groups: dict[int, list[AssetRecord]] = {}
        for asset in assets:
            stored = newest.get((asset.id, interval.value))
            bars = self.bars_needed(stored, interval, now=now, market=market)
            if stored is not None:
                wanted = self._settings.candle_overlap_bars + self._bars_behind(
                    stored, interval, now=now, market=market
                )
                if wanted > cap:
                    self._note_capped_catchup(
                        market, interval, asset, stored, wanted=wanted, now=now
                    )
            groups.setdefault(bars, []).append(asset)

        pulls: list[_Pull] = []
        for bars, group in sorted(groups.items()):
            probe = tuple(
                (asset.symbol, newest.get((asset.id, interval.value)))
                for asset in group
            )
            pulls.append(
                _Pull(
                    bars=bars,
                    symbols=tuple(asset.symbol for asset in group),
                    detect_gaps=self._gap_probe_is_new(
                        market, interval, bars=bars, probe=probe
                    ),
                    probe=probe,
                )
            )
        return pulls

    def _gap_probe_is_new(
        self,
        market: Market,
        interval: Horizon,
        *,
        bars: int,
        probe: tuple[tuple[str, datetime | None], ...],
    ) -> bool:
        """Whether this pull can tell ``backfill`` anything new about holes.

        Two conditions, and the old code had neither working. The window has to
        be wider than a routine pass - and a routine pass is
        :meth:`_routine_bars`, one more than the overlap, so the old
        ``bars > overlap`` test was true on every single pass. And the series
        has to have *moved* since the last time this pass asked, because a
        catch-up that stays stuck (a symbol the provider will not serve, a hole
        older than the ceiling) otherwise rewrites the identical GAP_DETECTED
        row every minute, which is exactly how an operator learns to ignore the
        event. Nothing here fills a gap either way (SPEC 4).

        A predicate and nothing else. It used to record the mark as it compared,
        which made the condition "the last time this pass *intended* to ask" -
        and those two differ on exactly the pass that fails. The recording lives
        in :meth:`_refresh_one`, on the branch where ``backfill`` returned, the
        same way ``settled_on`` waits for a pull rather than for a gate.
        """
        if bars <= self._routine_bars():
            return False
        return any(
            self._gap_probed.get((market, interval, symbol), _UNPROBED) != mark
            for symbol, mark in probe
        )

    def _note_capped_catchup(
        self,
        market: Market,
        interval: Horizon,
        asset: AssetRecord,
        stored: datetime,
        *,
        wanted: int,
        now: datetime,
    ) -> None:
        """Say once that a catch-up was clamped and what stays missing.

        The pull window a provider builds is anchored to *now*, not to the
        newest stored bar, so a hole deeper than ``candle_max_bars`` is jumped
        over rather than closed: the newest stored bar leaps forward, the next
        pass looks up to date, and the missing stretch is never mentioned by
        anything. That is a silent hole, and a silence that reads as "clean" is
        the failure this project keeps having. Reported, never filled (SPEC 4).
        """
        key = (market, interval, asset.id)
        if self._capped_at.get(key) == stored:
            return
        self._capped_at[key] = stored
        cap = self._settings.candle_max_bars
        reachable = bar_start(now, interval, market=market) - interval.duration * cap
        log.warning(
            "upkeep.catchup_capped",
            market=market.value,
            interval=interval.value,
            symbol=asset.symbol,
            newest_stored=isoformat(stored),
            bars_wanted=wanted,
            bars_pulled=cap,
            unreachable_before=isoformat(reachable),
            detail=(
                "the catch-up window is anchored to now, so bars older than the "
                "ceiling are not pulled and are not filled; close it with "
                "aruna fetch"
            ),
        )

    def _note_deferral(
        self,
        market: Market,
        interval: Horizon,
        state: IntervalState,
        *,
        cost: int,
        spent: int,
    ) -> None:
        """Count a deferral, and stop calling it temporary once it is not.

        "Deferred, not dropped" is true for a cycle or two and false for ever
        after: an interval that costs more requests than the ceiling can ever
        grant is deferred on every tick, silently, while the loop keeps
        reporting healthy cycles.

        ``spent`` is carried into the warning because the ceiling is not a
        bound: each market's first due interval goes through whole, so the
        cycle this line is complaining about has usually already spent more
        than ``ceiling`` requests. An operator reading "ceiling 40" while the
        cycle spent 45 is owed both numbers, not the one that looks tidier.
        """
        state.deferrals += 1
        if state.deferrals == 2 or state.deferrals % WARN_REPEAT_EVERY == 0:
            log.warning(
                "upkeep.interval_starved",
                market=market.value,
                interval=interval.value,
                cycles_deferred=state.deferrals,
                cost=cost,
                ceiling=self._settings.max_requests_per_cycle,
                spent_this_cycle=spent,
                detail=(
                    "one interval costs one request per asset; raise "
                    "ARUNA_UPKEEP_MAX_REQUESTS_PER_CYCLE above the asset count "
                    "or this interval only ever moves when it sorts first"
                ),
            )

    def _priority(
        self, market: Market, interval: Horizon, *, now: datetime
    ) -> tuple[float, float]:
        """Sort key: most bar-lengths since a clean refresh first.

        Measured in the interval's own bars so 1m one minute late and 1d one day
        late rank the same, which is the honest comparison - both are exactly
        one bar behind.
        """
        state = self._state(market, interval)
        if state.last_success_at is None:
            missed = float("inf")
        else:
            missed = (
                now - state.last_success_at
            ).total_seconds() / interval.duration.total_seconds()
        return (-missed, interval.duration.total_seconds())

    # ---- introspection --------------------------------------------------

    def state(self) -> dict[str, dict[str, Any]]:
        """The schedule as health details. Keys are English - this is data."""
        return {
            f"{market.value}:{interval.value}": {
                "last_success_at": (
                    isoformat(entry.last_success_at) if entry.last_success_at else None
                ),
                "last_attempt_at": (
                    isoformat(entry.last_attempt_at) if entry.last_attempt_at else None
                ),
                "consecutive_failures": entry.consecutive_failures,
                "deferrals": entry.deferrals,
                "settled_on": entry.settled_on.isoformat() if entry.settled_on else None,
            }
            for (market, interval), entry in self._states.items()
        }


__all__ = [
    "MAX_BACKOFF_DOUBLINGS",
    "SAMPLING_FALLBACK_DEPTH",
    "WARN_REPEAT_EVERY",
    "CandleRefresher",
    "IntervalState",
    "MarketGate",
    "RefreshResult",
    "bar_start",
    "refresh_intervals",
]
