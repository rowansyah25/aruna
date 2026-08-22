"""Futures market data foundation (FUTURES SPEC 2, 3, 4, 5, 29, 46).

The load-bearing test in this file is the one proving **ARUNA cannot place an
order**. FUTURES SPEC 3 and 50 make that the whole basis on which a leveraged
analysis tool is safe to run at all: it analyses, a person executes. A promise
in a docstring is not a guarantee, so the test goes looking for an execution
path and asserts there is none.

Second is data integrity. A mark price from two seconds ago combined with an
open-interest reading from twenty minutes ago describes a market that never
existed, and neither number looks wrong on its own.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.core.clock import now_utc
from aruna.futures import binance as binance_module
from aruna.futures.binance import (
    PUBLIC_ENDPOINTS,
    BinanceFuturesProvider,
    ForbiddenEndpoint,
)
from aruna.futures.integrity import (
    MAX_INPUT_AGE_SEC,
    MAX_INPUT_SKEW_SEC,
    IntegrityVerdict,
    check,
)
from aruna.futures.models import (
    ContractSpec,
    FundingRate,
    FuturesSnapshot,
    InstrumentType,
    MarkPrice,
    OpenInterest,
    PositionSide,
    side_of,
)
from aruna.futures.orderbook import OrderBookDepth, assess_liquidity

NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)


def _provenance():
    from aruna.data.models import Provenance

    return Provenance(source="test", server_timestamp=NOW)


def _book(
    *,
    spread: Decimal = Decimal(1),
    levels: int = 10,
    as_of: datetime | None = None,
) -> OrderBookDepth:
    mid = Decimal(50_000)
    return OrderBookDepth(
        symbol="BTCUSDT",
        bids=tuple(
            (mid - spread / 2 - Decimal(i), Decimal("0.5")) for i in range(levels)
        ),
        asks=tuple(
            (mid + spread / 2 + Decimal(i), Decimal("0.5")) for i in range(levels)
        ),
        as_of=as_of or NOW,
    )


def _snapshot(**overrides) -> FuturesSnapshot:
    base = {
        "symbol": "BTCUSDT",
        "captured_at": NOW,
        "mark": MarkPrice(
            symbol="BTCUSDT",
            mark_price=Decimal(50_000),
            index_price=Decimal(49_990),
            last_price=Decimal(50_001),
            as_of=NOW,
            provenance=_provenance(),
        ),
        "funding": FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            funding_time=NOW,
            next_funding_time=NOW + timedelta(hours=8),
            interval_hours=8,
            provenance=_provenance(),
        ),
        "open_interest": OpenInterest(
            symbol="BTCUSDT",
            open_interest=Decimal(80_000),
            notional=None,
            as_of=NOW,
            provenance=_provenance(),
        ),
        "order_book": _book(),
        "contract": ContractSpec(
            symbol="BTCUSDT",
            instrument=InstrumentType.PERPETUAL,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_notional=Decimal(5),
            max_leverage=125,
            maintenance_margin_rate=Decimal("0.004"),
        ),
    }
    return FuturesSnapshot(**(base | overrides))


# ---------------------------------------------------------------------------
# FUTURES SPEC 3, 50 - ARUNA cannot execute
# ---------------------------------------------------------------------------


class TestReadOnly:
    """These must never be relaxed."""

    FORBIDDEN = (
        "/order",
        "/allOpenOrders",
        "/openOrder",
        "/leverage",
        "/marginType",
        "/positionMargin",
        "/positionSide",
        "/account",
        "/balance",
        "/transfer",
        "/withdraw",
        "/listenKey",
    )

    def test_no_execution_endpoint_is_allowlisted(self) -> None:
        for path in PUBLIC_ENDPOINTS:
            for forbidden in self.FORBIDDEN:
                assert not path.endswith(forbidden), path

    def test_the_module_contains_no_execution_path_at_all(self) -> None:
        """The check that survives somebody adding "just one small helper"."""
        source = inspect.getsource(binance_module)
        # Strip the docstring, which names the forbidden paths in order to
        # explain that they are absent.
        body = source.split('"""', 2)[-1]
        for forbidden in self.FORBIDDEN:
            assert f'"{forbidden}"' not in body, forbidden
            assert f"'{forbidden}'" not in body, forbidden

    def test_nothing_signs_a_request(self) -> None:
        """An authenticated endpoint could not be called even if a path
        slipped through: there is no signing and no secret."""
        source = inspect.getsource(binance_module).lower()
        for marker in ("hmac", "signature", "api_secret", "apisecret", "x-mbx-apikey"):
            assert marker not in source, marker

    async def test_an_unlisted_path_is_refused_before_any_request(self) -> None:
        provider = BinanceFuturesProvider()
        with pytest.raises(ForbiddenEndpoint) as exc:
            await provider._get("/fapi/v1/order", symbol="BTCUSDT")
        assert "read-only" in str(exc.value)
        assert "cannot place" in str(exc.value)

    def test_the_provider_exposes_no_execution_method(self) -> None:
        methods = {
            name
            for name, _ in inspect.getmembers(
                BinanceFuturesProvider, inspect.isfunction
            )
        }
        for banned in (
            "create_order",
            "place_order",
            "cancel_order",
            "set_leverage",
            "set_margin_mode",
            "transfer",
            "withdraw",
        ):
            assert banned not in methods

    def test_the_capabilities_declare_the_regulatory_position(self) -> None:
        """FUTURES SPEC 5: recorded, not routed around, and not assumed either
        way - reachability turned out to be network-dependent."""
        capabilities = BinanceFuturesProvider().capabilities
        assert "Bappebti" in capabilities.regulatory_note
        assert "some Indonesian networks" in capabilities.regulatory_note
        assert any("read-only" in item for item in capabilities.limitations)
        assert any("unknown rather than guessed" in i for i in capabilities.limitations)


