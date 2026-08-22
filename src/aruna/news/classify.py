"""News classification: category, importance, sentiment, asset linkage.

**This is a keyword lexicon, not language understanding.** It matches terms in
Indonesian and English against curated lists. It will miss sarcasm, negation
("bukan gagal"), context, and anything phrased unusually.

That limitation is the reason every verdict carries a confidence, and why
:class:`~aruna.news.models.Sentiment` has both NEUTRAL (the lexicon looked and
found balance) and UNKNOWN (the lexicon found nothing to go on). Reporting
UNKNOWN as NEUTRAL would turn "I cannot tell" into a finding, which is exactly
the false precision SPEC 6 and SPEC 49 warn against.

A stronger classifier is a model change and belongs under SPEC 36 - research,
backtest, validation, human approval - not smuggled in here.
"""

from __future__ import annotations

import re
import unicodedata

from aruna.core.enums import Market
from aruna.news.models import Importance, NewsCategory, Sentiment

# ---------------------------------------------------------------------------
# Lexicons. Indonesian first, since most IDX coverage is Indonesian.
# ---------------------------------------------------------------------------

_CATEGORY_TERMS: dict[NewsCategory, tuple[str, ...]] = {
    NewsCategory.BI_RATE: (
        "bi rate", "bi-rate", "suku bunga", "bank indonesia", "bi7drr",
        "interest rate", "rate cut", "rate hike",
    ),
    NewsCategory.INFLATION: ("inflasi", "inflation", "deflasi", "cpi", "ihk"),
    NewsCategory.RUPIAH: ("rupiah", "kurs", "nilai tukar", "exchange rate", "usd/idr"),
    NewsCategory.DIVIDEND: ("dividen", "dividend", "payout", "cum date", "ex date"),
    NewsCategory.RIGHTS_ISSUE: ("rights issue", "hmetd", "private placement"),
    NewsCategory.STOCK_SPLIT: ("stock split", "pemecahan saham", "reverse split"),
    NewsCategory.EARNINGS: (
        "laba", "rugi", "kinerja keuangan", "laporan keuangan", "pendapatan",
        "earnings", "revenue", "profit", "net income", "kuartal", "quarterly",
    ),
    NewsCategory.ACQUISITION: (
        "akuisisi", "merger", "caplok", "acquisition", "takeover", "divestasi",
    ),
    NewsCategory.MANAGEMENT: (
        "direktur utama", "dirut", "komisaris", "rups", "ceo", "resign",
        "pengunduran diri", "appointed",
    ),
    NewsCategory.CORPORATE_ACTION: (
        "buyback", "aksi korporasi", "corporate action", "obligasi", "bond issue",
    ),
    NewsCategory.COMMODITY: (
        "batu bara", "batubara", "cpo", "nikel", "emas", "minyak", "coal",
        "crude", "commodity", "komoditas",
    ),
    NewsCategory.GOVERNMENT_POLICY: (
        "pemerintah", "kebijakan", "regulasi", "ojk", "kementerian", "pajak",
        "tax", "subsidi", "policy", "government",
    ),
    NewsCategory.ETF: ("etf", "exchange traded fund", "spot etf"),
    NewsCategory.SECURITY: (
        "hack", "hacked", "exploit", "peretasan", "breach", "stolen", "rug pull",
        "scam", "phishing",
    ),
    NewsCategory.PROTOCOL_UPGRADE: (
        "upgrade", "hard fork", "fork", "mainnet", "testnet", "halving",
        "protocol", "staking",
    ),
    # One Indonesian venue name was removed from this list under PASAL 34,
    # which asks for the project to be searched for it and cleared. It was a
    # news-vocabulary term, not an API dependency, so this is a small measured
    # loss rather than a clean-up: headlines naming only that venue no longer
    # classify as EXCHANGE and fall through to the default category. The
    # remaining Indonesian terms ("bursa kripto", "tokocrypto") still catch
    # most of that traffic; how much is not measured, so it is not claimed.
    NewsCategory.EXCHANGE: (
        "exchange", "bursa kripto", "listing", "delisting", "binance", "coinbase",
        "tokocrypto",
    ),
    NewsCategory.REGULATION: (
        "regulation", "sec ", "bappebti", "regulator", "lawsuit", "gugatan",
        "banned", "dilarang", "legal",
    ),
    NewsCategory.GEOPOLITICAL: (
        "perang", "war", "sanksi", "sanction", "tariff", "tarif", "geopolit",
        "conflict", "konflik",
    ),
    NewsCategory.MACRO: (
        "the fed", "federal reserve", "fomc", "gdp", "pdb", "resesi", "recession",
        "unemployment", "makroekonomi", "macro",
    ),
    NewsCategory.SECTOR: ("sektor", "sector", "industri", "industry"),
    NewsCategory.PROJECT: ("partnership", "kemitraan", "roadmap", "launch", "peluncuran"),
}

