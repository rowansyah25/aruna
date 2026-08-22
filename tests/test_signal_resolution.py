"""SignalService resolution: the freshness guard and the write sequence.

Nothing in this repo used to call :meth:`SignalService.resolve_due`,
:meth:`SignalService._resolve_one` or :meth:`SignalService._prices_during`.
``tests/test_upkeep.py`` drives a *fake* resolver, so the loop was covered and
the thing the loop calls was not - which is exactly how a freshness guard that
never held anything back shipped with a docstring promising the opposite. These
tests exist to make that class of miss impossible: they exercise the real
service against fakes that are only allowed to lie about I/O, never about
rules.

Two of the fakes deliberately enforce database constraints rather than accept
whatever they are handed:

* ``paper_results`` has ``UNIQUE KEY paper_results_signal (signal_id)`` and
  :meth:`SignalRepository.record_outcome` is a plain ``INSERT``, so a second
  write for the same signal must fail. SPEC 22 is the reason it has no
  ``ON DUPLICATE KEY UPDATE``: a scored prediction is never edited.
* ``paper_trades`` has the same unique key but
  :meth:`SignalRepository.record_trade` *does* upsert, so writing it twice is
  safe.

A fake that swallowed the duplicate would let the recovery tests pass against
code that wedges a signal permanently in production.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.enums import Decision, Horizon, Market
from aruna.core.errors import ArunaError, DatabaseError
from aruna.signals.models import LockedSignal, SignalStatus
from aruna.signals.outcome import MIN_OBSERVATIONS
from aruna.signals.service import CANDLE_FRESHNESS_FACTOR, SignalService

#: Fixed and in the past. Resolution reads ``moment``, never the wall clock, so
#: a frozen origin keeps every freshness assertion arithmetic rather than racy.
NOW = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class _Asset:
    def __init__(self, asset_id: int = 7) -> None:
        self.id = asset_id
        self.symbol = "BTC/USDT"


class _Deliberation:
    """Stands in for DeliberationService. Resolution only calls find_asset."""

    def __init__(self, asset: _Asset | None = None) -> None:
        self.asset = _Asset() if asset is None else asset

    async def find_asset(self, market: Market, symbol: str):
        return self.asset


class _MarketData:
    """Stored candles per interval.

    Mirrors :meth:`MarketDataRepository.candles`: oldest-first, and the *newest*
    ``limit`` rows rather than the oldest, because the real query orders
    descending before reversing. Getting that backwards would hide a stale
    series behind ancient rows.
    """

    def __init__(self, series: dict[Horizon, list[tuple[datetime, Decimal]]]) -> None:
        self._series = series
        self.asked: list[Horizon] = []

    def replace(
        self, series: dict[Horizon, list[tuple[datetime, Decimal]]]
    ) -> None:
        """Move the stored candles, the way a refresh does between two passes.

        The upkeep loop refreshes before every resolution pass, so "the series
        is the same both times" is the one thing a repair test must not assume:
        it is exactly what hid a repair pass rewriting ``paper_trades`` from a
        second, different reading of the same horizon.
        """
        self._series = series

    async def candles(
        self,
        asset_id: int,
        interval: Horizon,
        *,
        limit: int = 500,
        closed_only: bool = True,
    ) -> list[dict[str, object]]:
        self.asked.append(interval)
        rows = [
            {"close_time": moment, "close": price}
            for moment, price in self._series.get(interval, [])
        ]
        return rows[-limit:]


#: The statement each repository method actually runs, trimmed the way
#: ``aruna.db.pool._describe`` trims it.
_SQL_SNIPPET = {
    "record_samples": "INSERT INTO outcome_snapshots (signal_id, offset_label, ...)",
    "record_outcome": "INSERT INTO paper_results (signal_id, original_direction, ...)",
    "record_trade": "INSERT INTO paper_trades (signal_id, market_code, ...)",
    "set_status": "UPDATE signals SET status = %s, resolved_at = %s WHERE ...",
}


class _Store:
    """Stands in for SignalRepository, recording the order of every write.

    ``fail_on`` names a method that raises :class:`DatabaseError` the first
    ``fail_times`` times it is called - how a transient MySQL problem arrives at
    this layer, since :class:`aruna.db.pool.Database` re-raises every driver
    failure as one.
    """

    def __init__(
        self,
        signals: list[LockedSignal],
        *,
        fail_on: str | None = None,
        fail_times: int = 1,
        duplicate_carries_errno: bool = True,
    ) -> None:
        self._signals = {s.signal_id: s for s in signals}
        self.status = {s.signal_id: SignalStatus.LOCKED for s in signals}
        self.resolved_at: dict[str, datetime] = {}
        self.withheld_reason: dict[str, str] = {}
        self.outcomes: dict[str, object] = {}
        self.trades: dict[str, object] = {}
        self.samples: dict[str, list] = {}
        self.calls: list[str] = []
        self._fail_on = fail_on
        self._fail_left = fail_times if fail_on else 0
        self._duplicate_carries_errno = duplicate_carries_errno

    # -- reads ----------------------------------------------------------

    async def due(self, *, reference: datetime, limit: int = 50) -> list[str]:
        # The real query filters status = 'LOCKED'. A RESOLVED signal is never
        # offered again, which is why a half-written resolution that flipped the
        # status early can never be repaired.
        return [
            signal_id
            for signal_id, signal in self._signals.items()
            if self.status[signal_id] is SignalStatus.LOCKED
            and reference >= signal.resolves_at
        ][:limit]

    async def get(self, signal_id: str) -> tuple[LockedSignal, str] | None:
        signal = self._signals.get(signal_id)
        return (signal, signal.fingerprint) if signal else None

    # -- writes ---------------------------------------------------------

    def _maybe_fail(self, name: str) -> None:
        """Raise the shape :class:`Database` produces, SQL snippet included.

        The snippet matters: the service tells "already scored" apart from "the
        database is unwell" partly by the table named in the message, so a fake
        that omitted it would let a lost connection pass the discriminator in a
        test while failing it in production.
        """
        if self._fail_on == name and self._fail_left > 0:
            self._fail_left -= 1
            raise DatabaseError(
                "query failed (OperationalError: (2013, 'Lost connection to "
                f"MySQL server during query')) | sql: {_SQL_SNIPPET[name]}"
            )

    async def record_samples(self, samples: list) -> int:
        self.calls.append("record_samples")
        self._maybe_fail("record_samples")
        # outcome_snapshots upserts, so re-running a pass is harmless.
        if samples:
            self.samples[samples[0].signal_id] = list(samples)
        return len(samples)

    async def record_outcome(self, outcome) -> None:
        self.calls.append("record_outcome")
        self._maybe_fail("record_outcome")
        if outcome.signal_id in self.outcomes:
            raise _duplicate_outcome(
                outcome.signal_id, carries_errno=self._duplicate_carries_errno
            )
        self.outcomes[outcome.signal_id] = outcome

    async def record_trade(self, trade) -> None:
        self.calls.append("record_trade")
        self._maybe_fail("record_trade")
        self.trades[trade.signal_id] = trade  # upsert

    async def set_status(
        self,
        signal_id: str,
        status: SignalStatus,
        *,
        resolved_at: datetime | None = None,
        superseded_by: str | None = None,
        withheld_reason: str | None = None,
    ) -> None:
        self.calls.append(f"set_status({status.value})")
        self._maybe_fail("set_status")
        self.status[signal_id] = status
        if resolved_at is not None:
            self.resolved_at[signal_id] = resolved_at
        if withheld_reason is not None:
            self.withheld_reason[signal_id] = withheld_reason


def _duplicate_outcome(signal_id: str, *, carries_errno: bool) -> DatabaseError:
    """The error MySQL raises when paper_results already holds this signal.

    Shaped like :func:`aruna.db.pool._describe` output, because that is the only
    form the service ever sees: the driver exception is flattened into a message
    and re-raised as :class:`DatabaseError`. ``carries_errno`` toggles whether
    the driver exception survives as ``__cause__`` - it does today, but a future
    ``raise ... from None`` would leave only the text, and recovery must not
    depend on which of the two is present.
    """
    driver = Exception(
        1062,
        f"Duplicate entry '{signal_id}' for key 'paper_results.paper_results_signal'",
    )
    message = (
        f"query failed (IntegrityError: {driver}) | sql: INSERT INTO paper_results "
        "(signal_id, original_direction, ...) VALUES (...)"
    )
    error = DatabaseError(message)
    if carries_errno:
        error.__cause__ = driver
    return error


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def _signal(**overrides) -> LockedSignal:
    base = {
        "signal_id": "res0000000000001",
        "market": Market.CRYPTO,
        "symbol": "BTC/USDT",
        "horizon": Horizon.D1,
        "direction": Decision.BUY,
        "confidence": 0.7,
        "reference_price": Decimal(1000),
        "entry_price": Decimal(1000),
        "target_price": Decimal(1100),
        "expected_move_pct": 10.0,
        "locked_at": NOW,
        "as_of": NOW - timedelta(minutes=1),
        "resolves_at": NOW + timedelta(days=1),
        "reasoning": ("because the test said so",),
    }
    return LockedSignal(**(base | overrides))


def _bars(
    interval: Horizon, *, first: timedelta, count: int, start: float = 1000.0
) -> list[tuple[datetime, Decimal]]:
    """``count`` closed bars of ``interval``, the first closing at ``first``."""
    return [
        (
            NOW + first + interval.duration * i,
            Decimal(str(start + i)),
        )
        for i in range(count)
    ]


def _service(store: _Store, market_data: _MarketData) -> SignalService:
    return SignalService(
        deliberation=_Deliberation(),
        market_data=market_data,
        store=store,
        model_version="test",
    )


class TestPrediksiYangTidakBisaDinilai:
    """UNSCOREABLE: jendela yang tertutup dan sudah berlalu tanpa satu bar pun.

    Diukur pada database ini: 88 prediksi IDX dikunci hari SABTU, nol bar
    tertutup di dalam horizon mana pun, di interval mana pun. Bursa tutup
    sepanjang hidup setiap prediksi. Dibiarkan LOCKED, mereka duduk permanen di
    KEPALA antrean ``due()`` - yang mengurutkan berdasarkan resolves_at -
    dibaca ulang tiap putaran, menahan yang di belakangnya, dan dihitung
    sebagai ``awaiting_candles`` sehingga health menjanjikan antrean akan
    terkuras.

    Bahaya perbaikannya adalah kebalikannya: sebuah status "tidak bisa dinilai"
    gampang sekali berubah jadi cara menyingkirkan prediksi yang merepotkan.
    Karena itu sebagian besar test di kelas ini menguji kapan status itu TIDAK
    boleh dipakai.
    """

    @staticmethod
    def _window_kosong():
        """Sinyal 1d yang jendelanya terlewati: seri 1h ada, tapi seluruh
        barnya jatuh SETELAH akhir horizon - persis bentuk IDX hari Sabtu."""
        signal = _signal(horizon=Horizon.D1)
        market_data = _MarketData(
            {
                Horizon.H1: _bars(
                    Horizon.H1, first=timedelta(days=1, hours=1), count=30
                )
            }
        )
        return signal, market_data

    async def test_jendela_yang_terbukti_kosong_ditutup(self) -> None:
        signal, market_data = self._window_kosong()
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(
            reference=NOW + timedelta(days=3)
        )

        assert result.no_bars_in_window == 1
        assert result.unscoreable == 1
        assert store.status[signal.signal_id] is SignalStatus.UNSCOREABLE

    async def test_bukan_hasil_tidak_ada_angka_yang_dikarang(self) -> None:
        """Tidak ada outcome, sample, atau paper trade. Menilainya ke arah mana
        pun berarti mengarang angka (SPEC 4)."""
        signal, market_data = self._window_kosong()
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(
            reference=NOW + timedelta(days=3)
        )

        assert result.resolved == 0
        assert result.outcomes == []
        assert store.outcomes == {}
        assert store.trades == {}
        assert store.samples == {}
        assert signal.signal_id not in store.resolved_at

    async def test_alasannya_disimpan_supaya_bisa_diaudit(self) -> None:
        """Perubahan status yang tidak bisa diaudit adalah prediksi yang
        dibuang diam-diam."""
        signal, market_data = self._window_kosong()
        store = _Store([signal])

        await _service(store, market_data).resolve_due(reference=NOW + timedelta(days=3))

        assert store.withheld_reason[signal.signal_id]

    async def test_keluar_dari_antrean_setelah_ditutup(self) -> None:
        """Inti kerusakannya: ``due()`` hanya mengembalikan LOCKED, jadi selama
        statusnya belum berubah ia dibaca ulang selamanya."""
        signal, market_data = self._window_kosong()
        store = _Store([signal])
        service = _service(store, market_data)

        first = await service.resolve_due(reference=NOW + timedelta(days=3))
        second = await service.resolve_due(reference=NOW + timedelta(days=3))

        assert first.unscoreable == 1
        assert second.unscoreable == 0
        assert second.no_bars_in_window == 0

    # ---- kapan status ini TIDAK boleh dipakai --------------------------

    async def test_seri_yang_sekadar_tertinggal_tetap_locked(self) -> None:
        """"Tertinggal dan sedang dikejar" bukan "tidak akan pernah ada".
        Menutupnya di sini akan membuang prediksi yang candle-nya akan datang
        semenit lagi - dan SPEC 22 melarang membatalkannya kemudian."""
        signal = _signal(horizon=Horizon.D1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=6)}
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(
            reference=NOW + timedelta(days=1, hours=2)
        )

        assert result.unscoreable == 0
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

    async def test_yang_bisa_dinilai_tidak_ikut_ditutup(self) -> None:
        signal = _signal()
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(
            reference=NOW + timedelta(days=1, hours=1)
        )

        assert result.unscoreable == 0
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED

    async def test_tanpa_seri_sama_sekali_tidak_ditutup(self) -> None:
        """Nol baris bukan bukti bahwa jendelanya kosong - ia bukti bahwa
        pertanyaannya belum pernah bisa diajukan."""
        signal = _signal(horizon=Horizon.D1)
        store = _Store([signal])

        result = await _service(store, _MarketData({})).resolve_due(
            reference=NOW + timedelta(days=3)
        )

        assert result.unscoreable == 0
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

    async def test_gagal_menutup_tidak_membatalkan_batch(self) -> None:
        """Yang gagal ditutup tinggal LOCKED - keadaan yang memang sudah ia
        tempati - dan sisanya tetap dikerjakan."""
        signal, market_data = self._window_kosong()
        store = _Store([signal], fail_on="set_status", fail_times=1)

        result = await _service(store, market_data).resolve_due(
            reference=NOW + timedelta(days=3)
        )

        assert result.unscoreable == 0
        assert result.failures
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

    def test_unscoreable_diizinkan_skema(self) -> None:
        """CHECK di migrasi 0019 harus menyebut nilai yang dipakai kode ini,
        atau setiap penutupan gagal di produksi sementara test unit hijau."""
        from pathlib import Path

        sql = Path("migrations/0019_unscoreable.sql").read_text(encoding="utf-8")
        assert SignalStatus.UNSCOREABLE.value in sql
        for status in SignalStatus:
            assert status.value in sql, f"{status.value} hilang dari CHECK"


# ---------------------------------------------------------------------------
# the freshness guard (SPEC 22)
# ---------------------------------------------------------------------------


class TestFreshnessGuard:
    """The guard exists because a scored prediction can never be corrected."""

    async def test_a_fresh_fine_series_is_scored_as_a_path(self) -> None:
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        # 1h bars covering the whole horizon, still arriving at `moment`.
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 1
        assert result.awaiting_candles == 0
        # Scored from 1h, not from the horizon's own interval.
        assert result.coarsely_sampled == 0
        assert len(store.samples[signal.signal_id]) >= MIN_OBSERVATIONS
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED

    async def test_a_stale_fine_series_is_not_scored_from_the_horizon_itself(
        self,
    ) -> None:
        """The blocker: an endpoint from a coarser series is a permanent lie.

        An IDX 1d prediction whose 1h series stopped a third of the way through
        the horizon must wait. Falling through to the 1d bar scores it from a
        single close - and SPEC 22 forbids editing that afterwards, so the wrong
        number is the record forever.
        """
        signal = _signal(market=Market.IDX, symbol="BBCA")
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {
                # 1h stopped 15 hours before `moment`: far past 3x its length.
                Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=10),
                # The 1d bar for the horizon itself is present and fresh.
                Horizon.D1: [(NOW + timedelta(days=1), Decimal("915"))],
            }
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.awaiting_candles == 1
        assert result.resolved == 0
        assert result.coarsely_sampled == 0
        # Nothing at all is written on this path - the signal stays scoreable.
        assert store.calls == []
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

    async def test_a_stale_series_does_not_block_a_fresh_finer_one(self) -> None:
        """Freshness is per series, not a latch. 15m still arriving is a path."""
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {
                Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=10),
                Horizon.M15: _bars(
                    Horizon.M15, first=timedelta(minutes=15), count=100
                ),
            }
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 1
        assert result.awaiting_candles == 0
        assert result.coarsely_sampled == 0

    async def test_the_coarse_fallback_survives_when_nothing_is_stale(self) -> None:
        """Not over-corrected: an endpoint is still better than never scoring.

        With no finer series stored at all there is nothing to wait for, so the
        horizon's own bar is used and the pass says so via ``coarsely_sampled``.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData({Horizon.D1: [(NOW + timedelta(days=1), Decimal(1100))]})
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 1
        assert result.coarsely_sampled == 1
        assert result.awaiting_candles == 0

    async def test_no_stored_interval_is_reported_as_unavailable_not_awaited(
        self,
    ) -> None:
        """A 3m horizon samples only from 3m, and nothing serves 3m.

        Counted apart from ``awaiting_candles`` because no amount of waiting
        fixes it, and telling an operator to wait for a bar that will never be
        ingested is the kind of nought-means-unknown claim SPEC 4 forbids.

        Dan karena menunggu tidak memperbaikinya, prediksinya **ditutup**
        sebagai UNSCOREABLE alih-alih ditawarkan lagi pada setiap pass
        selamanya. Baris ``store.calls == []`` di sini dulu mencatat perilaku
        lama; ia bukan properti yang dijaga test ini - judul dan docstring-nya
        soal penghitung yang dipakai, bukan soal apakah sesuatu ditulis.
        """
        signal = _signal(
            horizon=Horizon.M3, resolves_at=NOW + timedelta(minutes=3)
        )
        moment = NOW + timedelta(minutes=10)
        store = _Store([signal])

        result = await _service(store, _MarketData({})).resolve_due(reference=moment)

        assert result.unavailable_interval == 1
        assert result.awaiting_candles == 0
        assert result.resolved == 0
        assert store.calls == ["set_status(UNSCOREABLE)"]
        # Ditutup, bukan diskor: tidak ada outcome, tidak ada menang, tidak ada
        # kalah.
        assert result.outcomes == []
        assert result.scored == []

    async def test_the_guard_can_be_turned_off_for_replay(self) -> None:
        """``require_fresh_candles=False`` scores from the stale series.

        The parameter had no caller and no test, so the difference it claims to
        make was unproven. Same data as the blocker test; only the flag moves.
        """
        signal = _signal(market=Market.IDX, symbol="BBCA")
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {
                Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=10),
                Horizon.D1: [(NOW + timedelta(days=1), Decimal("915"))],
            }
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(
            reference=moment, require_fresh_candles=False
        )

        assert result.resolved == 1
        assert result.awaiting_candles == 0
        assert result.coarsely_sampled == 0
        # The exact split the guard decides: 1009 is the last 1h close, 915 is
        # the lone daily bar. With the guard on this signal waits; with it off
        # it is scored from the fine series, never from the endpoint.
        final = store.samples[signal.signal_id][-1]
        assert final.price == Decimal("1009")
        assert store.outcomes[signal.signal_id].final_price == Decimal("1009")

    async def test_a_series_that_reaches_the_end_of_the_horizon_is_never_stale(
        self,
    ) -> None:
        """Waiting only makes sense while the missing bars can still arrive.

        Scored a week late, the 1h series is far behind ``moment`` - but it runs
        to ``resolves_at``, and the window is closed and in the past. Holding
        this back would be a wait with no end, which is the failure mode the
        guard is meant to prevent, not cause.
        """
        signal = _signal()
        moment = NOW + timedelta(days=5)
        covering = _bars(Horizon.H1, first=timedelta(hours=1), count=24)
        assert covering[-1][0] == signal.resolves_at
        store = _Store([signal])

        result = await _service(store, _MarketData({Horizon.H1: covering})).resolve_due(
            reference=moment
        )

        assert result.resolved == 1
        assert result.awaiting_candles == 0
        assert result.coarsely_sampled == 0

    async def test_a_series_stopping_short_of_the_horizon_still_waits(self) -> None:
        """The counterpart: one bar short of the end is still a truncated path."""
        signal = _signal()
        moment = NOW + timedelta(days=5)
        short = _bars(Horizon.H1, first=timedelta(hours=1), count=23)
        assert short[-1][0] < signal.resolves_at
        store = _Store([signal])

        result = await _service(store, _MarketData({Horizon.H1: short})).resolve_due(
            reference=moment
        )

        assert result.awaiting_candles == 1
        assert result.resolved == 0

    async def test_the_cutoff_is_three_times_the_sampling_interval(self) -> None:
        """The boundary itself, so the factor cannot drift unnoticed."""
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        # Newest 1h bar exactly at the cutoff: fresh. One second earlier: stale.
        cutoff = moment - CANDLE_FRESHNESS_FACTOR * Horizon.H1.duration

        fresh = _bars(Horizon.H1, first=timedelta(hours=1), count=22)
        assert fresh[-1][0] == cutoff
        stale = [(moment_ - timedelta(seconds=1), price) for moment_, price in fresh]

        store = _Store([signal])
        ok = await _service(store, _MarketData({Horizon.H1: fresh})).resolve_due(
            reference=moment
        )
        assert ok.resolved == 1

        store = _Store([_signal()])
        blocked = await _service(store, _MarketData({Horizon.H1: stale})).resolve_due(
            reference=moment
        )
        assert blocked.awaiting_candles == 1
        assert blocked.resolved == 0


