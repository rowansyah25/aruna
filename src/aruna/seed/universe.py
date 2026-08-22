"""The starting tradable universe.

SPEC 2 wants crypto assets to be easy to add; SPEC 3 forbids hardcoding the IDX
list.  Both are satisfied the same way: the constants below are defaults, and
``config/universe.json`` overrides them wholesale when present, so changing the
universe never requires touching Python or writing a migration.

Deliberately absent: tick sizes and price precision.  Those are exchange
metadata, and inventing them here would be exactly the fabricated data SPEC 4
prohibits.  They stay NULL.

The Binance spot adapter can now answer for them - ``fetch_metadata`` reads
``tickSize`` from ``/api/v3/exchangeInfo`` - but nothing calls it during seed
yet, so the columns are still NULL in practice.  That is a known gap, not a
solved problem: until the seed path reads the venue, any number in these
columns would be a guess.
"""

from __future__ import annotations

import json
from pathlib import Path

from aruna.core.config import PROJECT_ROOT
from aruna.core.enums import Market
from aruna.core.errors import ConfigError
from aruna.db.repositories.universe import AssetSpec

#: Optional override file.  Same shape as the constants below, as JSON.
UNIVERSE_FILE = PROJECT_ROOT / "config" / "universe.json"

#: IDX trades in lots of 100 shares.
IDX_LOT_SIZE = 100

#: IDX price ticks (fraksi harga) are banded by price, so they cannot live in a
#: single per-asset column.  Kept here as (price_below, tick); the last entry's
#: bound is None, meaning "at or above the previous bound".
IDX_TICK_BANDS: tuple[tuple[int | None, int], ...] = (
    (200, 1),
    (500, 2),
    (2_000, 5),
    (5_000, 10),
    (None, 25),
)


def idx_tick_size(price: float) -> int:
    """Tick size for an IDX price, per the banded fraksi harga table."""
    for bound, tick in IDX_TICK_BANDS:
        if bound is None or price < bound:
            return tick
    return IDX_TICK_BANDS[-1][1]


# ---------------------------------------------------------------------------
# SPEC 2 - crypto MVP
# ---------------------------------------------------------------------------

def _crypto(base: str, name: str, quote: str = "USDT") -> AssetSpec:
    return AssetSpec(
        market=Market.CRYPTO,
        symbol=f"{base}/{quote}",
        display_name=name,
        asset_class="CRYPTO_SPOT",
        base_asset=base,
        quote_asset=quote,
    )


#: The five SPEC 2 coins, quoted in USDT (PASAL 6, 33).
#:
#: An IDR quote is not merely discouraged here, it is unavailable: the crypto
#: engine's only source is Binance spot, which does not list these pairs
#: against rupiah at all.  Seeding ``BTC/IDR`` would therefore create an asset
#: no adapter can ever fetch - a row that looks configured and returns nothing.
#:
#: The uniform quote also keeps cross-asset comparison honest, which is the
#: same reason the previous IDR-only set was uniform.  What changed is the
#: currency, not the principle.  IDX assets stay in IDR; the two markets are
#: never compared against each other in one number.
CRYPTO_UNIVERSE: tuple[AssetSpec, ...] = (
    _crypto("BTC", "Bitcoin / Tether"),
    _crypto("ETH", "Ethereum / Tether"),
    _crypto("SOL", "Solana / Tether"),
    _crypto("BNB", "BNB / Tether"),
    _crypto("XRP", "XRP / Tether"),
)


# ---------------------------------------------------------------------------
# SPEC 3 - IDX starting universe (an example, not a fixed list)
# Sectors follow the IDX-IC classification.
# ---------------------------------------------------------------------------


def _idx(symbol: str, name: str, sector: str) -> AssetSpec:
    return AssetSpec(
        market=Market.IDX,
        symbol=symbol,
        display_name=name,
        asset_class="IDX_EQUITY",
        quote_asset="IDR",
        sector=sector,
        lot_size=IDX_LOT_SIZE,
    )


IDX_UNIVERSE: tuple[AssetSpec, ...] = (
    _idx("BBCA", "Bank Central Asia Tbk", "Financials"),
    _idx("BBRI", "Bank Rakyat Indonesia (Persero) Tbk", "Financials"),
    _idx("BMRI", "Bank Mandiri (Persero) Tbk", "Financials"),
    _idx("BBNI", "Bank Negara Indonesia (Persero) Tbk", "Financials"),
    _idx("TLKM", "Telkom Indonesia (Persero) Tbk", "Infrastructures"),
    _idx("ASII", "Astra International Tbk", "Industrials"),
    _idx("ICBP", "Indofood CBP Sukses Makmur Tbk", "Consumer Non-Cyclicals"),
    _idx("INDF", "Indofood Sukses Makmur Tbk", "Consumer Non-Cyclicals"),
    _idx("ANTM", "Aneka Tambang Tbk", "Basic Materials"),
    _idx("MDKA", "Merdeka Copper Gold Tbk", "Basic Materials"),
    _idx("GOTO", "GoTo Gojek Tokopedia Tbk", "Technology"),
)


def default_universe() -> tuple[AssetSpec, ...]:
    return CRYPTO_UNIVERSE + IDX_UNIVERSE


def load_universe(path: Path | None = None) -> tuple[AssetSpec, ...]:
    """Universe from ``config/universe.json`` if it exists, else the defaults."""
    target = path or UNIVERSE_FILE
    if not target.is_file():
        return default_universe()

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read universe file {target}: {exc}") from exc

    if not isinstance(payload, list):
        raise ConfigError(f"{target} must contain a JSON array of asset objects")

    specs: list[AssetSpec] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise ConfigError(f"{target}[{index}] is not an object")
        try:
            specs.append(
                AssetSpec(
                    market=Market(str(entry["market"]).upper()),
                    symbol=str(entry["symbol"]),
                    display_name=str(entry.get("display_name", entry["symbol"])),
                    asset_class=str(entry["asset_class"]),
                    base_asset=entry.get("base_asset"),
                    quote_asset=entry.get("quote_asset"),
                    sector=entry.get("sector"),
                    lot_size=entry.get("lot_size"),
                    price_precision=entry.get("price_precision"),
                    metadata=entry.get("metadata"),
                )
            )
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"{target}[{index}] is invalid: {exc}") from exc

    if not specs:
        raise ConfigError(f"{target} defines no assets")
    return tuple(specs)


__all__ = [
    "CRYPTO_UNIVERSE",
    "IDX_LOT_SIZE",
    "IDX_TICK_BANDS",
    "IDX_UNIVERSE",
    "UNIVERSE_FILE",
    "default_universe",
    "idx_tick_size",
    "load_universe",
]
