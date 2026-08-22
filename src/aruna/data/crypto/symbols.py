"""Canonical symbol <-> Binance spot symbol.

ARUNA stores ``BASE/QUOTE`` with a slash - ``BTC/USDT`` - in ``assets.symbol``
and in every table that carries a symbol alongside it.  Binance wants
``BTCUSDT``: uppercase, no separator, and case-sensitive on the wire, where
``btcusdt`` comes back as ``Invalid symbol``.

The translation lives here and nowhere else, on purpose.  A second copy is how
the two forms drift apart: the futures side already keeps its own venue ->
canonical bridge (:mod:`aruna.futures.service`), and the two have to agree on
every symbol or resolution fails for all of them at once, not gradually.  One
function per direction, one test file, one place to change.

Nothing in here touches the network, so it can be exercised without a venue.
"""

from __future__ import annotations

#: Quote assets recognised when **reading** a venue symbol, longest first so
#: suffix matching is unambiguous (``ETHFDUSD`` must not read as ``ETHFD``
#: quoted in ``USD``).
#:
#: This list is a vocabulary, not a permission.  Splitting ``BTCFDUSD`` in the
#: right place is strictly better than splitting it in the wrong one, so
#: :func:`to_canonical_symbol` knows every stablecoin quote the venue actually
#: lists.  What may enter the engine is a different question, answered by
#: :data:`ENGINE_QUOTE_ASSETS` - and the two used to be the same tuple, which
#: meant ``BTC/USDC`` was translated and fetched live even though PASAL 33 says
#: USDT only.  Direction matters: reading widely, admitting narrowly.
QUOTE_ASSETS: tuple[str, ...] = ("FDUSD", "USDT", "USDC", "TUSD", "BUSD")

#: Quote assets ARUNA's crypto engine may hold, on the way *in* to the venue.
#:
#: PASAL 33 is "CRYPTO: USDT PAIRS ONLY" and PASAL 6 restricts the crypto market
#: engine to USDT-denominated pairs.  Before this constant existed the only
#: thing keeping ``BTC/USDC`` out was that nobody had seeded it: one
#: ``--symbols BTC/USDC`` reached Binance and returned a real price, from a
#: market whose price is not the same as the USDT one.  A rule that holds only
#: because of what a table happens to contain is not a rule.
#:
#: Widening this is an operator decision about a written pasal, and it belongs
#: in a written deviation - not in a tuple that quietly grew a member.
ENGINE_QUOTE_ASSETS: tuple[str, ...] = ("USDT",)

#: Separators a canonical symbol may arrive with.  ``BTC/USDT`` is the stored
#: form; the others are tolerated on input so a hand-typed ``--symbols
#: BTC-USDT`` on the CLI does not fail for a punctuation reason.
_SEPARATORS = ("/", "-", "_")


def split_canonical(symbol: str) -> tuple[str, str]:
    """``BTC/USDT`` -> ``("BTC", "USDT")``.

    Raises ``ValueError`` for anything that is not a pair ARUNA may hold, and
    that includes ``BTC/IDR`` (the venue does not list it) *and* ``BTC/USDC``
    (the venue lists it and PASAL 33 does not allow it).  Refusing here rather
    than passing the symbol down to the venue is the difference between a
    message naming the rule and an HTTP 400 whose body says ``Invalid symbol`` -
    the second one reads like an outage.

    The gate is :data:`ENGINE_QUOTE_ASSETS`, not :data:`QUOTE_ASSETS`: this is
    the inbound direction, where the question is what ARUNA is allowed to
    analyse, not what the venue happens to publish.
    """
    text = symbol.strip().upper()
    for separator in _SEPARATORS:
        text = text.replace(separator, "/")

    base, found, quote = text.partition("/")
    if not found:
        raise ValueError(
            f"{symbol!r} bukan simbol kanonik ARUNA: bentuknya BASE/QUOTE, "
            "misalnya BTC/USDT"
        )
    if not base or not quote:
        raise ValueError(
            f"{symbol!r} tidak lengkap: base dan quote dua-duanya harus terisi, "
            "misalnya BTC/USDT"
        )
    if quote not in ENGINE_QUOTE_ASSETS:
        raise ValueError(
            f"{symbol!r} memakai quote {quote}. ARUNA hanya memegang pair "
            f"berdenominasi USDT (PASAL 33 'CRYPTO: USDT PAIRS ONLY', PASAL 6); "
            f"quote yang boleh masuk: {', '.join(ENGINE_QUOTE_ASSETS)}. "
            "Melebarkan daftar ini adalah keputusan operator yang harus "
            "ditulis, bukan efek samping"
        )
    return base, quote


def to_venue_symbol(symbol: str) -> str:
    """``BTC/USDT`` -> ``BTCUSDT``.

    Uppercase and unseparated, because that is the only form the REST API
    accepts.  Lowercasing it - which is what the websocket stream names want -
    would make every REST call fail, so the two forms must never be produced by
    the same function.
    """
    base, quote = split_canonical(symbol)
    return f"{base}{quote}"


def to_canonical_symbol(venue_symbol: str) -> str:
    """``BTCUSDT`` -> ``BTC/USDT``.

    The inverse is genuinely ambiguous without a quote-asset list - ``BTCUSDT``
    could be split in four places - so the split is decided by
    :data:`QUOTE_ASSETS`, longest suffix first.  A symbol whose quote is not on
    that list raises rather than being cut at a guessed offset: a silently
    mis-split symbol would go on to match no asset row at all, and the failure
    would surface far from here.

    Reading a non-USDT symbol correctly is not the same as admitting it.
    ``ETHFDUSD`` becomes ``ETH/FDUSD`` here and is then refused by
    :func:`split_canonical` on the way back out, which is the intended shape:
    the venue's vocabulary is wider than ARUNA's mandate.
    """
    text = venue_symbol.strip().upper()
    for separator in _SEPARATORS:
        text = text.replace(separator, "")

    for quote in QUOTE_ASSETS:
        if text.endswith(quote) and len(text) > len(quote):
            return f"{text[: -len(quote)]}/{quote}"

    raise ValueError(
        f"{venue_symbol!r} tidak bisa dipecah jadi base/quote: quote-nya tidak "
        f"ada di daftar yang dikenali ({', '.join(QUOTE_ASSETS)}). Simbol ini "
        "tidak diterjemahkan dengan tebakan"
    )


__all__ = [
    "ENGINE_QUOTE_ASSETS",
    "QUOTE_ASSETS",
    "split_canonical",
    "to_canonical_symbol",
    "to_venue_symbol",
]