#: Direction words only. Topic nouns such as "laba"/"rugi" are deliberately
#: absent: they signal the EARNINGS *category*, not sentiment, and counting
#: them here makes "laba turun" (profit fell) score as balanced when it is
#: plainly negative.
_POSITIVE_TERMS: frozenset[str] = frozenset({
    "naik", "menguat", "melonjak", "melesat", "untung", "tumbuh",
    "positif", "rekor", "optimis", "surplus", "cuan", "meroket", "bangkit",
    "rally", "surge", "gain", "gains", "jump", "soar", "bullish", "record",
    "growth", "profit", "beat", "upgrade", "approval", "approved", "adoption",
    "breakthrough", "recovery", "strong", "boost",
})

_NEGATIVE_TERMS: frozenset[str] = frozenset({
    "turun", "melemah", "anjlok", "merosot", "defisit", "negatif",
    "pesimis", "ambruk", "jeblok", "tertekan", "koreksi", "gagal", "krisis",
    "drop", "fall", "plunge", "crash", "loss", "losses", "bearish", "decline",
    "slump", "sink", "weak", "downgrade", "reject", "rejected", "hack",
    "exploit", "lawsuit", "ban", "banned", "fraud", "scam", "warning", "risk",
})

#: Categories that move markets on their own, regardless of wording.
_CRITICAL_CATEGORIES = frozenset({
    NewsCategory.SECURITY, NewsCategory.BI_RATE, NewsCategory.REGULATION,
})
_HIGH_CATEGORIES = frozenset({
    NewsCategory.EARNINGS, NewsCategory.DIVIDEND, NewsCategory.ACQUISITION,
    NewsCategory.RIGHTS_ISSUE, NewsCategory.ETF, NewsCategory.INFLATION,
    NewsCategory.STOCK_SPLIT,
})

_CRYPTO_HINTS = (
    "bitcoin", "btc", "ethereum", "eth", "kripto", "crypto", "altcoin",
    "solana", "sol ", "xrp", "ripple", "bnb", "binance", "blockchain",
    "token", "defi", "stablecoin",
)


def normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", folded.lower()).strip()


def classify_category(text: str) -> tuple[NewsCategory, tuple[str, ...]]:
    """First matching category, with the terms that matched.

    Ordered by specificity in ``_CATEGORY_TERMS``: 'BI rate' should win over the
    broader 'government policy' when both appear.
    """
    haystack = normalise(text)
    for category, terms in _CATEGORY_TERMS.items():
        hits = tuple(term.strip() for term in terms if term in haystack)
        if hits:
            return category, hits
    return NewsCategory.UNCLASSIFIED, ()


def classify_sentiment(text: str) -> tuple[Sentiment, float, tuple[str, ...]]:
    """Lexicon sentiment with a confidence that reflects how little it knows.

    Returns UNKNOWN - not NEUTRAL - when no term fired at all. The difference
    matters: NEUTRAL claims the item is balanced, UNKNOWN admits the lexicon had
    nothing to go on.
    """
    words = set(re.findall(r"[a-z]+", normalise(text)))
    positives = tuple(sorted(words & _POSITIVE_TERMS))
    negatives = tuple(sorted(words & _NEGATIVE_TERMS))
    total = len(positives) + len(negatives)

    if total == 0:
        return Sentiment.UNKNOWN, 0.0, ()

    score = (len(positives) - len(negatives)) / total
    # Confidence grows with how many terms fired, capped well below 1: a word
    # list cannot be highly confident about meaning.
    confidence = min(0.75, 0.25 + 0.15 * total) * abs(score)
    matched = positives + negatives

    if abs(score) < 0.25:
        return Sentiment.NEUTRAL, round(max(confidence, 0.2), 3), matched
    if score > 0:
        return Sentiment.POSITIVE, round(confidence, 3), matched
    return Sentiment.NEGATIVE, round(confidence, 3), matched


def classify_importance(category: NewsCategory, sentiment_confidence: float) -> Importance:
    if category in _CRITICAL_CATEGORIES:
        return Importance.CRITICAL
    if category in _HIGH_CATEGORIES:
        return Importance.HIGH
    if category is NewsCategory.UNCLASSIFIED:
        return Importance.LOW
    return Importance.MEDIUM if sentiment_confidence >= 0.3 else Importance.LOW


def infer_market(text: str, default: Market | None = None) -> Market | None:
    haystack = normalise(text)
    if any(hint in haystack for hint in _CRYPTO_HINTS):
        return Market.CRYPTO
    return default


def link_symbols(text: str, known: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Match an item to assets.

    ``known`` maps a canonical symbol to the aliases worth searching for. IDX
    tickers are matched as whole words - a bare 'ANTM' inside another word is
    not a mention, and four-letter tickers collide easily with ordinary text.
    """
    haystack = normalise(text)
    hits: list[str] = []
    for symbol, aliases in known.items():
        for alias in aliases:
            token = normalise(alias)
            if not token:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", haystack):
                hits.append(symbol)
                break
    return tuple(sorted(set(hits)))


__all__ = [
    "classify_category",
    "classify_importance",
    "classify_sentiment",
    "infer_market",
    "link_symbols",
    "normalise",
]