# ---------------------------------------------------------------------------
# FUTURES SPEC 46 - data integrity
# ---------------------------------------------------------------------------


class TestIntegrity:
    def test_a_coherent_set_passes(self) -> None:
        report = check(_snapshot(), reference=NOW + timedelta(seconds=5))
        assert report.verdict is IntegrityVerdict.OK
        assert report.blocks_signal is False

    def test_a_missing_input_blocks_rather_than_defaults(self) -> None:
        report = check(_snapshot(open_interest=None), reference=NOW)
        assert report.verdict is IntegrityVerdict.INCOMPLETE
        assert report.blocks_signal is True
        assert "open_interest" in report.findings[0]
        assert "tidak ada yang disubstitusikan" in report.findings[0]

    def test_a_stale_input_blocks(self) -> None:
        report = check(
            _snapshot(), reference=NOW + timedelta(seconds=MAX_INPUT_AGE_SEC + 5)
        )
        assert report.verdict is IntegrityVerdict.STALE
        assert report.blocks_signal is True

    def test_individually_fresh_inputs_can_still_be_incoherent(self) -> None:
        """The failure neither number reveals on its own."""
        stale_oi = OpenInterest(
            symbol="BTCUSDT",
            open_interest=Decimal(80_000),
            notional=None,
            as_of=NOW - timedelta(seconds=MAX_INPUT_SKEW_SEC + 10),
            provenance=_provenance(),
        )
        report = check(_snapshot(open_interest=stale_oi), reference=NOW + timedelta(seconds=2))

        assert report.verdict is IntegrityVerdict.SKEWED
        assert "tidak menggambarkan momen yang sama" in report.findings[0]

    def test_funding_is_judged_by_whether_it_has_settled(self) -> None:
        """The exemption belongs to a *settled* rate, not to a live estimate.

        This test originally asserted the opposite - that any funding reading
        got the eight-hour allowance. Live data showed why that is wrong: the
        current rate arrives on the same premiumIndex call as the mark price,
        so a three-hour-old one is genuinely stale, while a settled rate that
        old is simply the last settlement.
        """
        three_hours = NOW - timedelta(hours=3)
        live = FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            funding_time=three_hours,
            next_funding_time=NOW + timedelta(hours=5),
            interval_hours=8,
            provenance=_provenance(),
            settled=False,
        )
        settled = FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            funding_time=three_hours,
            next_funding_time=NOW + timedelta(hours=5),
            interval_hours=8,
            provenance=_provenance(),
            settled=True,
        )
        moment = NOW + timedelta(seconds=2)
        assert check(_snapshot(funding=live), reference=moment).verdict is (
            IntegrityVerdict.STALE
        )
        assert check(_snapshot(funding=settled), reference=moment).verdict is (
            IntegrityVerdict.OK
        )

    def test_the_report_says_why_there_is_no_middle_state(self) -> None:
        note = check(_snapshot(), reference=NOW).to_dict()["note"]
        assert "bukan sekadar turun kualitasnya, tapi salah" in note

    def test_a_bucketed_input_is_judged_on_its_own_cadence(self) -> None:
        """The defect the first live run exposed.

        `globalLongShortAccountRatio` is published in five-minute buckets, so
        the newest point is always up to 300s old. Judged at the 60s continuous
        limit it failed every single time - and blocked 100% of signals for a
        reason that was publication cadence, not staleness.
        """
        from aruna.futures.integrity import MAX_BUCKETED_AGE_SEC
        from aruna.futures.models import LongShortRatio

        bucketed = LongShortRatio(
            symbol="BTCUSDT",
            long_share=Decimal("0.67"),
            short_share=Decimal("0.33"),
            as_of=NOW - timedelta(seconds=237),  # a real observed age
            provenance=_provenance(),
        )
        report = check(
            _snapshot(long_short=bucketed), reference=NOW + timedelta(seconds=2)
        )
        assert report.verdict is IntegrityVerdict.OK
        assert MAX_BUCKETED_AGE_SEC > MAX_INPUT_AGE_SEC

    def test_a_bucketed_input_can_still_go_genuinely_stale(self) -> None:
        from aruna.futures.integrity import MAX_BUCKETED_AGE_SEC
        from aruna.futures.models import LongShortRatio

        ancient = LongShortRatio(
            symbol="BTCUSDT",
            long_share=Decimal("0.67"),
            short_share=Decimal("0.33"),
            as_of=NOW - timedelta(seconds=MAX_BUCKETED_AGE_SEC + 60),
            provenance=_provenance(),
        )
        report = check(_snapshot(long_short=ancient), reference=NOW)
        assert report.verdict is IntegrityVerdict.STALE

    def test_a_live_funding_estimate_is_judged_as_fresh_data(self) -> None:
        """It arrives on the same call as the mark price. The hours-long
        exemption belongs to *settled* rates, not to a running estimate."""
        stale_estimate = FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            funding_time=NOW - timedelta(minutes=30),
            next_funding_time=NOW + timedelta(hours=8),
            interval_hours=8,
            provenance=_provenance(),
            settled=False,
        )
        report = check(_snapshot(funding=stale_estimate), reference=NOW)
        assert report.verdict is IntegrityVerdict.STALE

    def test_a_settled_funding_rate_keeps_its_long_exemption(self) -> None:
        settled = FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            funding_time=NOW - timedelta(hours=6),
            next_funding_time=NOW + timedelta(hours=2),
            interval_hours=8,
            provenance=_provenance(),
            settled=True,
        )
        report = check(_snapshot(funding=settled), reference=NOW)
        assert report.verdict is IntegrityVerdict.OK

    def test_a_venue_clock_far_ahead_is_caught(self) -> None:
        """Negative ages make everything look fresh; a large lead would hide
        genuine staleness. Observed on this venue at about 7 seconds."""
        from aruna.futures.integrity import MAX_CLOCK_LEAD_SEC

        report = check(
            _snapshot(), reference=NOW - timedelta(seconds=MAX_CLOCK_LEAD_SEC + 30)
        )
        assert report.verdict is IntegrityVerdict.CLOCK_SKEW
        assert "lebih segar daripada kenyataannya" in report.findings[0]

    def test_ordinary_clock_drift_is_tolerated(self) -> None:
        report = check(_snapshot(), reference=NOW - timedelta(seconds=7))
        assert report.verdict is IntegrityVerdict.OK


