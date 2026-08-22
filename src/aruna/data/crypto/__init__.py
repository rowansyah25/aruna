"""Crypto market data adapters.

One source, Binance spot, quoted in USDT (PASAL 5, 6, 33).  The IDR-quoted
adapter that used to live beside this one was deleted rather than left behind a
flag: PASAL 5 forbids a fallback, and an importable second source is a fallback
whether or not anything is configured to use it.

Nothing here reaches an execution endpoint.  That is enforced in
:mod:`aruna.data.crypto.binance` by an allowlist the transport checks before it
opens a connection, not by this sentence (PASAL 41).
"""

from aruna.data.crypto.binance import BinanceSpotProvider

__all__ = ["BinanceSpotProvider"]
