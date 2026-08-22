"""Market data records, resampling, and provider registry."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.config import DataSettings, ProviderSettings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ConfigError
from aruna.data.models import Candle, OrderBook, OrderBookLevel, Provenance, Quote, Snapshot
from aruna.data.registry import available, build_provider, build_providers
from aruna.data.resample import (
    can_resample,
    incomplete_buckets,
    is_resampled,
    resample_candles,
)

NOW = datetime(2026, 8, 15, 2, 0, tzinfo=UTC)


def provenance(**kwargs) -> Provenance:
    return Provenance(source="test", server_timestamp=NOW, **kwargs)


class TestProvenance:
    def test_zero_delay_is_realtime(self) -> None:
        assert provenance().is_realtime is True

    def test_declared_delay_is_not_realtime(self) -> None:
        assert provenance(declared_delay_sec=900).is_realtime is False

    def test_provider_age_is_measured_against_receipt(self) -> None:
        p = provenance(provider_timestamp=NOW - timedelta(seconds=30))
        assert p.provider_age_sec == 30

    def test_negative_age_means_the_provider_clock_is_ahead(self) -> None:
        p = provenance(provider_timestamp=NOW + timedelta(seconds=5))
        assert p.provider_age_sec == -5


class TestQuote:
    def test_spread_in_basis_points(self) -> None:
        q = Quote(
            market=Market.CRYPTO,
            symbol="BTC/USDT",
            price=Decimal(100),
            bid=Decimal(99),
            ask=Decimal(101),
            provenance=provenance(),
        )
        assert q.spread == Decimal(2)
        assert q.mid == Decimal(100)
        assert q.spread_bps == Decimal("200.0000")

    def test_spread_is_quantised_for_storage(self) -> None:
        """Decimal division yields 28 digits; rounding is decided here, not by
        a silent narrowing on insert."""
        q = Quote(
            market=Market.CRYPTO,
            symbol="BTC/USDT",
            price=Decimal(3),
            bid=Decimal(3),
            ask=Decimal("3.0001"),
            provenance=provenance(),
        )
        assert q.spread_bps is not None
        assert q.spread_bps.as_tuple().exponent == -4

    def test_missing_side_means_no_spread(self) -> None:
        q = Quote(
            market=Market.CRYPTO, symbol="BTC/USDT", price=Decimal(100),
            bid=Decimal(99), provenance=provenance(),
        )
        assert q.spread_bps is None


class TestCandle:
    def _candle(self, **overrides) -> Candle:
        values = {
            "open": Decimal(100), "high": Decimal(110),
            "low": Decimal(90), "close": Decimal(105),
        } | overrides
        return Candle(
            market=Market.CRYPTO,
            symbol="BTC/USDT",
            interval=Horizon.M1,
            open_time=NOW,
            close_time=NOW + timedelta(minutes=1),
            volume=Decimal(5),
            provenance=provenance(),
            **values,
        )

    def test_coherent_candle(self) -> None:
        assert self._candle().is_coherent

    @pytest.mark.parametrize(
        "overrides",
        [
            {"high": Decimal(80)},          # high below low
            {"low": Decimal(120)},          # low above high
            {"open": Decimal(200)},         # open above high
            {"close": Decimal(1)},          # close below low
        ],
    )
    def test_incoherent_combinations(self, overrides: dict) -> None:
        assert not self._candle(**overrides).is_coherent

    def test_change_percentage(self) -> None:
        assert self._candle().change_pct == Decimal(5)


class TestOrderBook:
    def _book(self, best_bid: str = "99", best_ask: str = "101") -> OrderBook:
        return OrderBook(
            market=Market.CRYPTO,
            symbol="BTC/USDT",
            bids=(OrderBookLevel(Decimal(best_bid), Decimal(2)),),
            asks=(OrderBookLevel(Decimal(best_ask), Decimal(6)),),
            provenance=provenance(),
        )

    def test_depth_and_imbalance(self) -> None:
        book = self._book()
        assert book.bid_depth == Decimal(2)
        assert book.ask_depth == Decimal(6)
        assert book.imbalance == Decimal("0.25")

    def test_crossed_detection(self) -> None:
        assert self._book().is_crossed is False
        assert self._book("102", "101").is_crossed is True

    def test_empty_book_has_no_imbalance(self) -> None:
        empty = OrderBook(
            market=Market.CRYPTO, symbol="BTC/USDT", bids=(), asks=(),
            provenance=provenance(),
        )
        assert empty.imbalance is None
        assert empty.is_crossed is False


class TestSnapshotFreshness:
    def _snapshot(self, **kwargs) -> Snapshot:
        base = {
            "market": Market.CRYPTO,
            "symbol": "BTC/USDT",
            "captured_at": NOW,
            "last_price": Decimal(100),
            "provenance": provenance(),
        }
        return Snapshot(**(base | kwargs))

    def test_realtime_open_market(self) -> None:
        assert self._snapshot().describe_freshness() == "REALTIME"

    def test_delayed_feed_says_so(self) -> None:
        """SPEC 3 and 5: a delayed feed must never be described as realtime."""
        snap = self._snapshot(provenance=provenance(declared_delay_sec=900))
        assert snap.describe_freshness() == "DELAYED ~15m"
        assert snap.is_realtime is False

    def test_closed_market_says_so(self) -> None:
        assert "CLOSED" in self._snapshot(market_open=False).describe_freshness()

    def test_closed_market_is_not_tradeable(self) -> None:
        assert self._snapshot(market_open=False).tradeable is False

    def test_bad_quality_is_not_tradeable(self) -> None:
        from aruna.core.enums import DataQuality

        assert self._snapshot(quality=DataQuality.STALE).tradeable is False

    def test_crypto_has_no_open_close_concept(self) -> None:
        assert self._snapshot(market_open=None).tradeable is True


class TestResampling:
    def _series(self, count: int, *, start: datetime = NOW) -> list[Candle]:
        return [
            Candle(
                market=Market.CRYPTO,
                symbol="BTC/USDT",
                interval=Horizon.M1,
                open_time=start + timedelta(minutes=i),
                close_time=start + timedelta(minutes=i + 1),
                open=Decimal(100 + i),
                high=Decimal(110 + i),
                low=Decimal(90 + i),
                close=Decimal(105 + i),
                volume=Decimal(1),
                provenance=provenance(),
            )
            for i in range(count)
        ]

    def test_whole_multiples_only(self) -> None:
        assert can_resample(Horizon.M1, Horizon.M3)
        assert can_resample(Horizon.M1, Horizon.M10)
        assert not can_resample(Horizon.M1, Horizon.M1)
        assert not can_resample(Horizon.M5, Horizon.M1)  # cannot split a bar

    def test_aggregate_is_exact(self) -> None:
        derived = resample_candles(self._series(6), Horizon.M3)
        assert len(derived) == 2
        first = derived[0]
        assert first.open == Decimal(100)     # first bar's open
        assert first.close == Decimal(107)    # last bar's close
        assert first.high == Decimal(112)     # highest high
        assert first.low == Decimal(90)       # lowest low
        assert first.volume == Decimal(3)     # summed

    def test_incomplete_bucket_is_dropped_not_averaged(self) -> None:
        """An aggregate spanning a gap looks plausible and is wrong."""
        series = self._series(4)  # 4 one-minute bars -> one full 3m + a partial
        derived = resample_candles(series, Horizon.M3)
        assert len(derived) == 1
        assert incomplete_buckets(series, Horizon.M3)

    def test_derived_candles_are_labelled(self) -> None:
        """Nothing downstream may mistake a derived bar for a published one."""
        derived = resample_candles(self._series(3), Horizon.M3)
        assert is_resampled(derived[0].provenance.source)
        assert "resampled(1m)" in derived[0].provenance.source

    def test_mixed_intervals_are_refused(self) -> None:
        series = self._series(3)
        series[1] = replace(series[1], interval=Horizon.M5)
        with pytest.raises(ValueError, match="mixed-interval"):
            resample_candles(series, Horizon.M3)

    def test_impossible_target_is_refused(self) -> None:
        """A week is 2.33 three-day bars, so no exact aggregate exists."""
        assert not can_resample(Horizon.D3, Horizon.W1)
        series = [replace(c, interval=Horizon.D3) for c in self._series(3)]
        with pytest.raises(ValueError, match="not a whole multiple"):
            resample_candles(series, Horizon.W1)

    def test_empty_input(self) -> None:
        assert resample_candles([], Horizon.M3) == []


class TestProviderRegistry:
    def test_registered_providers(self) -> None:
        assert "binance-spot" in available(Market.CRYPTO)
        assert "yahoo" in available(Market.IDX)

    def test_crypto_has_exactly_one_provider(self) -> None:
        """PASAL 5 forbids a fallback, and a second registered adapter is one.

        Not a style assertion.  While two crypto adapters were registered, a
        single edited environment variable silently selected the retired
        IDR-quoted venue, and every downstream symbol lookup then failed for a
        reason that pointed at the universe rather than at the feed.
        """
        assert available(Market.CRYPTO) == ("binance-spot",)

    def test_build_by_name(self) -> None:
        provider = build_provider(
            Market.CRYPTO, "binance-spot", DataSettings(_env_file=None)
        )
        assert provider.name == "binance-spot"
        assert provider.market is Market.CRYPTO

    def test_name_is_case_insensitive(self) -> None:
        assert build_provider(Market.IDX, "YAHOO", DataSettings(_env_file=None))

    def test_unknown_provider_is_refused(self) -> None:
        """The example name is deliberately one that has never been registered.

        It used to be ``binance``, which stopped being a lie the moment a
        Binance adapter existed - the test would have gone red for being
        correct.  A name no adapter will ever claim keeps the assertion about
        the refusal path instead of about the roster.
        """
        with pytest.raises(ConfigError, match="unknown CRYPTO provider"):
            build_provider(Market.CRYPTO, "no-such-venue", DataSettings(_env_file=None))

    def test_blank_provider_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="no provider configured"):
            build_provider(Market.CRYPTO, "  ", DataSettings(_env_file=None))

    def test_unconfigured_market_is_simply_absent(self) -> None:
        """A market with no provider is reported unavailable, never served from
        another market's feed."""
        providers = build_providers(
            ProviderSettings(
                _env_file=None, crypto_provider="binance-spot", idx_provider=""
            ),
            DataSettings(_env_file=None),
            (Market.CRYPTO, Market.IDX),
        )
        assert set(providers) == {Market.CRYPTO}

    def test_the_default_crypto_provider_is_one_that_exists(self) -> None:
        """The shipped default must be buildable without editing anything.

        A default naming an unregistered adapter does not crash at import: it
        raises ``ConfigError`` deep inside startup, where ``app.py`` catches
        it, logs ``ingest.provider_config_invalid`` and carries on - ARUNA
        alive with no crypto ingest at all, and one log line to say so.
        """
        default = ProviderSettings(_env_file=None).crypto_provider
        assert default in available(Market.CRYPTO)


