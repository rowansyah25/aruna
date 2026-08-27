"""Data quality gate (SPEC 5).

Detects stale data, duplicate ticks, missing data, timestamp mismatch,
abnormal price, abnormal spread, provider disconnect, and latency spikes.

The rule this module exists to enforce: **invalid data yields NO SIGNAL, never
a guessed one.** So the gate never repairs, interpolates, or smooths anything.
It returns a verdict, and a non-OK verdict blocks signal generation downstream.

The gate is stateful per symbol, because most of these conditions are only
visible in sequence - a price is not "abnormal" on its own, only relative to
the last one ARUNA saw.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from aruna.core.clock import IDX_CALENDAR, now_utc
from aruna.core.config import DataSettings
from aruna.core.enums import DataQuality, Market
from aruna.data.models import Candle, Quote


@dataclass(frozen=True, slots=True)
class QualityVerdict:
    quality: DataQuality
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.quality is DataQuality.OK

    @property
    def blocks_signal(self) -> bool:
        return self.quality.blocks_signal

    def __str__(self) -> str:
        return self.quality.value if not self.detail else f"{self.quality.value}: {self.detail}"


OK = QualityVerdict(DataQuality.OK)


def _first_non_finite(*fields: tuple[str, Decimal | None]) -> str | None:
    """Name the first NaN or Infinity among ``fields``, or ``None``.

    ``Decimal("NaN")`` and ``Decimal("Infinity")`` are legal constructions -
    ``Decimal(str(value))`` builds them without raising - and unlike float NaN,
    a Decimal NaN *signals* ``InvalidOperation`` on ``<``, ``<=``, ``>`` and
    ``>=`` instead of quietly answering ``False``.  So the first ordinary
    comparison in this gate raises, and nothing in the ingest loop's per-asset
    handler catches ``decimal.InvalidOperation``: one poisoned field would end
    the whole poll pass for *every* symbol, not just the one that carried it.

    Infinity is screened for the opposite reason: it compares perfectly well,
    so it raises nothing and passes everything.  ``Decimal("Infinity")`` as a
    bar's high satisfies every clause of ``is_coherent`` - it is greater than
    or equal to all of them - and is stored as an accepted price.

    Screening the fields first turns both into what they actually are - a price
    that is not a finite number is an abnormal price - so the gate answers with
    a verdict, which is the one thing it exists to always do.  Remove this and
    a NaN reaches the comparisons below it while an Infinity reaches storage.
    """
    for name, value in fields:
        if value is not None and not value.is_finite():
            return f"non-finite {name} {value}"
    return None


@dataclass(slots=True)
class SymbolState:
    """What the gate remembers between observations of one symbol."""

    last_price: Decimal | None = None
    last_provider_timestamp: datetime | None = None
    last_server_timestamp: datetime | None = None
    last_signature: tuple[object, ...] | None = None
    consecutive_duplicates: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    #: Most recent provider-minus-ARUNA clock offset, in seconds. Positive
    #: means the provider's clock runs ahead.
    clock_skew_sec: float | None = None

    def note_rejection(self, quality: DataQuality) -> None:
        self.rejections[quality.value] = self.rejections.get(quality.value, 0) + 1


class QualityGate:
    """Evaluates observations for one provider.

    Thresholds come from :class:`~aruna.core.config.DataSettings`.  The staleness
    threshold is chosen per market: an equities feed is delayed by design, so
    judging it against a crypto threshold would flag every single tick.
    """

    def __init__(
        self,
        settings: DataSettings,
        *,
        source: str,
        declared_delay_sec: int = 0,
    ) -> None:
        self._settings = settings
        self._source = source
        self._declared_delay_sec = declared_delay_sec
        self._state: dict[str, SymbolState] = {}
        self._connected = True

    # ---- state ---------------------------------------------------------

    def state_for(self, symbol: str) -> SymbolState:
        return self._state.setdefault(symbol, SymbolState())

    def reset(self, symbol: str | None = None) -> None:
        """Forget history.  Called after a reconnect, where the gap between the
        last tick and the next one is an artefact of the outage, not the market."""
        if symbol is None:
            self._state.clear()
        else:
            self._state.pop(symbol, None)

    def mark_disconnected(self) -> QualityVerdict:
        self._connected = False
        return QualityVerdict(
            DataQuality.PROVIDER_DISCONNECTED,
            f"{self._source} connection lost",
        )

    def mark_connected(self) -> None:
        self._connected = True
        # Sequence checks across an outage are meaningless.
        self.reset()

    @property
    def connected(self) -> bool:
        return self._connected

    def rejection_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for state in self._state.values():
            for key, count in state.rejections.items():
                totals[key] = totals.get(key, 0) + count
        return totals

    def observed_clock_skew_sec(self) -> float | None:
        """Largest provider-vs-ARUNA clock offset seen, in seconds.

        Surfaced by health so a drifting clock is visible as a trend rather
        than as a sudden wall of rejected ticks.
        """
        skews = [s.clock_skew_sec for s in self._state.values() if s.clock_skew_sec is not None]
        return max(skews, key=abs) if skews else None

    # ---- quotes ---------------------------------------------------------

    def evaluate_quote(self, quote: Quote, *, reference: datetime | None = None) -> QualityVerdict:
        """Judge a quote.  Checks run worst-first, and the first hit wins.

        **This order differs from PASAL 10's, and the difference is stated
        rather than left to be discovered.**  The pasal lists DUPLICATE before
        STALE; here staleness is judged first.  That is a decision, not an
        oversight, and reversing it would cost a measurement.  A quote's
        duplicate signature includes its provider timestamp, so a frozen feed
        repeats the *same* timestamp and its age grows with every poll: it
        always reaches the staleness limit eventually.  Judging duplication
        first would label an hour-old feed ``DUPLICATE`` - "we saw this N times
        in a row" - and hide the thing ARUNA actually measured, "3600s old,
        limit 60s" (SPEC 49: report what was measured, not what was inferred
        from it).  DUPLICATE keeps exactly the window where it is the only
        thing known: identical quotes arriving faster than the staleness limit,
        which is the shape a fast poll against a frozen feed makes before the
        clock catches up.

        **Sequence is checked, but not under its own name.**  A quote whose
        provider timestamp predates the last accepted one is rejected inside
        :meth:`_check_timestamp` as ``TIMESTAMP_MISMATCH``.  For a quote,
        "arrived out of order" and "carries the wrong clock" are one
        measurement read twice - the same comparison against the same field -
        so a separate verdict would only invent a second name for it.

        **Consistency is a bar property, and this gate does not enforce it.**
        :meth:`evaluate_candle` judges OHLC coherence and positive prices, and
        nothing else; the interval-span and epoch-alignment checks PASAL 10
        wants live in the adapter (:mod:`aruna.data.crypto.binance`), where the
        interval a bar claims to be is still in hand.  A quote has no span to
        be consistent with, so there is nothing here for such a check to read.
        """
        if not self._connected:
            return self._reject(quote.symbol, self.mark_disconnected())

        state = self.state_for(quote.symbol)
        now = reference or now_utc()

        # The order is load-bearing, not incidental - see the docstring above
        # before moving anything, and _check_staleness before _check_duplicate
        # in particular.  Swap those two and a feed frozen for an hour is
        # labelled DUPLICATE, and the measured "3600s old, limit 60s" never
        # reaches the verdict (SPEC 49).
        # ``tests/test_data_quality.py::TestUrutanGerbang`` pins it.
        for check in (
            self._check_price_sane,
            self._check_timestamp,
            self._check_staleness,
            self._check_latency,
            self._check_duplicate,
            self._check_spread,
            self._check_price_jump,
        ):
            verdict = check(quote, state, now)
            if not verdict.ok:
                self._remember(quote, state)
                return self._reject(quote.symbol, verdict)

        self._remember(quote, state)
        return OK

    def _reject(self, symbol: str, verdict: QualityVerdict) -> QualityVerdict:
        self.state_for(symbol).note_rejection(verdict.quality)
        return verdict

    def _remember(self, quote: Quote, state: SymbolState) -> None:
        state.last_price = quote.price
        state.last_provider_timestamp = quote.provenance.provider_timestamp
        state.last_server_timestamp = quote.provenance.server_timestamp
        state.last_signature = self._signature(quote)

    @staticmethod
    def _signature(quote: Quote) -> tuple[object, ...]:
        return (
            quote.provenance.provider_timestamp,
            quote.price,
            quote.quantity,
            quote.bid,
            quote.ask,
        )

    # ---- individual checks ---------------------------------------------

    def _check_price_sane(
        self, quote: Quote, _state: SymbolState, _now: datetime
    ) -> QualityVerdict:
        non_finite = _first_non_finite(
            ("price", quote.price), ("bid", quote.bid), ("ask", quote.ask)
        )
        if non_finite is not None:
            return QualityVerdict(DataQuality.ABNORMAL_PRICE, non_finite)
        if quote.price <= 0:
            return QualityVerdict(
                DataQuality.ABNORMAL_PRICE, f"non-positive price {quote.price}"
            )
        if quote.bid is not None and quote.ask is not None and quote.bid > quote.ask:
            return QualityVerdict(
                DataQuality.ABNORMAL_SPREAD,
                f"crossed book: bid {quote.bid} above ask {quote.ask}",
            )
        return OK

    def _check_timestamp(
        self, quote: Quote, state: SymbolState, now: datetime
    ) -> QualityVerdict:
        provider_ts = quote.provenance.provider_timestamp
        if provider_ts is None:
            return OK

        drift = (provider_ts - now).total_seconds()
        # Recorded whether or not it trips the threshold, so an operator can
        # see a growing skew before it starts rejecting ticks.
        state.clock_skew_sec = drift

        if drift > self._settings.future_tolerance_sec:
            # A future timestamp is how future-data leakage (SPEC 24) sneaks in.
            return QualityVerdict(
                DataQuality.TIMESTAMP_MISMATCH,
                f"provider timestamp is {drift:.1f}s in the future",
            )

        previous = state.last_provider_timestamp
        if previous is not None and provider_ts < previous:
            regression = (previous - provider_ts).total_seconds()
            return QualityVerdict(
                DataQuality.TIMESTAMP_MISMATCH,
                f"out-of-order: {regression:.1f}s older than the previous quote",
            )
        return OK

    def _check_staleness(
        self, quote: Quote, _state: SymbolState, now: datetime
    ) -> QualityVerdict:
        provider_ts = quote.provenance.provider_timestamp
        if provider_ts is None:
            return OK

        age = (now - provider_ts).total_seconds()
        limit = self._staleness_limit(quote.market)
        # Measured on top of the declared delay: a feed that is honestly 15
        # minutes behind is not stale at 15 minutes, it is stale at 15 + limit.
        allowance = limit + self._declared_delay_sec
        if age > allowance:
            return QualityVerdict(
                DataQuality.STALE,
                f"{age:.0f}s old, limit {allowance:.0f}s"
                + (
                    f" (feed declares {self._declared_delay_sec}s delay)"
                    if self._declared_delay_sec
                    else ""
                ),
            )
        return OK

    def staleness_limit(self, market: Market) -> float:
        """Batas basi untuk ``market``, dalam detik.

        Publik karena bukan cuma gerbang ini yang membutuhkannya: pelapor yang
        menulis "basi 900 detik, batas 660" harus menyebut angka yang SAMA
        dengan yang dipakai menolak, bukan salinannya yang bisa menyimpang.
        """
        return self._staleness_limit(market)

    def _staleness_limit(self, market: Market) -> float:
        if market is Market.IDX:
            return self._settings.stale_idx_tick_sec
        if market is Market.FOREX:
            return self._settings.stale_forex_tick_sec
        return self._settings.stale_tick_sec

    def _check_latency(
        self, quote: Quote, _state: SymbolState, _now: datetime
    ) -> QualityVerdict:
        latency = quote.provenance.latency_ms
        if latency is not None and latency > self._settings.latency_spike_ms:
            return QualityVerdict(
                DataQuality.LATENCY_SPIKE,
                f"{latency:.0f}ms round trip, limit {self._settings.latency_spike_ms:.0f}ms",
            )
        return OK

    def _check_duplicate(
        self, quote: Quote, state: SymbolState, _now: datetime
    ) -> QualityVerdict:
        if state.last_signature is None:
            return OK
        if self._signature(quote) != state.last_signature:
            # The run is broken, so reset here rather than on the success path -
            # a reset after every accepted quote would stop the counter ever
            # reaching the threshold, and a frozen feed would go unnoticed.
            state.consecutive_duplicates = 0
            return OK

        state.consecutive_duplicates += 1
        # A quiet market legitimately repeats a quote. Only a run of identical
        # observations suggests the feed has frozen rather than the market.
        if state.consecutive_duplicates < 3:
            return OK

        # Dua hal yang selama ini dinamai satu, dan menuntut tindakan yang
        # berlawanan.
        #
        # `_signature` MEMUAT stempel waktu provider, jadi tanda tangan yang
        # identik berarti stempelnya juga identik: providernya belum
        # menghasilkan observasi baru. Itu bukan cacat data - itu ukuran
        # seberapa cepat ARUNA bertanya dibandingkan seberapa cepat feed-nya
        # menjawab. Terukur pada Yahoo/IDX: 1269 panggilan per enam jam
        # menghasilkan 301 stempel berbeda, dan 1165 penolakan DUPLICATE per
        # enam jam - nol di Binance, yang berubah hampir tiap panggilan.
        #
        # Menuduh data yang baik selama ribuan kali sehari punya harga yang
        # sudah kita bayar sekali: log yang penuh satu peringatan yang sama
        # membuat peringatan sungguhan tidak terlihat.
        #
        # Feed yang benar-benar beku tetap tertangkap - stempelnya tidak
        # bergerak, umurnya tumbuh tiap poll, dan `_check_staleness` menyala
        # lebih dulu karena ia diperiksa sebelum yang ini.
        if quote.provenance.provider_timestamp is not None:
            return QualityVerdict(
                DataQuality.REPEATED_READ,
                f"observasi yang sama dibaca {state.consecutive_duplicates} kali; "
                "provider belum memperbarui stempel waktunya",
            )

        # Tanpa stempel waktu, umurnya tidak terukur dan STALE tidak akan
        # pernah menyala. Pengulangan adalah satu-satunya petunjuk yang tersisa,
        # jadi di sini ia tetap dilaporkan sebagai temuan.
        return QualityVerdict(
            DataQuality.DUPLICATE,
            f"identical quote {state.consecutive_duplicates} times in a row, "
            "and no provider timestamp to measure its age",
        )

    def _check_spread(
        self, quote: Quote, _state: SymbolState, _now: datetime
    ) -> QualityVerdict:
        spread_bps = quote.spread_bps
        if spread_bps is None:
            return OK
        if float(spread_bps) > self._settings.max_spread_bps:
            return QualityVerdict(
                DataQuality.ABNORMAL_SPREAD,
                f"{float(spread_bps):.1f} bps, limit {self._settings.max_spread_bps:.0f}",
            )
        return OK

    def _check_price_jump(
        self, quote: Quote, state: SymbolState, _now: datetime
    ) -> QualityVerdict:
        previous = state.last_price
        if previous is None or previous <= 0:
            return OK
        move_pct = abs((quote.price - previous) / previous * Decimal(100))
        if float(move_pct) > self._settings.max_price_jump_pct:
            return QualityVerdict(
                DataQuality.ABNORMAL_PRICE,
                f"{float(move_pct):.2f}% move from {previous} to {quote.price}, "
                f"limit {self._settings.max_price_jump_pct:.1f}%",
            )
        return OK

    # ---- candles --------------------------------------------------------

    def evaluate_candle(self, candle: Candle) -> QualityVerdict:
        """Judge a single bar on its own internal consistency."""
        non_finite = _first_non_finite(
            ("open", candle.open),
            ("high", candle.high),
            ("low", candle.low),
            ("close", candle.close),
            ("volume", candle.volume),
        )
        if non_finite is not None:
            return QualityVerdict(DataQuality.ABNORMAL_PRICE, non_finite)
        if not candle.is_coherent:
            return QualityVerdict(
                DataQuality.ABNORMAL_PRICE,
                f"incoherent OHLC: o={candle.open} h={candle.high} "
                f"l={candle.low} c={candle.close}",
            )
        if candle.open <= 0 or candle.close <= 0:
            return QualityVerdict(DataQuality.ABNORMAL_PRICE, "non-positive price")
        return OK


def find_candle_gaps(candles: list[Candle]) -> list[tuple[datetime, datetime, int]]:
    """Missing bars in an otherwise ordered series (SPEC 5: missing data).

    Returns ``(gap_start, gap_end, missing_count)`` per gap.  Reported, never
    filled: an interpolated candle is fabricated data, which SPEC 4 forbids and
    which would silently corrupt any backtest built on it.

    Non-trading time is not a gap.  A daily IDX series legitimately skips every
    weekend, and counting those as missing data produces a permanent wall of
    false alarms - which is worse than no detection at all, because it teaches
    an operator to ignore the real ones.  Slots outside the exchange calendar
    are therefore excluded for IDX; crypto trades continuously, so every
    absent slot there is genuine.
    """
    if len(candles) < 2:
        return []

    ordered = sorted(candles, key=lambda c: c.open_time)
    step = ordered[0].interval.duration
    market = ordered[0].market
    gaps: list[tuple[datetime, datetime, int]] = []

    for previous, current in pairwise(ordered):
        expected = previous.open_time + step
        if current.open_time <= expected:
            continue
        missing = _count_missing_slots(expected, current.open_time, step, market)
        if missing > 0:
            gaps.append((expected, current.open_time, missing))
    return gaps


def _count_missing_slots(
    start: datetime, end: datetime, step: timedelta, market: Market
) -> int:
    """Absent slots between two bars that the venue should have traded."""
    total = int((end - start).total_seconds() // step.total_seconds())
    if market is not Market.IDX or total <= 0:
        return total

    # Walk the window and keep only slots inside a trading session. Bounded by
    # `total` so a long holiday cannot turn into an expensive loop.
    missing = 0
    moment = start
    for _ in range(total):
        if IDX_CALENDAR.is_open(moment):
            missing += 1
        moment += step
    return missing


__all__ = [
    "OK",
    "QualityGate",
    "QualityVerdict",
    "SymbolState",
    "find_candle_gaps",
]