# ---------------------------------------------------------------------------
# FUTURES SPEC 29, 30 - liquidity
# ---------------------------------------------------------------------------


class TestOrderBook:
    def test_the_spread_comes_from_the_touch(self) -> None:
        book = _book(spread=Decimal(10))
        assert book.best_ask - book.best_bid == Decimal(10)
        assert book.spread_bps == pytest.approx(Decimal(2), abs=Decimal("0.1"))

    def test_a_small_order_costs_almost_nothing(self) -> None:
        cost = _book().sweep_cost_pct(Decimal(1000), buying=True)
        assert cost is not None
        assert cost < Decimal("0.05")

    def test_a_large_order_walks_the_book_and_costs_more(self) -> None:
        book = _book()
        small = book.sweep_cost_pct(Decimal(5_000), buying=True)
        large = book.sweep_cost_pct(Decimal(200_000), buying=True)
        assert large > small

    def test_an_order_bigger_than_the_book_returns_none(self) -> None:
        """A number extrapolated past the last level would be invented."""
        assert _book().sweep_cost_pct(Decimal(100_000_000), buying=True) is None

    def test_liquidity_refuses_a_size_the_book_cannot_fill(self) -> None:
        verdict = assess_liquidity(_book(), Decimal(100_000_000), buying=True)
        assert verdict.tradeable is False
        assert "sama sekali tidak bisa mengisi" in verdict.findings[0]
        assert "apa yang terjadi setelah itu tidak diketahui" in verdict.findings[0]

    def test_liquidity_refuses_a_size_that_moves_the_price(self) -> None:
        verdict = assess_liquidity(
            _book(), Decimal(240_000), buying=True, max_sweep_pct=Decimal("0.001")
        )
        assert verdict.tradeable is False
        assert (
            "menggerakkan harga yang justru mau ditransaksikannya"
            in verdict.findings[0]
        )

    def test_a_one_sided_book_is_flagged(self) -> None:
        thin_asks = OrderBookDepth(
            symbol="BTCUSDT",
            bids=tuple((Decimal(50_000) - Decimal(i), Decimal(5)) for i in range(10)),
            asks=((Decimal(50_001), Decimal("0.01")),),
            as_of=NOW,
        )
        verdict = assess_liquidity(thin_asks, Decimal(100), buying=True)
        assert any("book condong ke sisi bid" in f for f in verdict.findings)

    def test_the_book_states_that_it_sees_only_resting_orders(self) -> None:
        assert "hidden liquidity" in _book().to_dict()["note"]


