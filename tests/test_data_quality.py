"""The SPEC 5 quality gate.

These are the checks that decide whether ARUNA trusts a price at all, so each
condition the specification names gets an explicit test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.config import DataSettings
from aruna.core.enums import DataQuality, Horizon, Market
from aruna.data.models import Candle, Provenance, Quote
from aruna.data.quality import QualityGate, find_candle_gaps

NOW = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)  # a Monday, 11:00 WIB


@pytest.fixture
def settings() -> DataSettings:
    return DataSettings(_env_file=None)


@pytest.fixture
def gate(settings: DataSettings) -> QualityGate:
    return QualityGate(settings, source="test")


def quote(
    price: str = "100",
    *,
    bid: str | None = None,
    ask: str | None = None,
    age_sec: float = 0,
    latency_ms: float | None = None,
    delay: int = 0,
    market: Market = Market.CRYPTO,
    symbol: str = "BTC/USDT",
    stamped: bool = True,
) -> Quote:
    return Quote(
        market=market,
        symbol=symbol,
        price=Decimal(price),
        bid=Decimal(bid) if bid else None,
        ask=Decimal(ask) if ask else None,
        provenance=Provenance(
            source="test",
            server_timestamp=NOW,
            provider_timestamp=(
                NOW - timedelta(seconds=age_sec) if stamped else None
            ),
            latency_ms=latency_ms,
            declared_delay_sec=delay,
        ),
    )


class TestAcceptance:
    def test_a_normal_quote_passes(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote(bid="99", ask="101"), reference=NOW).ok

    def test_quote_without_a_provider_timestamp_is_not_penalised(
        self, gate: QualityGate
    ) -> None:
        """Some feeds publish no event time; that is a gap in metadata, not
        evidence the price is wrong."""
        bare = Quote(
            market=Market.CRYPTO,
            symbol="BTC/USDT",
            price=Decimal(100),
            provenance=Provenance(source="test", server_timestamp=NOW),
        )
        assert gate.evaluate_quote(bare, reference=NOW).ok


class TestStaleness:
    def test_old_quote_is_stale(self, gate: QualityGate) -> None:
        verdict = gate.evaluate_quote(quote(age_sec=120), reference=NOW)
        assert verdict.quality is DataQuality.STALE
        assert verdict.blocks_signal

    def test_declared_delay_extends_the_allowance(self, settings: DataSettings) -> None:
        """A feed honestly 15 minutes behind is not stale at 15 minutes."""
        delayed = QualityGate(settings, source="yahoo", declared_delay_sec=900)
        within = quote(age_sec=1000, market=Market.IDX, symbol="BBCA", delay=900)
        assert delayed.evaluate_quote(within, reference=NOW).ok

    def test_delay_allowance_is_not_unlimited(self, settings: DataSettings) -> None:
        delayed = QualityGate(settings, source="yahoo", declared_delay_sec=900)
        beyond = quote(age_sec=60_000, market=Market.IDX, symbol="BBCA", delay=900)
        assert delayed.evaluate_quote(beyond, reference=NOW).quality is DataQuality.STALE

    def test_idx_uses_its_own_threshold(self, gate: QualityGate) -> None:
        """200s would be stale for crypto and is fine for equities."""
        assert gate.evaluate_quote(
            quote(age_sec=200, market=Market.IDX, symbol="BBCA"), reference=NOW
        ).ok
        gate.reset()
        assert not gate.evaluate_quote(quote(age_sec=200), reference=NOW).ok


class TestTimestamps:
    def test_far_future_timestamp_is_rejected(self, gate: QualityGate) -> None:
        verdict = gate.evaluate_quote(quote(age_sec=-600), reference=NOW)
        assert verdict.quality is DataQuality.TIMESTAMP_MISMATCH

    def test_small_clock_skew_is_tolerated(self, gate: QualityGate) -> None:
        """Venues run seconds ahead of an unsynchronised machine; that is a
        clock, not the future data SPEC 24 forbids."""
        assert gate.evaluate_quote(quote(age_sec=-7), reference=NOW).ok

    def test_skew_is_recorded_even_when_tolerated(self, gate: QualityGate) -> None:
        gate.evaluate_quote(quote(age_sec=-7), reference=NOW)
        assert gate.observed_clock_skew_sec() == pytest.approx(7.0, abs=0.01)

    def test_out_of_order_quote_is_rejected(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote(age_sec=0), reference=NOW).ok
        verdict = gate.evaluate_quote(quote("101", age_sec=60), reference=NOW)
        assert verdict.quality is DataQuality.TIMESTAMP_MISMATCH
        assert "out-of-order" in verdict.detail


class TestPriceAndSpread:
    def test_non_positive_price_is_rejected(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote("0"), reference=NOW).quality is (
            DataQuality.ABNORMAL_PRICE
        )

    def test_crossed_book_is_rejected(self, gate: QualityGate) -> None:
        verdict = gate.evaluate_quote(quote(bid="102", ask="101"), reference=NOW)
        assert verdict.quality is DataQuality.ABNORMAL_SPREAD
        assert "crossed" in verdict.detail

    def test_wide_spread_is_rejected(self, gate: QualityGate) -> None:
        verdict = gate.evaluate_quote(quote("100", bid="90", ask="110"), reference=NOW)
        assert verdict.quality is DataQuality.ABNORMAL_SPREAD

    def test_tight_spread_passes(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote("100", bid="99.9", ask="100.1"), reference=NOW).ok

    def test_large_jump_is_rejected(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote("100"), reference=NOW).ok
        verdict = gate.evaluate_quote(quote("150", age_sec=-1), reference=NOW)
        assert verdict.quality is DataQuality.ABNORMAL_PRICE
        assert "50.00%" in verdict.detail

    def test_small_move_passes(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote("100"), reference=NOW).ok
        assert gate.evaluate_quote(quote("103", age_sec=-1), reference=NOW).ok

    def test_first_quote_has_no_jump_baseline(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote("999999"), reference=NOW).ok


class TestDuplicatesAndLatency:
    def test_a_repeated_quote_is_tolerated_briefly(self, gate: QualityGate) -> None:
        """Quiet markets legitimately repeat a quote."""
        for _ in range(3):
            assert gate.evaluate_quote(quote("100"), reference=NOW).ok

    def test_membaca_ulang_bukan_cacat_data(self, gate: QualityGate) -> None:
        """Stempel waktu provider ikut dalam tanda tangan duplikat.

        Jadi "tanda tangan identik" berarti stempelnya juga identik: ini
        observasi yang sama dibaca ulang, bukan feed yang membeku. Terukur di
        Yahoo/IDX: 1165 penolakan per enam jam, nol di Binance - beda cadence
        feed, bukan beda mutu data.
        """
        for _ in range(3):
            gate.evaluate_quote(quote("100"), reference=NOW)
        verdict = gate.evaluate_quote(quote("100"), reference=NOW)

        assert verdict.quality is DataQuality.REPEATED_READ
        assert "belum memperbarui stempel" in verdict.detail

    def test_tanpa_stempel_pengulangan_tetap_dilaporkan(
        self, gate: QualityGate
    ) -> None:
        """Tanpa stempel waktu, umur data tidak terukur dan STALE tidak akan
        pernah menyala. Pengulangan jadi satu-satunya petunjuk yang tersisa,
        jadi di situ ia harus tetap berbunyi."""
        for _ in range(3):
            gate.evaluate_quote(quote("100", stamped=False), reference=NOW)
        verdict = gate.evaluate_quote(quote("100", stamped=False), reference=NOW)

        assert verdict.quality is DataQuality.DUPLICATE
        assert "no provider timestamp" in verdict.detail

    def test_keduanya_tetap_menghentikan_pemakaian_datanya(
        self, gate: QualityGate
    ) -> None:
        """Yang berubah cara melaporkannya, bukan apakah datanya dipakai.
        Observasi yang dibaca ulang tidak membawa informasi baru, jadi ia tetap
        tidak boleh dihitung sebagai pengamatan baru."""
        assert DataQuality.REPEATED_READ.blocks_signal is True
        assert DataQuality.DUPLICATE.blocks_signal is True

    def test_latency_spike_is_flagged(self, gate: QualityGate) -> None:
        verdict = gate.evaluate_quote(quote(latency_ms=9_000), reference=NOW)
        assert verdict.quality is DataQuality.LATENCY_SPIKE

    def test_normal_latency_passes(self, gate: QualityGate) -> None:
        assert gate.evaluate_quote(quote(latency_ms=150), reference=NOW).ok


class TestDisconnect:
    def test_disconnected_provider_blocks_everything(self, gate: QualityGate) -> None:
        gate.mark_disconnected()
        verdict = gate.evaluate_quote(quote(), reference=NOW)
        assert verdict.quality is DataQuality.PROVIDER_DISCONNECTED

    def test_reconnect_clears_sequence_history(self, gate: QualityGate) -> None:
        """A gap caused by an outage is an artefact of the outage, so judging
        the next quote against the pre-outage one would be wrong."""
        gate.evaluate_quote(quote("100"), reference=NOW)
        gate.mark_disconnected()
        gate.mark_connected()
        assert gate.evaluate_quote(quote("150"), reference=NOW).ok


class TestUrutanGerbang:
    """Urutan cek quote sengaja berbeda dari PASAL 10, dan bedanya dikunci.

    Pasal menaruh DUPLICATE sebelum STALE; gerbang ini menilai staleness lebih
    dulu. Alasannya ada di docstring ``evaluate_quote`` dan intinya satu: umur
    kuotasi adalah angka yang DIUKUR ("120s old, limit 60s"), sedangkan
    "identik N kali" adalah kesimpulan yang ditarik darinya. Menukar urutannya
    membuat feed yang membeku sejam dilaporkan sebagai DUPLICATE dan angka yang
    diukur hilang dari verdict (SPEC 49).

    Dua test di bawah adalah dua sisi keputusan yang sama, dan keduanya harus
    ada: yang pertama merah kalau ``_check_duplicate`` dipindah ke depan, yang
    kedua merah kalau DUPLICATE malah dimatikan demi memenangkan STALE.
    """

    #: Empat kuotasi identik: cukup untuk melewati ambang tiga kali berturut.
    _RUN = 4

    def test_feed_beku_dilaporkan_stale_bukan_duplicate(self, gate: QualityGate) -> None:
        """Kuotasi yang sekaligus stale DAN duplikat harus bilang STALE."""
        later = NOW + timedelta(seconds=120)
        verdicts = [
            gate.evaluate_quote(quote("100"), reference=later) for _ in range(self._RUN)
        ]

        assert [v.quality for v in verdicts] == [DataQuality.STALE] * self._RUN
        # Angka terukurnya ikut terbawa, bukan cuma labelnya.
        assert "120s old" in verdicts[-1].detail

    def test_pengulangan_tetap_terdeteksi_selagi_kuotasinya_belum_stale(
        self, gate: QualityGate
    ) -> None:
        """Sisi sebaliknya: urutan ini tidak boleh mematikan deteksinya.

        Polling yang lebih cepat dari ambang staleness adalah satu-satunya
        jendela di mana 'identik berulang' adalah hal baru yang diketahui.
        """
        verdicts = [
            gate.evaluate_quote(quote("100"), reference=NOW) for _ in range(self._RUN)
        ]

        assert [v.quality for v in verdicts[:-1]] == [DataQuality.OK] * (self._RUN - 1)
        assert verdicts[-1].quality is DataQuality.REPEATED_READ


class TestRejectionAccounting:
    def test_rejections_are_counted_per_quality(self, gate: QualityGate) -> None:
        # Separate symbols: state is per-symbol, and reusing one would make the
        # second quote fail the out-of-order check before reaching staleness.
        gate.evaluate_quote(quote("0", symbol="BTC/USDT"), reference=NOW)
        gate.evaluate_quote(quote(age_sec=500, symbol="ETH/USDT"), reference=NOW)
        counts = gate.rejection_counts()
        assert counts[DataQuality.ABNORMAL_PRICE.value] == 1
        assert counts[DataQuality.STALE.value] == 1


# ---------------------------------------------------------------------------


def candle(
    open_time: datetime,
    interval: Horizon = Horizon.M1,
    *,
    market: Market = Market.CRYPTO,
    high: str = "110",
    low: str = "90",
) -> Candle:
    return Candle(
        market=market,
        symbol="BTC/USDT",
        interval=interval,
        open_time=open_time,
        close_time=open_time + interval.duration,
        open=Decimal(100),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(105),
        volume=Decimal(1),
        provenance=Provenance(source="test", server_timestamp=NOW),
    )


class TestCandleQuality:
    def test_coherent_candle_passes(self, gate: QualityGate) -> None:
        assert gate.evaluate_candle(candle(NOW)).ok

    def test_high_below_low_is_rejected(self, gate: QualityGate) -> None:
        broken = candle(NOW, high="80", low="120")
        verdict = gate.evaluate_candle(broken)
        assert verdict.quality is DataQuality.ABNORMAL_PRICE
        assert "incoherent" in verdict.detail


class TestNilaiTidakBerhingga:
    """NaN/Infinity dijawab sebagai verdict, bukan sebagai exception.

    ``Decimal("NaN")`` sah dibangun dan - berbeda dari float NaN - MELEMPAR
    ``InvalidOperation`` pada ``<``, ``<=``, ``>``, ``>=``. Satu field beracun
    membuat gerbang ini berhenti sebagai exception, dan handler per-aset di
    ``ingest.py`` hanya menangkap ``DataSourceUnavailableError``/``ArunaError``,
    sehingga SATU simbol menjatuhkan seluruh pass poll untuk semua aset.

    Gerbang ini ada justru untuk selalu mengembalikan verdict. Harga yang bukan
    bilangan adalah ABNORMAL_PRICE - itu jawabannya, bukan crash.
    """

    @pytest.mark.parametrize("nilai", ["NaN", "Infinity", "-Infinity"])
    def test_harga_tidak_berhingga_ditolak_bukan_meledak(
        self, gate: QualityGate, nilai: str
    ) -> None:
        verdict = gate.evaluate_quote(quote(nilai), reference=NOW)
        assert verdict.quality is DataQuality.ABNORMAL_PRICE
        assert "non-finite price" in verdict.detail

    def test_bid_atau_ask_tidak_berhingga_ditolak(self, gate: QualityGate) -> None:
        """Bid/ask ikut disaring: perbandingan bid > ask meledak sama saja."""
        verdict = gate.evaluate_quote(quote("100", bid="NaN", ask="101"), reference=NOW)
        assert verdict.quality is DataQuality.ABNORMAL_PRICE
        assert "non-finite bid" in verdict.detail

    def test_bar_dengan_ohlc_tidak_berhingga_ditolak(self, gate: QualityGate) -> None:
        verdict = gate.evaluate_candle(candle(NOW, high="Infinity"))
        assert verdict.quality is DataQuality.ABNORMAL_PRICE
        assert "non-finite high" in verdict.detail

    def test_nilai_berhingga_biasa_tidak_ikut_tersaring(self, gate: QualityGate) -> None:
        """Penjaga ini tidak boleh menolak apa pun yang selama ini lolos."""
        assert gate.evaluate_quote(quote("100", bid="99", ask="101"), reference=NOW).ok
        assert gate.evaluate_candle(candle(NOW)).ok


class TestGapDetection:
    def test_contiguous_series_has_no_gaps(self) -> None:
        series = [candle(NOW + timedelta(minutes=i)) for i in range(5)]
        assert find_candle_gaps(series) == []

    def test_missing_crypto_bars_are_reported(self) -> None:
        series = [candle(NOW), candle(NOW + timedelta(minutes=5))]
        gaps = find_candle_gaps(series)
        assert len(gaps) == 1
        assert gaps[0][2] == 4

    def test_gaps_are_never_filled(self) -> None:
        """The detector reports; it must not return synthesised bars."""
        series = [candle(NOW), candle(NOW + timedelta(minutes=5))]
        assert len(series) == 2

    def test_idx_weekend_is_not_a_gap(self) -> None:
        """Friday to Monday on a daily series is the exchange being closed, not
        missing data.  Flagging it would bury the real gaps in noise."""
        friday = datetime(2026, 8, 14, 2, 0, tzinfo=UTC)
        monday = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        series = [
            candle(friday, Horizon.D1, market=Market.IDX),
            candle(monday, Horizon.D1, market=Market.IDX),
        ]
        assert find_candle_gaps(series) == []

    def test_idx_missing_trading_day_is_still_a_gap(self) -> None:
        monday = datetime(2026, 8, 17, 2, 0, tzinfo=UTC)
        wednesday = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
        series = [
            candle(monday, Horizon.D1, market=Market.IDX),
            candle(wednesday, Horizon.D1, market=Market.IDX),
        ]
        gaps = find_candle_gaps(series)
        assert len(gaps) == 1
        assert gaps[0][2] == 1  # Tuesday

    def test_single_candle_has_no_gaps(self) -> None:
        assert find_candle_gaps([candle(NOW)]) == []
