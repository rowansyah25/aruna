"""Guards for two things that were only ever true by accident.

Both of these are "the code is written, the unit test passes, the live path is
never reached" defects, which is the failure mode this project keeps hitting.

**PASAL 26.** ``market_ticks`` was written 76,567 times and read zero times.
Dropping the table and deleting the writer is not enough on its own: nothing
would notice a future change that added the write back, because nothing ever
asserted the write was absent. The tests below fail if the wire is reconnected.

**Telegram bindings.** ``/btc`` was bound to a hardcoded ``BTC/IDR`` while the
whole of ``tests/test_telegram.py`` passed, because those tests call the
formatting functions with fabricated row dicts and never touch the binding. The
command could have pointed at any symbol at all, including one no longer in the
universe, and the suite would have stayed green while the operator's phone
answered with silence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from tests.conftest import make_settings

from aruna.core.config import CURRENT_PHASE, DataSettings
from aruna.core.enums import Horizon, Market
from aruna.core.runtime_state import RuntimeState
from aruna.data.ingest import IngestResult, MarketIngestor
from aruna.data.models import Provenance, Snapshot
from aruna.data.provider import ProviderCapabilities, ProviderStatus, Transport
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.seed.universe import CRYPTO_UNIVERSE

NOW = datetime(2026, 8, 17, 6, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# PASAL 26 - no per-observation row reaches SQL
# ---------------------------------------------------------------------------


class _Provider:
    """The smallest provider that gets one asset through ``_poll_asset``."""

    name = "fake-spot"
    market = Market.CRYPTO

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name=self.name,
            market=Market.CRYPTO,
            transport=Transport.POLL,
            is_realtime=True,
            expected_delay_sec=0,
            # False so the poll does not also need a fake order book.
            supports_order_book=False,
            supported_intervals=(Horizon.M1,),
            max_candles_per_request=1000,
            requires_credentials=False,
            regulatory_note="test double",
        )

    async def status(self) -> ProviderStatus:
        return ProviderStatus(reachable=True)

    async def fetch_snapshot(self, symbol: str) -> Snapshot:
        return Snapshot(
            market=Market.CRYPTO,
            symbol=symbol,
            captured_at=NOW,
            last_price=Decimal("63000.00"),
            bid=Decimal("62999.99"),
            ask=Decimal("63000.01"),
            provenance=Provenance(
                source=self.name,
                server_timestamp=NOW,
                provider_timestamp=NOW,
                latency_ms=12.0,
            ),
        )


class _Universe:
    def __init__(self, assets: list[Any]) -> None:
        self._assets = assets

    async def assets(self, *, market: Market, enabled_only: bool = True) -> list[Any]:
        return self._assets


class _Asset:
    id = 1
    symbol = "BTC/USDT"


class _StoreThatRefusesUnknownWrites:
    """Records snapshot writes; explodes on anything else.

    ``__getattr__`` is the point of this class. A permissive mock would answer
    a ``record_tick`` call happily and the test would still pass, which is
    exactly how the original write survived unnoticed.
    """

    def __init__(self) -> None:
        self.snapshots: list[Any] = []
        self.events: list[str] = []

    async def record_snapshot(self, asset_id: int, snapshot: Any) -> int:
        self.snapshots.append((asset_id, snapshot))
        return len(self.snapshots)

    async def record_provider_event(self, **kwargs: Any) -> int:
        self.events.append(str(kwargs.get("event_type")))
        return len(self.events)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"ingest reached MarketDataRepository.{name}(), which is not a "
            "write PASAL 26 permits. SQL is long-term analysis memory: no "
            "per-tick, per-websocket-event or per-order-book-update row."
        )


def _ingestor(store: Any) -> MarketIngestor:
    return MarketIngestor(
        provider=_Provider(),
        universe=_Universe([_Asset()]),
        store=store,
        settings=DataSettings(_env_file=None),
    )


class TestNoTickReachesSql:
    @pytest.mark.asyncio
    async def test_a_poll_writes_a_snapshot_and_nothing_else(self) -> None:
        """Cut the wire by re-adding a ``record_tick`` call in ``_poll_asset``
        and this fails on the fake store's ``__getattr__``."""
        store = _StoreThatRefusesUnknownWrites()
        result = await _ingestor(store).poll_once()

        assert isinstance(result, IngestResult)
        assert result.failures == []
        assert len(store.snapshots) == 1
        assert store.snapshots[0][1].symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_the_result_does_not_report_a_tick_count(self) -> None:
        """``IngestResult.ticks`` was printed by ``aruna fetch``. A count that
        can only ever be zero is worse than no count: it reads as a measurement
        of something that is not happening."""
        result = await _ingestor(_StoreThatRefusesUnknownWrites()).poll_once()
        assert not hasattr(result, "ticks")
        assert "ticks" not in result.summary()

    def test_the_repository_offers_no_tick_api(self) -> None:
        """The table is gone, so a method that writes to it would raise at
        runtime rather than at review time. This is the review."""
        for name in ("record_tick", "latest_tick", "quality_breakdown"):
            assert not hasattr(MarketDataRepository, name), (
                f"MarketDataRepository.{name} is back; migration 0020 dropped "
                "market_ticks, so this can only fail at runtime"
            )

    def test_the_sampling_knob_is_gone_with_the_thing_it_sampled(self) -> None:
        """``tick_sample_sec`` thinned tick storage. With no tick storage it
        configures nothing, and a knob that configures nothing is a promise the
        system does not keep."""
        assert not hasattr(DataSettings(_env_file=None), "tick_sample_sec")