class TestWaitsThatCannotEnd:
    """"Behind and being caught up" versus "behind and nobody's job".

    They look identical in a timestamp. IDX is the measured case: 1m is a
    sampling candidate for every IDX horizon, 5,500 IDX 1m rows are stored, and
    IDX's refresh set is (15m, 1h, 1d) - so nothing will ever move that series
    again. Treating it as merely stale held every affected prediction LOCKED
    when re-tested a day, a week and a month later.
    """

    #: An IDX session that closed most of the way through the horizon: both
    #: maintained series run to ``resolves_at`` and neither holds four
    #: observations inside it, which is what forces the endpoint fallback.
    @staticmethod
    def _idx_session() -> dict[Horizon, list[tuple[datetime, Decimal]]]:
        return {
            # Frozen at the moment of the lock: 1m is not in IDX's refresh set.
            Horizon.M1: _bars(Horizon.M1, first=timedelta(minutes=1), count=5),
            Horizon.M15: _bars(
                Horizon.M15, first=timedelta(hours=23, minutes=30), count=3
            ),
            Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=22), count=3),
        }

    async def test_a_series_nothing_refreshes_never_holds_a_prediction(self) -> None:
        signal = _signal(market=Market.IDX, symbol="BBCA")
        moment = NOW + timedelta(days=1, hours=1)
        series = self._idx_session()
        series[Horizon.D1] = [(NOW + timedelta(days=1), Decimal(1100))]
        store = _Store([signal])

        result = await _service(store, _MarketData(series)).resolve_due(
            reference=moment
        )

        assert result.resolved == 1
        assert result.coarsely_sampled == 1
        assert result.awaiting_candles == 0
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED

    async def test_a_wait_that_cannot_end_is_not_counted_as_one_that_can(
        self,
    ) -> None:
        """Held back is not the failure here; being held back *silently* is.

        Yang tidak boleh terjadi adalah menghitungnya di ``awaiting_candles``,
        yang dibaca health sebagai backlog yang akan habis sendiri.

        **Dan sekarang ia tidak lagi dibiarkan LOCKED.** Ini persis kasus 88
        prediksi IDX yang terukur di lapangan: ditahan seri 1m yang tidak dijaga
        refresh mana pun, terkunci tiga hari, dan pada setiap pass berikutnya
        duduk di kepala antrean lagi karena ``due()`` mengurutkan dengan
        ``resolves_at``.
        """
        signal = _signal(market=Market.IDX, symbol="BBCA")
        moment = NOW + timedelta(days=1, hours=1)
        store = _Store([signal])

        result = await _service(store, _MarketData(self._idx_session())).resolve_due(
            reference=moment
        )

        assert result.resolved == 0
        assert result.unavailable_interval == 1
        assert result.awaiting_candles == 0
        assert store.calls == ["set_status(UNSCOREABLE)"]
        assert store.status[signal.signal_id] is SignalStatus.UNSCOREABLE
        # Ditutup bukan diskor: win rate tidak menerima satu angka pun dari ini.
        assert result.outcomes == []
        assert result.trades == {}

    async def test_a_maintained_series_that_is_behind_still_holds_it_back(
        self,
    ) -> None:
        """The other half: this must not become "never wait for anything".

        1h *is* in IDX's refresh set, so a 1h series that stopped a third of the
        way through the horizon is a wait with an end, and scoring from the 1d
        endpoint instead would write a single close as the final price for good.
        """
        signal = _signal(market=Market.IDX, symbol="BBCA")
        moment = NOW + timedelta(days=1, hours=1)
        store = _Store([signal])
        market_data = _MarketData(
            {
                Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=10),
                Horizon.D1: [(NOW + timedelta(days=1), Decimal("915"))],
            }
        )

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.awaiting_candles == 1
        assert result.unavailable_interval == 0
        assert result.resolved == 0

    async def test_an_empty_series_does_not_hold_back_what_a_stale_one_does(
        self,
    ) -> None:
        """The documented asymmetry, pinned so the docstring is a checked claim.

        A series that exists and lags is demonstrably being written; one that
        has never produced a row for this asset offers no evidence that it ever
        will. The first postpones the endpoint fallback, the second does not.
        """
        moment = NOW + timedelta(days=1, hours=1)
        endpoint = [(NOW + timedelta(days=1), Decimal(1100))]

        empty = _Store([_signal()])
        scored = await _service(empty, _MarketData({Horizon.D1: endpoint})).resolve_due(
            reference=moment
        )
        assert scored.resolved == 1
        assert scored.coarsely_sampled == 1

        behind = _Store([_signal()])
        held = await _service(
            behind,
            _MarketData(
                {
                    Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=10),
                    Horizon.D1: endpoint,
                }
            ),
        ).resolve_due(reference=moment)
        assert held.resolved == 0
        assert held.awaiting_candles == 1


