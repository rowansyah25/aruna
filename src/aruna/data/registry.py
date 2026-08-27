"""Provider selection.

Which adapter serves which market is configuration, not a hardcoded import, so
swapping a data source never means editing ingestion code.  An unknown or blank
provider name is an error at construction rather than a ``None`` that surfaces
as a confusing failure later (SPEC 4).
"""

from __future__ import annotations

from collections.abc import Callable

from aruna.core.config import DataSettings, ProviderSettings
from aruna.core.enums import Market
from aruna.core.errors import ConfigError
from aruna.data.crypto.binance import BinanceSpotProvider
from aruna.data.forex.twelvedata import TwelveDataForexProvider
from aruna.data.idx.yahoo import YahooIdxProvider
from aruna.data.provider import MarketDataProvider

ProviderFactory = Callable[[DataSettings, str], MarketDataProvider]


def _tanpa_kunci(cls: type[MarketDataProvider]) -> ProviderFactory:
    """Adapter yang tidak memerlukan kredensial.

    Kuncinya diterima lalu diabaikan, dan itu ditulis di sini alih-alih di
    konstruktor adapter: Binance dan Yahoo tidak punya kredensial sama sekali,
    dan menambahkan parameter kosong ke keduanya hanya untuk menyeragamkan
    tanda tangan akan membuat keduanya terlihat seolah bisa dikonfigurasi.
    """

    def factory(settings: DataSettings, _api_key: str) -> MarketDataProvider:
        return cls(settings)

    return factory


def _dengan_kunci(cls: type[MarketDataProvider]) -> ProviderFactory:
    """Adapter yang kredensialnya wajib sampai."""

    def factory(settings: DataSettings, api_key: str) -> MarketDataProvider:
        return cls(settings, api_key=api_key)

    return factory

#: Registered adapters, by market and configured name.
#:
#: The key is the adapter's own ``capabilities.name``, so what ``aruna
#: providers`` prints is exactly what goes in ``.env``.  ``binance-spot`` is
#: spelled out rather than shortened to ``binance``: this project also carries
#: ``binance-futures``, the two feeds quote different prices, and a bare
#: ``binance`` in a provenance column would not say which one wrote the row.
#:
#: Crypto has exactly one entry on purpose.  PASAL 5 forbids a fallback to the
#: retired IDR venue, and a second registered adapter *is* that fallback - it
#: only takes
#: one edited environment variable to select it, which is precisely the
#: silent-substitution path the rule exists to close.  If Binance is
#: unreachable, ARUNA reports ``DATA SOURCE UNAVAILABLE`` and stops (SPEC 4).
PROVIDERS: dict[Market, dict[str, ProviderFactory]] = {
    Market.CRYPTO: {"binance-spot": _tanpa_kunci(BinanceSpotProvider)},
    Market.IDX: {"yahoo": _tanpa_kunci(YahooIdxProvider)},
    #: Valas punya satu entri dengan alasan yang sama seperti crypto: adapter
    #: kedua yang terdaftar *adalah* jalur substitusi diam-diam, dan XAU/USD
    #: yang dikutip dua venue berbeda bukan angka yang sama.
    Market.FOREX: {"twelvedata": _dengan_kunci(TwelveDataForexProvider)},
}


def available(market: Market) -> tuple[str, ...]:
    return tuple(sorted(PROVIDERS.get(market, {})))


def build_provider(
    market: Market, name: str, settings: DataSettings, *, api_key: str = ""
) -> MarketDataProvider:
    factories = PROVIDERS.get(market)
    if not factories:
        raise ConfigError(f"no data providers are registered for market {market.value}")

    key = name.strip().lower()
    if not key:
        raise ConfigError(
            f"no provider configured for {market.value}. "
            f"Set the provider name in .env; available: {', '.join(available(market))}"
        )

    factory = factories.get(key)
    if factory is None:
        raise ConfigError(
            f"unknown {market.value} provider {name!r}; "
            f"available: {', '.join(available(market))}"
        )

    provider = factory(settings, api_key)

    # An adapter that declares it needs credentials and did not get any would
    # otherwise fail one request at a time, hours later, as an authentication
    # error that reads like a venue outage.  Refusing here means the operator
    # learns at startup, which is the only moment they can still fix it.
    if provider.capabilities.requires_credentials and not api_key.strip():
        raise ConfigError(
            f"{market.value} provider {key!r} requires an API key but none is set. "
            f"Set ARUNA_{market.value}_PROVIDER_API_KEY in .env"
        )
    return provider


def build_providers(
    providers: ProviderSettings,
    data: DataSettings,
    markets: tuple[Market, ...],
) -> dict[Market, MarketDataProvider]:
    """One adapter per enabled market.

    A market whose provider is unconfigured is simply absent from the result;
    callers report ``DATA SOURCE UNAVAILABLE`` for it rather than substituting
    a different market's feed.
    """
    selected: dict[Market, MarketDataProvider] = {}
    names = {
        Market.CRYPTO: providers.crypto_provider,
        Market.IDX: providers.idx_provider,
        Market.FOREX: providers.forex_provider,
    }
    #: Read here rather than inside the adapters: the key lives in
    #: ``ProviderSettings`` and the factories only ever receive
    #: ``DataSettings``, so before this map a mandatory credential could never
    #: reach the adapter that needs it.
    kunci = {
        Market.FOREX: providers.forex_provider_api_key.get_secret_value(),
    }

    for market in markets:
        name = names.get(market, "")
        if not name.strip():
            continue
        selected[market] = build_provider(
            market, name, data, api_key=kunci.get(market, "")
        )
    return selected


__all__ = ["PROVIDERS", "available", "build_provider", "build_providers"]
