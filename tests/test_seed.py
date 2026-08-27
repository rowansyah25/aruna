"""Universe configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aruna.core.enums import Market
from aruna.core.errors import ConfigError
from aruna.seed.universe import (
    CRYPTO_UNIVERSE,
    IDX_LOT_SIZE,
    IDX_UNIVERSE,
    default_universe,
    idx_tick_size,
    load_universe,
)


class TestDefaults:
    def test_crypto_mvp_covers_the_five_spec_coins(self) -> None:
        """The specification names these five coins, quoted in USDT."""
        assert {spec.base_asset for spec in CRYPTO_UNIVERSE} == {
            "BTC",
            "ETH",
            "SOL",
            "BNB",
            "XRP",
        }

    def test_idx_starting_universe_matches_the_spec_example(self) -> None:
        symbols = {spec.symbol for spec in IDX_UNIVERSE}
        assert symbols == {
            "BBCA",
            "BBRI",
            "BMRI",
            "BBNI",
            "TLKM",
            "ASII",
            "ICBP",
            "INDF",
            "ANTM",
            "MDKA",
            "GOTO",
        }

    def test_every_asset_belongs_to_a_supported_market(self) -> None:
        for spec in default_universe():
            assert spec.market in (Market.CRYPTO, Market.IDX)

    def test_idx_assets_use_the_100_share_lot(self) -> None:
        for spec in IDX_UNIVERSE:
            assert spec.lot_size == IDX_LOT_SIZE
            assert spec.quote_asset == "IDR"
            assert spec.sector

    def test_no_invented_tick_sizes_or_precision(self) -> None:
        """SPEC 4: exchange metadata arrives with a PHASE 2 provider, not from
        a guess written here."""
        for spec in default_universe():
            assert spec.tick_size is None
            assert spec.price_precision is None

    def test_crypto_pairs_declare_base_and_quote(self) -> None:
        for spec in CRYPTO_UNIVERSE:
            assert spec.base_asset
            assert spec.quote_asset == "USDT"
            assert spec.asset_class == "CRYPTO_SPOT"

    def test_no_crypto_pair_is_quoted_in_rupiah(self) -> None:
        """PASAL 6 and 33: USDT pairs only, no IDR pair in the crypto engine.

        Stated as its own assertion rather than left implied by the one above,
        because the failure it guards is asymmetric.  ARUNA's only crypto
        source does not list these pairs against rupiah at all, so an IDR row
        here is not a preference violated - it is an asset that seeds cleanly,
        reports enabled, and returns nothing forever.
        """
        for spec in CRYPTO_UNIVERSE:
            assert not spec.symbol.endswith("/IDR")
            assert spec.quote_asset != "IDR"

    def test_each_market_has_exactly_one_quote_currency(self) -> None:
        """Uniform quoting *within* a market keeps cross-asset comparison from
        silently mixing currencies.

        This replaces an assertion that both markets shared a single quote
        currency, which PASAL 6 makes impossible: crypto is USDT, IDX is IDR.
        The property worth keeping was never "one currency everywhere" - it
        was "never two currencies in one comparison", and that is what is
        checked here.  The two markets' figures are not addable, and nothing
        in ARUNA adds them.
        """
        by_market: dict[Market, set[str | None]] = {}
        for spec in default_universe():
            by_market.setdefault(spec.market, set()).add(spec.quote_asset)

        assert by_market[Market.CRYPTO] == {"USDT"}
        assert by_market[Market.IDX] == {"IDR"}


class TestIdxTickBands:
    @pytest.mark.parametrize(
        ("price", "tick"),
        [(50, 1), (199, 1), (200, 2), (499, 2), (500, 5), (1_999, 5), (2_000, 10), (5_000, 25)],
    )
    def test_banded_fraksi_harga(self, price: int, tick: int) -> None:
        assert idx_tick_size(price) == tick


class TestOverrideFile:
    def test_defaults_apply_when_no_file_exists(self, tmp_path: Path) -> None:
        assert load_universe(tmp_path / "absent.json") == default_universe()

    def test_file_replaces_the_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "universe.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "market": "IDX",
                        "symbol": "UNVR",
                        "display_name": "Unilever Indonesia Tbk",
                        "asset_class": "IDX_EQUITY",
                        "quote_asset": "IDR",
                        "sector": "Consumer Non-Cyclicals",
                        "lot_size": 100,
                    }
                ]
            ),
            encoding="utf-8",
        )
        universe = load_universe(path)
        assert len(universe) == 1
        assert universe[0].symbol == "UNVR"

    def test_forex_alias_in_the_override_is_rejected(self, tmp_path: Path) -> None:
        """``FOREX`` is legal since 2026-08-27; its aliases still are not."""
        path = tmp_path / "universe.json"
        path.write_text(
            json.dumps([{"market": "FX", "symbol": "EURUSD", "asset_class": "FX"}]),
            encoding="utf-8",
        )
        with pytest.raises(ConfigError):
            load_universe(path)

    def test_malformed_json_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "universe.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ConfigError, match="cannot read universe file"):
            load_universe(path)

    def test_non_array_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "universe.json"
        path.write_text(json.dumps({"symbol": "BBCA"}), encoding="utf-8")
        with pytest.raises(ConfigError, match="JSON array"):
            load_universe(path)

    def test_empty_array_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "universe.json"
        path.write_text("[]", encoding="utf-8")
        with pytest.raises(ConfigError, match="defines no assets"):
            load_universe(path)

    def test_missing_required_field_is_reported_with_its_index(self, tmp_path: Path) -> None:
        path = tmp_path / "universe.json"
        path.write_text(json.dumps([{"market": "IDX"}]), encoding="utf-8")
        with pytest.raises(ConfigError, match=r"\[0\] is invalid"):
            load_universe(path)