class TestHorizonsWithNothingInThem:
    """A prediction nothing can ever score is a measurement, not a silence."""

    async def test_an_empty_horizon_window_is_measured_not_left_blank(self) -> None:
        """Bars are appended forwards, so a closed past window stays empty.

        The series runs two days past the end of the horizon and holds nothing
        inside it. That is measurable and permanent, and reporting it only as
        ``no_prices`` - the same counter as "the data has not turned up yet" -
        is the nought-means-unknown error SPEC 4 and 49 forbid.
        """
        signal = _signal()
        moment = NOW + timedelta(days=3)
        market_data = _MarketData(
            {
                Horizon.H1: [
                    (NOW - timedelta(hours=2), Decimal(1000)),
                    (NOW - timedelta(hours=1), Decimal(1001)),
                    (NOW + timedelta(days=2), Decimal(1002)),
                    (NOW + timedelta(days=2, hours=1), Decimal(1003)),
                ]
            }
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 0
        assert result.no_prices == 1
        assert result.no_bars_in_window == 1
        assert any("tidak punya satu pun candle" in note for note in result.notes)
        # No *outcome* is written - scoring an empty window either way would be
        # inventing a number. But the lifecycle is closed, because leaving it
        # LOCKED says "waiting" about a window that shut in the past: `due()`
        # orders by resolves_at, so it would sit at the head of the queue for
        # ever, re-read every pass, holding up everything behind it.
        assert store.outcomes == {}
        assert store.trades == {}
        assert store.samples == {}
        assert store.calls == ["set_status(UNSCOREABLE)"]
        assert store.status[signal.signal_id] is SignalStatus.UNSCOREABLE

    async def test_the_claim_is_not_made_when_bars_could_still_arrive(self) -> None:
        """The counterpart, so the claim cannot start being made for free.

        Two observations inside the horizon is too few to score from, but the
        series stops short of the end - more can still land inside it, so this
        is an ordinary ``no_prices`` and nothing is said about permanence.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {
                Horizon.H1: [
                    (NOW + timedelta(hours=22), Decimal(1000)),
                    (NOW + timedelta(hours=23), Decimal(1001)),
                ]
            }
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.no_prices == 1
        assert result.no_bars_in_window == 0
        assert result.notes == []


class _LogSpy:
    """Records what the service told the operator, in order.

    ``interval_unavailable`` is a *reason* before it is a counter, and the
    reason only exists in the log line. A test that checks the counter alone
    passes just as happily while the log states a cause that is not true.
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, dict[str, object]]] = []

    def _record(self, event: str, **fields: object) -> None:
        self.records.append((event, fields))

    debug = info = warning = error = exception = _record

    def field(self, event: str, name: str) -> object:
        for recorded, fields in self.records:
            if recorded == event:
                return fields.get(name)
        raise AssertionError(f"{event!r} was never logged: {self.records!r}")