# ---------------------------------------------------------------------------
# Telegram bindings point at symbols that exist
# ---------------------------------------------------------------------------


class _RecordingMarketData:
    """Answers the asset-detail path and records what symbol it was asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[Market, str]] = []

    async def latest_snapshot(self, *, market: Market, symbol: str) -> None:
        self.asked.append((market, symbol))
        return None

    async def candles(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class _Update:
    def __init__(self) -> None:
        self.sent: list[str] = []

    @property
    def effective_chat(self) -> Any:
        return None


class TestTelegramCryptoBindings:
    """The binding, not the formatter. Nothing else covers this."""

    def _bot(self, market_data: Any) -> Any:
        from aruna.notify.telegram.bot import BotDeps, TelegramBot

        return TelegramBot(
            BotDeps(
                settings=make_settings(),
                state=RuntimeState(),
                phase=CURRENT_PHASE,
                latest_health=lambda: None,
                market_data=market_data,
            )
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("command", "expected"),
        [("btc", "BTC/USDT"), ("eth", "ETH/USDT"), ("sol", "SOL/USDT")],
    )
    async def test_a_crypto_command_asks_for_the_symbol_the_universe_seeds(
        self, command: str, expected: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Change the bound symbol back to ``BTC/IDR`` and this goes red.

        The assertion is deliberately against the seeded universe rather than
        against a literal: a binding that names a symbol ARUNA does not seed
        returns an empty snapshot forever, and empty is indistinguishable from
        "the venue is down" on a phone screen.
        """
        seeded = {spec.symbol for spec in CRYPTO_UNIVERSE}
        assert expected in seeded

        store = _RecordingMarketData()
        bot = self._bot(store)
        monkeypatch.setattr(bot, "_reply", _noop_reply)

        handler = bot.registry[command].handler
        assert handler is not None, f"/{command} is not bound at all"
        await handler(_Update(), None)

        assert store.asked == [(Market.CRYPTO, expected)]
        assert store.asked[0][1] in seeded

    def test_every_crypto_command_description_names_a_seeded_symbol(self) -> None:
        """The help text is the other half of the binding. It drifted before:
        the handler and the description are set in different modules, and only
        the description is ever read by a human."""
        seeded = {spec.symbol for spec in CRYPTO_UNIVERSE}
        bot = self._bot(_RecordingMarketData())
        for command in ("btc", "eth", "sol"):
            summary = bot.registry[command].summary
            assert any(symbol in summary for symbol in seeded), (
                f"/{command} is described as {summary!r}, which names no "
                "symbol ARUNA seeds"
            )