#: Minimal well-formed payloads, keyed by path, for the attribution tests.
_RAW: dict[str, object] = {
    "/fapi/v1/premiumIndex": {
        "markPrice": "50000",
        "indexPrice": "49990",
        "lastFundingRate": "0.0001",
        "nextFundingTime": int((NOW + timedelta(hours=8)).timestamp() * 1000),
        "time": int(NOW.timestamp() * 1000),
    },
    "/fapi/v1/openInterest": {
        "openInterest": "80000",
        "time": int(NOW.timestamp() * 1000),
    },
    "/fapi/v1/depth": {
        "T": int(NOW.timestamp() * 1000),
        "bids": [["49999", "1"]],
        "asks": [["50001", "1"]],
    },
    "/futures/data/globalLongShortAccountRatio": [
        {
            "longAccount": "0.55",
            "shortAccount": "0.45",
            "timestamp": int(NOW.timestamp() * 1000),
        }
    ],
    "/fapi/v1/exchangeInfo": {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "contractType": "PERPETUAL",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }
        ]
    },
}


class TestFailureAttribution:
    """One shared `try` around six awaits made the first failure eat the rest.

    `contract` is required and was fetched last, so a failure of the OPTIONAL
    long/short feed suppressed it. The snapshot then reported `missing:
    contract` and blocked every signal, naming an endpoint that had never been
    called - and integrity's wording for it, "the venue did not supply them",
    was false: it was not asked.
    """

    @staticmethod
    def _provider(failing: str) -> object:
        from aruna.futures.binance import BinanceFuturesProvider, FuturesDataUnavailable

        class Provider(BinanceFuturesProvider):
            async def _get(self, path: str, **params: object) -> object:
                if failing in path:
                    raise FuturesDataUnavailable(f"{path} is down")
                return _RAW.get(path, {})

        return Provider()

    @pytest.mark.asyncio
    async def test_an_optional_feed_failing_does_not_cost_a_required_one(
        self,
    ) -> None:
        provider = self._provider("globalLongShortAccountRatio")
        snapshot = await provider.snapshot("BTCUSDT")

        assert snapshot.long_short is None
        assert snapshot.contract is not None
        assert "contract" not in snapshot.missing

    @pytest.mark.asyncio
    async def test_a_failure_costs_exactly_the_input_that_failed(self) -> None:
        provider = self._provider("openInterest")
        snapshot = await provider.snapshot("BTCUSDT")

        assert snapshot.missing == ("open_interest",)
        assert snapshot.mark is not None
        assert snapshot.contract is not None