class TestSebabIntervalUnavailable:
    """Horizon 3m: perilakunya benar, sebab yang dilaporkan dulu tidak.

    ``sampling_intervals(M3)`` jatuh ke ``(M3,)`` - tidak ada interval tersimpan
    yang cukup halus untuk menghasilkan MIN_OBSERVATIONS di dalam tiga menit -
    dan 3m sendiri bukan anggota STORED_INTERVALS, jadi prediksi 3m masuk ke
    ``interval_unavailable``. Yang dicetak ke operator dulu berbunyi "no
    provider serves an interval this horizon can be sampled from". Sejak
    PASAL 5 itu salah: binance-spot memetakan M3 ke "3m" dan menyajikannya.
    Sebab yang benar adalah keputusan penyimpanan ARUNA, dan itu pula satu-
    satunya sebab yang bisa diubah orang yang membaca log ini (SPEC 49).
    """

    @staticmethod
    def _sinyal_3m() -> LockedSignal:
        return _signal(
            horizon=Horizon.M3,
            resolves_at=NOW + timedelta(minutes=3),
            target_price=None,
            expected_move_pct=None,
        )

    async def test_sebabnya_penyimpanan_bukan_venue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        signal = self._sinyal_3m()
        store = _Store([signal])
        spy = _LogSpy()
        monkeypatch.setattr("aruna.signals.service.log", spy)

        result = await _service(store, _MarketData({})).resolve_due(
            reference=NOW + timedelta(minutes=10)
        )

        assert result.unavailable_interval == 1
        detail = spy.field("signal.sampling_interval_unavailable", "detail")
        assert isinstance(detail, str)
        # Sebab yang benar, dengan angkanya: apa yang diminta vs apa yang disimpan.
        assert "STORED_INTERVALS" in detail
        assert "3m" in detail
        assert "storage decision, not a venue limit" in detail
        # Sebab yang salah tidak boleh kembali lewat pintu mana pun.
        assert "provider" not in detail

    async def test_prediksinya_ditutup_tanpa_diskor(self) -> None:
        """Menunggu yang tidak akan pernah selesai diakhiri, bukan diperpanjang.

        Sebelumnya prediksi ini dibiarkan LOCKED dan ditawarkan lagi pada setiap
        pass selamanya. Yang berubah adalah statusnya, bukan penilaiannya:
        tidak ada outcome, tidak ada paper trade, tidak ada menang dan tidak ada
        kalah (PASAL 11.21).
        """
        signal = self._sinyal_3m()
        store = _Store([signal])

        result = await _service(store, _MarketData({})).resolve_due(
            reference=NOW + timedelta(minutes=10)
        )

        assert result.resolved == 0
        assert result.awaiting_candles == 0
        assert result.unscoreable == 1
        assert store.calls == ["set_status(UNSCOREABLE)"]
        assert result.outcomes == []
        assert result.scored == []
        assert store.status[signal.signal_id] is SignalStatus.UNSCOREABLE