async def _noop_reply(*args: Any, **kwargs: Any) -> None:
    return None


# ---------------------------------------------------------------------------
# Prices survive the trip to the operator's screen
# ---------------------------------------------------------------------------


class TestUsdtPricesAreNotRoundedToNothing:
    """Every figure below was read off ``market_snapshots`` on 2026-08-17.

    The existing formatting tests all pass fabricated rows with IDX-sized
    prices, where rounding to whole units is invisible. That is why the
    ``places=0`` default survived the move to USDT with a green suite while
    the operator's screen said XRP was worth ``1``.
    """

    @pytest.mark.parametrize(
        ("stored", "shown"),
        [
            ("1.001500000000", "1,0015"),  # XRP/USDT - was rendered "1"
            ("75.810000000000", "75,81"),  # SOL/USDT - was rendered "76"
            ("63663.080000000000", "63.663,08"),  # BTC/USDT
            ("1907.260000000000", "1.907,26"),  # ETH/USDT
        ],
    )
    def test_a_usdt_price_keeps_the_digits_the_venue_quoted(
        self, stored: str, shown: str
    ) -> None:
        from aruna.notify.telegram.formatting import _price

        assert _price(Decimal(stored)) == shown

    def test_a_whole_rupiah_price_is_unchanged(self) -> None:
        """The IDX side must not grow decimals it never had. DECIMAL(30,12)
        pads every stored price, so a naive fix prints ``4.200,000000000000``."""
        from aruna.notify.telegram.formatting import _price

        assert _price(Decimal("4200.000000000000")) == "4.200"

    def test_a_tight_spread_is_still_two_different_numbers(self) -> None:
        """BTC/USDT's measured spread was 0.0016 bps: bid 63663.07, ask
        63663.08. Rounded to whole units the operator sees the same number
        twice and no spread at all."""
        from aruna.notify.telegram.formatting import _price

        bid = _price(Decimal("63663.070000000000"))
        ask = _price(Decimal("63663.080000000000"))
        assert bid != ask

    def test_a_missing_price_is_a_dash_not_a_zero(self) -> None:
        from aruna.notify.telegram.formatting import _price

        assert _price(None) == "-"

    def test_the_rendered_message_carries_the_precision_not_just_the_helper(
        self,
    ) -> None:
        """Asserted on the finished message, because the helper is not the bug.

        A guard that only tests ``_price`` leaves every call site free to keep
        calling ``_money(..., 0)``. That is not hypothetical: reverting the
        ``TERAKHIR:`` line alone was checked against the whole existing
        Telegram suite and nothing went red, because those tests feed
        fabricated IDX-sized rows through the same function.

        The row below is the real XRP/USDT snapshot from 2026-08-17.
        """
        from aruna.notify.telegram import formatting as fmt

        row = {
            "captured_at": NOW,
            "last_price": Decimal("1.001500000000"),
            "bid": Decimal("1.001400000000"),
            "ask": Decimal("1.001500000000"),
            "spread_bps": Decimal("0.9986"),
            "high_24h": Decimal("1.023400000000"),
            "low_24h": Decimal("0.998100000000"),
            "volume_24h": Decimal("125000.5"),
            "change_24h_pct": Decimal("-1.24"),
            "session_code": "OPEN",
            "market_open": None,
            "is_realtime": True,
            "declared_delay_sec": 0,
            "source": "binance-spot",
            "quality": "OK",
            "quality_detail": None,
            "provider_timestamp": NOW,
            "server_timestamp": NOW,
            "latency_ms": 12.0,
        }
        text = fmt.asset_detail("XRP/USDT", row, [], phase=CURRENT_PHASE)

        assert "1,0015" in text
        assert "1,0014" in text
        # The failure this replaces: every one of those became a bare "1".
        assert "TERAKHIR:  1\n" not in text