class TestOneClockPerSnapshot:
    """Mixed clocks made a band of the tolerance unreachable.

    Every other input is stamped from the venue's clock. The order book was
    stamped from ours, so the venue/local offset landed in the skew figure -
    and any offset between ~32s and the 60s MAX_CLOCK_LEAD_SEC tripped SKEWED
    before CLOCK_SKEW could fire. Half the range that constant declares
    tolerable could not produce an OK verdict.
    """

    class _Provider:
        def __init__(self, payload: dict) -> None:
            self.payload = payload

        async def _get(self, path: str, **params: object) -> dict:
            return self.payload

    @staticmethod
    def _payload(**extra: object) -> dict:
        return {
            "bids": [["50000", "1"]],
            "asks": [["50010", "1"]],
            **extra,
        }

    @pytest.mark.asyncio
    async def test_the_book_is_stamped_from_the_venue_transaction_time(self) -> None:
        from aruna.futures.orderbook import fetch_order_book

        venue = datetime(2026, 8, 15, 12, 0, 30, tzinfo=UTC)
        book = await fetch_order_book(
            self._Provider(self._payload(T=int(venue.timestamp() * 1000))),
            "BTCUSDT",
        )
        assert book.as_of == venue

    @pytest.mark.asyncio
    async def test_event_time_is_used_when_there_is_no_transaction_time(self) -> None:
        from aruna.futures.orderbook import fetch_order_book

        venue = datetime(2026, 8, 15, 12, 0, 45, tzinfo=UTC)
        book = await fetch_order_book(
            self._Provider(self._payload(E=int(venue.timestamp() * 1000))),
            "BTCUSDT",
        )
        assert book.as_of == venue

    @pytest.mark.asyncio
    async def test_a_payload_with_no_stamp_falls_back_to_our_clock(self) -> None:
        """Still honest: an unstamped body is stamped on arrival, not refused."""
        from aruna.futures.orderbook import fetch_order_book

        before = now_utc()
        book = await fetch_order_book(self._Provider(self._payload()), "BTCUSDT")
        assert before <= book.as_of <= now_utc()

    def test_a_venue_lead_inside_the_tolerance_still_reaches_ok(self) -> None:
        """The band that was unreachable: 40s of lead, well under the 60s limit.

        Before the fix the order book alone carried a 0s age while the other
        inputs carried -40s, producing a 40s skew against a 30s limit - SKEWED,
        on six requests issued moments apart.
        """
        lead = timedelta(seconds=40)
        base = _snapshot()
        snapshot = _snapshot(
            mark=replace(base.mark, as_of=NOW + lead),
            funding=replace(base.funding, funding_time=NOW + lead),
            open_interest=replace(base.open_interest, as_of=NOW + lead),
            order_book=_book(as_of=NOW + lead),
        )
        report = check(snapshot, reference=NOW)
        assert report.verdict is IntegrityVerdict.OK
        assert report.blocks_signal is False

    def test_the_old_mixed_clock_state_would_have_been_rejected(self) -> None:
        """The proof the previous test is not vacuous.

        Same 40s venue lead, but the book stamped from our clock as it used to
        be. If this does not block, the fix changed nothing.
        """
        lead = timedelta(seconds=40)
        base = _snapshot()
        snapshot = _snapshot(
            mark=replace(base.mark, as_of=NOW + lead),
            funding=replace(base.funding, funding_time=NOW + lead),
            open_interest=replace(base.open_interest, as_of=NOW + lead),
            order_book=_book(as_of=NOW),
        )
        report = check(snapshot, reference=NOW)
        assert report.verdict is IntegrityVerdict.SKEWED
        assert report.blocks_signal is True


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_a_council_decision_renders_as_a_futures_side(self) -> None:
        """One decision, two vocabularies - not two parallel enums that drift."""
        from aruna.core.enums import Decision

        assert side_of(Decision.BUY) is PositionSide.LONG
        assert side_of(Decision.SELL) is PositionSide.SHORT
        assert side_of(Decision.WAIT) is PositionSide.FLAT

    def test_basis_is_mark_against_index(self) -> None:
        mark = _snapshot().mark
        assert mark.basis_pct is not None
        assert mark.basis_pct > 0  # perpetual trading over spot

    def test_the_reference_price_is_the_mark_not_the_last_trade(self) -> None:
        """Liquidation is measured against the mark; confusing the two is how a
        position dies at a level the trader thought was safe."""
        snapshot = _snapshot()
        assert snapshot.reference_price == Decimal(50_000)
        assert snapshot.mark.last_price == Decimal(50_001)

    def test_funding_cost_scales_with_the_holding_period(self) -> None:
        funding = _snapshot().funding
        eight_hours = funding.cost_pct(8)
        one_day = funding.cost_pct(24)
        assert one_day == pytest.approx(eight_hours * 3, abs=Decimal("0.000001"))

    def test_a_missing_input_is_named_not_defaulted(self) -> None:
        snapshot = _snapshot(funding=None, order_book=None)
        assert set(snapshot.missing) == {"funding", "order_book"}
        assert snapshot.complete is False

    def test_margin_brackets_pick_the_rate_for_the_notional(self) -> None:
        contract = ContractSpec(
            symbol="BTCUSDT",
            instrument=InstrumentType.PERPETUAL,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_notional=Decimal(5),
            max_leverage=125,
            maintenance_margin_rate=Decimal("0.004"),
            margin_brackets=(
                (Decimal(0), Decimal("0.004"), Decimal(0)),
                (Decimal(50_000), Decimal("0.005"), Decimal(50)),
                (Decimal(250_000), Decimal("0.010"), Decimal(1300)),
            ),
        )
        assert contract.bracket_for(Decimal(10_000)) == (Decimal("0.004"), Decimal(0))
        assert contract.bracket_for(Decimal(100_000)) == (Decimal("0.005"), Decimal(50))
        assert contract.bracket_for(Decimal(500_000)) == (Decimal("0.010"), Decimal(1300))

    def test_no_brackets_falls_back_conservatively(self) -> None:
        contract = _snapshot().contract
        rate, amount = contract.bracket_for(Decimal(1_000_000))
        assert rate == contract.maintenance_margin_rate
        assert amount == Decimal(0)

    def test_an_unfetched_leverage_cap_is_unknown_not_one(self) -> None:
        """The defect that emptied the whole leverage ladder.

        `/fapi/v1/leverageBracket` needs a signed request this adapter cannot
        make, so the cap is routinely unavailable. Defaulting it to 1 skipped
        every rung from 2x up and reported "no safe leverage" for a reason that
        had nothing to do with safety.
        """
        contract = ContractSpec(
            symbol="BTCUSDT",
            instrument=InstrumentType.PERPETUAL,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_notional=Decimal(50),
        )
        assert contract.max_leverage is None
        assert contract.leverage_cap_known is False

    def test_the_ladder_still_runs_when_the_cap_is_unknown(self) -> None:
        from aruna.futures.leverage import CANDIDATES, stress_test

        contract = ContractSpec(
            symbol="BTCUSDT",
            instrument=InstrumentType.PERPETUAL,
            base_asset="BTC",
            quote_asset="USDT",
            tick_size=Decimal("0.10"),
            step_size=Decimal("0.001"),
            min_notional=Decimal(50),
        )
        options = stress_test(
            entry=Decimal(50_000),
            stop=Decimal(49_000),
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            contract=contract,
            atr=Decimal(500),
        )
        assert {o.leverage for o in options} == set(CANDIDATES)
        assert all(
            any(
                "tidak diketahui apakah tingkat ini diizinkan" in f
                for f in o.findings
            )
            for o in options
        )