class TestCapabilityHonesty:
    """SPEC 3, 5, 49: an adapter must not overstate what it offers."""

    def test_binance_spot_declares_polling_and_realtime(self) -> None:
        caps = build_provider(
            Market.CRYPTO, "binance-spot", DataSettings(_env_file=None)
        ).capabilities
        assert caps.transport.value == "POLL"
        assert caps.is_realtime is True
        assert caps.expected_delay_sec == 0
        assert caps.supports_order_book is True

    def test_the_regulatory_note_says_binance_is_not_registered(self) -> None:
        """The predecessor asserted ``"Bappebti" in regulatory_note`` because
        that venue *was* registered.  Binance is not, so keeping the same
        assertion would keep the word and invert the fact.

        What must be true is the negation, and it has to survive being read by
        an operator deciding whether the deployment's jurisdiction permits
        this feed (SPEC 47).
        """
        caps = build_provider(
            Market.CRYPTO, "binance-spot", DataSettings(_env_file=None)
        ).capabilities
        note = caps.regulatory_note.lower()
        assert "bappebti" in note
        assert "tidak terdaftar" in note

    def test_yahoo_declares_itself_delayed(self) -> None:
        caps = build_provider(
            Market.IDX, "yahoo", DataSettings(_env_file=None)
        ).capabilities
        assert caps.is_realtime is False
        assert caps.expected_delay_sec == 900
        assert caps.supports_order_book is False
        assert caps.limitations

    def test_unsupported_intervals_are_absent_not_faked(self) -> None:
        """10m is genuinely absent from Binance spot; 3m genuinely is not.

        This test used to assert that crypto supported *neither*, which was
        true of the previous venue.  Carrying that assertion forward would
        have forced the adapter to hide an interval the venue publishes, and
        every 3m bar would have been resampled from 1m for no reason.
        """
        crypto = build_provider(
            Market.CRYPTO, "binance-spot", DataSettings(_env_file=None)
        ).capabilities
        assert not crypto.supports(Horizon.M10)
        assert crypto.supports(Horizon.M3)
        assert crypto.supports(Horizon.M1)