# ---------------------------------------------------------------------------
# the write sequence
# ---------------------------------------------------------------------------


class TestResolutionIsRecoverable:
    """Four writes, no transaction. Order is the only guarantee available.

    :class:`SignalRepository` exposes no connection-scoped variants, so
    ``_resolve_one`` cannot wrap its writes in ``Database.transaction()``. What
    it can do is make every interruption land in a state the next pass repairs,
    and never in the one state that is unrepairable: RESOLVED with no paper
    trade, which ``due()`` will never offer again.
    """

    async def test_the_status_flip_is_the_last_write(self) -> None:
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal])

        await _service(store, market_data).resolve_due(reference=moment)

        # The claim is first and the lifecycle flip is last. Between them the
        # writes may be in any order; outside them they may not be. Claiming
        # first is what lets a later pass tell "already scored" from "not scored
        # yet" *before* it overwrites anything with a second reading.
        assert store.calls[0] == "record_outcome"
        assert store.calls[-1] == "set_status(RESOLVED)"
        assert "record_trade" in store.calls
        assert store.calls.index("record_trade") < store.calls.index(
            "set_status(RESOLVED)"
        )

    async def test_a_failed_trade_write_leaves_the_signal_locked(self) -> None:
        """The signal must come back next pass, not sit RESOLVED without a trade."""
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal], fail_on="record_trade")

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 0
        assert result.failures, "a lost paper trade must be reported, not swallowed"
        assert store.status[signal.signal_id] is SignalStatus.LOCKED
        assert store.trades == {}
        assert "set_status(RESOLVED)" not in store.calls
        # The score was claimed before the trade was attempted, so the outcome
        # row is already there while the signal is still LOCKED. That is the
        # state the next pass finishes - and the reason it must finish *this*
        # score rather than compute a new one.
        assert signal.signal_id in store.outcomes

    async def test_a_failed_trade_write_is_repaired_by_the_next_pass(self) -> None:
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal], fail_on="record_trade")
        service = _service(store, market_data)

        await service.resolve_due(reference=moment)
        second = await service.resolve_due(reference=moment)

        assert second.resolved == 1
        assert second.failures == []
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        assert signal.signal_id in store.trades

    async def test_the_next_pass_repairs_a_scored_but_still_locked_signal(
        self,
    ) -> None:
        """The dangerous window: score claimed, status flip lost.

        ``record_outcome`` is a plain INSERT under a unique key, so a second
        attempt at scoring collides with the row the interrupted pass left
        behind. Treating that collision as fatal parks the signal at the head of
        ``due()`` - which orders by ``resolves_at`` - forever.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal], fail_on="set_status")
        service = _service(store, market_data)

        first = await service.resolve_due(reference=moment)
        assert first.resolved == 0
        assert signal.signal_id in store.outcomes  # the interrupted write
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

        second = await service.resolve_due(reference=moment)

        assert second.resolved == 1
        assert second.failures == []
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        assert signal.signal_id in store.trades

    async def test_the_recorded_outcome_is_never_rewritten(self) -> None:
        """SPEC 22: the repair completes the transition, it does not re-score."""
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal], fail_on="set_status")
        service = _service(store, market_data)

        await service.resolve_due(reference=moment)
        first_outcome = store.outcomes[signal.signal_id]

        await service.resolve_due(reference=moment)

        assert store.outcomes[signal.signal_id] is first_outcome

    async def test_a_repair_never_rewrites_the_trade_from_moved_candles(
        self,
    ) -> None:
        """The blocker: one prediction, two tables, two different numbers.

        ``paper_results`` refuses a second write and ``paper_trades`` accepts
        one, so a repair pass that re-reads the candles keeps the first score in
        one table and replaces it in the other. Nothing in either row says which
        is the record, and SPEC 22 means neither can be corrected afterwards.

        The series is moved between the two passes on purpose: the loop
        refreshes before every resolution pass, so a fixture that hands both
        passes the same candles cannot see this at all - which is precisely why
        the test above passed while the numbers disagreed.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal], fail_on="set_status")
        service = _service(store, market_data)

        first = await service.resolve_due(reference=moment)
        assert first.resolved == 0
        recorded = store.outcomes[signal.signal_id]
        trade = store.trades[signal.signal_id]
        assert recorded.final_price == Decimal("1023")
        assert trade.exit_price == recorded.final_price

        market_data.replace(
            {
                Horizon.H1: _bars(
                    Horizon.H1, first=timedelta(hours=1), count=25, start=2000.0
                )
            }
        )
        second = await service.resolve_due(reference=moment)

        assert second.resolved == 1
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        # The three records that describe this prediction still agree, and they
        # agree on the *first* score - the one that is stored (SPEC 22).
        assert store.outcomes[signal.signal_id] is recorded
        assert store.trades[signal.signal_id].exit_price == recorded.final_price
        assert store.trades[signal.signal_id].net_pnl == trade.net_pnl
        assert store.samples[signal.signal_id][-1].price == recorded.final_price

    async def test_a_score_from_a_run_that_is_gone_is_completed_not_recomputed(
        self,
    ) -> None:
        """The same window across a restart, where nothing is held in memory.

        A new process cannot read the stored score back - the repository has no
        method for it - so the only honest act is to complete the lifecycle and
        write nothing. Writing the samples and the trade from a fresh reading
        would be numbers invented next to a record that says otherwise, and the
        pass must not report an outcome it did not store either.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        store = _Store([signal], fail_on="set_status")

        first = await _service(
            store,
            _MarketData(
                {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
            ),
        ).resolve_due(reference=moment)
        assert first.resolved == 0
        recorded = store.outcomes[signal.signal_id]
        trade = store.trades[signal.signal_id]

        restarted = _service(
            store,
            _MarketData(
                {
                    Horizon.H1: _bars(
                        Horizon.H1, first=timedelta(hours=1), count=25, start=2000.0
                    )
                }
            ),
        )
        store.calls.clear()
        second = await restarted.resolve_due(reference=moment)

        assert second.resolved == 1
        assert second.outcomes == []
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        assert store.outcomes[signal.signal_id] is recorded
        assert store.trades[signal.signal_id] is trade
        assert store.calls == ["record_outcome", "set_status(RESOLVED)"]

    async def test_repair_works_without_the_driver_errno(self) -> None:
        """Recovery must not hang on ``__cause__`` surviving the re-raise.

        Driven across a restart - two services over one store - because that is
        the only path that reaches the discriminator at all. A pass that still
        holds the score in memory finishes it without going near
        ``record_outcome`` a second time, so running this on one service would
        assert nothing about the duplicate-entry handling it is named for.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store(
            [signal], fail_on="set_status", duplicate_carries_errno=False
        )

        await _service(store, market_data).resolve_due(reference=moment)
        second = await _service(store, market_data).resolve_due(reference=moment)

        assert second.resolved == 1
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED

    async def test_an_unrelated_write_failure_is_still_fatal_for_that_signal(
        self,
    ) -> None:
        """A real outage must not be mistaken for "already scored"."""
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal], fail_on="record_outcome")

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 0
        assert result.failures
        assert store.status[signal.signal_id] is SignalStatus.LOCKED

    async def test_one_unscoreable_signal_does_not_abandon_the_batch(self) -> None:
        broken = _signal(signal_id="res0000000000002")
        good = _signal(signal_id="res0000000000003")
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([broken, good], fail_on="record_trade", fail_times=1)

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 1
        assert len(result.failures) == 1

    async def test_a_failed_pass_does_not_report_a_scoring_it_did_not_do(
        self,
    ) -> None:
        """``coarsely_sampled`` counts writes that landed, not attempts.

        Retries are now normal, so a counter incremented before the writes would
        tally the same signal on every attempt - a nought-is-not-clean error in
        the other direction.
        """
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        # Only the horizon's own interval is stored, and nothing is stale, so
        # this is a genuine endpoint scoring - if it gets as far as scoring.
        market_data = _MarketData(
            {Horizon.D1: [(NOW + timedelta(days=1), Decimal(1100))]}
        )
        store = _Store([signal], fail_on="record_trade")
        service = _service(store, market_data)

        first = await service.resolve_due(reference=moment)
        assert first.resolved == 0
        assert first.coarsely_sampled == 0

        second = await service.resolve_due(reference=moment)
        assert second.resolved == 1
        assert second.coarsely_sampled == 1

    async def test_a_trade_that_cannot_be_modelled_still_resolves(self) -> None:
        """A zero entry price is not a transient fault, so retrying is pointless.

        Distinguished from a failed *write*: this one can never succeed, and
        leaving the signal LOCKED would park an unscoreable prediction at the
        head of the queue for good.
        """
        signal = _signal(entry_price=Decimal(0))
        moment = NOW + timedelta(days=1, hours=1)
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )
        store = _Store([signal])

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 1
        assert store.status[signal.signal_id] is SignalStatus.RESOLVED
        assert store.trades == {}

    async def test_a_tampered_signal_is_invalidated_not_scored(self) -> None:
        signal = _signal()
        moment = NOW + timedelta(days=1, hours=1)
        store = _Store([signal])

        async def _tampered(signal_id: str):
            return signal, "0" * 64

        store.get = _tampered  # type: ignore[method-assign]
        market_data = _MarketData(
            {Horizon.H1: _bars(Horizon.H1, first=timedelta(hours=1), count=25)}
        )

        result = await _service(store, market_data).resolve_due(reference=moment)

        assert result.resolved == 0
        assert result.failures
        assert store.status[signal.signal_id] is SignalStatus.INVALIDATED
        assert store.outcomes == {}


class TestResolveDueContract:
    async def test_a_missing_snapshot_is_reported_not_raised(self) -> None:
        signal = _signal()
        store = _Store([signal])

        async def _missing(signal_id: str):
            return None

        store.get = _missing  # type: ignore[method-assign]

        result = await _service(store, _MarketData({})).resolve_due(
            reference=NOW + timedelta(days=1, hours=1)
        )

        assert result.resolved == 0
        assert any("snapshot missing" in failure for failure in result.failures)

    async def test_an_aruna_error_from_the_store_is_caught_per_signal(self) -> None:
        signal = _signal()
        store = _Store([signal])

        async def _boom(signal_id: str):
            raise ArunaError("signals table unreachable")

        store.get = _boom  # type: ignore[method-assign]

        result = await _service(store, _MarketData({})).resolve_due(
            reference=NOW + timedelta(days=1, hours=1)
        )

        assert result.resolved == 0
        assert result.failures
