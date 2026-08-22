"""News, fundamentals, and correlation (PHASE 4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.analysis.correlation import (
    MIN_OVERLAP,
    build_matrix,
    concentration_warning,
    correlate,
    pearson,
)
from aruna.analysis.series import CandleSeries
from aruna.core.enums import Horizon, Market, ValuationVerdict
from aruna.data.models import Candle, Provenance
from aruna.fundamental.engine import FundamentalEngine
from aruna.fundamental.models import METRIC_FIELDS, Fundamentals
from aruna.news.classify import (
    classify_category,
    classify_importance,
    classify_sentiment,
    infer_market,
    link_symbols,
)
from aruna.news.models import Importance, NewsCategory, NewsItem, Sentiment
from aruna.news.rss import Feed, RssNewsProvider

NOW = datetime(2026, 8, 15, 4, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def make_series(symbol: str, closes: list[float], *, start: datetime = NOW) -> CandleSeries:
    candles = [
        Candle(
            market=Market.CRYPTO,
            symbol=symbol,
            interval=Horizon.H1,
            open_time=start + timedelta(hours=i),
            close_time=start + timedelta(hours=i + 1),
            open=Decimal(str(c)),
            high=Decimal(str(c + 1)),
            low=Decimal(str(c - 1)),
            close=Decimal(str(c)),
            volume=Decimal(10),
            provenance=Provenance(source="test", server_timestamp=start),
        )
        for i, c in enumerate(closes)
    ]
    return CandleSeries.from_candles(candles)


class TestPearson:
    def test_perfect_positive(self) -> None:
        assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)

    def test_perfect_negative(self) -> None:
        assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)

    def test_no_variance_is_none_not_zero(self) -> None:
        """A flat series has nothing to correlate. Returning 0 would claim
        'independent', which is a different and unsupported statement."""
        assert pearson([5, 5, 5, 5], [1, 2, 3, 4]) is None

    def test_mismatched_lengths(self) -> None:
        assert pearson([1, 2, 3], [1, 2]) is None


class TestCorrelate:
    def test_identical_movement_correlates(self) -> None:
        closes = [100 + (i % 7) * 2 for i in range(40)]
        pair = correlate(
            make_series("A", [float(c) for c in closes]),
            make_series("B", [float(c * 3) for c in closes]),
        )
        assert pair is not None
        assert pair.coefficient == pytest.approx(1.0)
        assert pair.strength == "STRONG"
        assert pair.reliable

    def test_opposite_movement_correlates_negatively(self) -> None:
        rising = [float(100 + i * (1 + i % 3)) for i in range(40)]
        falling = [float(400 - v) for v in rising]
        pair = correlate(make_series("A", rising), make_series("B", falling))
        assert pair is not None
        assert pair.coefficient < -0.9
        assert pair.direction == "NEGATIVE"

    def test_computed_on_returns_not_prices(self) -> None:
        """Two series drifting up correlate strongly on price but their
        bar-to-bar returns need not agree - the classic spurious result."""
        a = make_series("A", [float(100 + i) for i in range(60)])
        b = make_series("B", [float(100 + i * (1 if i % 2 else 3)) for i in range(60)])
        pair = correlate(a, b)
        assert pair is not None
        assert abs(pair.coefficient) < 0.999

    def test_non_overlapping_windows_do_not_pair(self) -> None:
        """Bars are joined by timestamp; pairing the Nth of each would be wrong
        the moment one series has a gap."""
        early = make_series("A", [float(100 + i) for i in range(30)], start=NOW)
        late = make_series(
            "B", [float(100 + i) for i in range(30)], start=NOW + timedelta(days=30)
        )
        assert correlate(early, late) is None

    def test_thin_overlap_is_flagged_unreliable(self) -> None:
        closes = [float(100 + (i % 5)) for i in range(10)]
        pair = correlate(make_series("A", closes), make_series("B", closes))
        assert pair is not None
        assert pair.overlap < MIN_OVERLAP
        assert not pair.reliable


class TestMatrix:
    def test_every_distinct_pair(self) -> None:
        closes = [float(100 + (i % 6) * 2) for i in range(40)]
        matrix = build_matrix(
            {
                "A": make_series("A", closes),
                "B": make_series("B", [c * 2 for c in closes]),
                "C": make_series("C", [400 - c for c in closes]),
            },
            interval="1h",
            computed_at=NOW,
        )
        assert len(matrix.pairs) == 3  # AB, AC, BC

    def test_concentration_warning_names_the_pairs(self) -> None:
        closes = [float(100 + (i % 6) * 2) for i in range(40)]
        matrix = build_matrix(
            {"A": make_series("A", closes), "B": make_series("B", [c * 2 for c in closes])},
            interval="1h",
            computed_at=NOW,
        )
        warning = concentration_warning(matrix)
        assert warning is not None
        assert "move as one position" in warning

    def test_no_warning_when_uncorrelated(self) -> None:
        matrix = build_matrix({}, interval="1h", computed_at=NOW)
        assert concentration_warning(matrix) is None


# ---------------------------------------------------------------------------
# News classification
# ---------------------------------------------------------------------------


class TestNewsCategory:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("BI Rate dipertahankan di level 5,25%", NewsCategory.BI_RATE),
            ("Inflasi Juli tercatat 2,8%", NewsCategory.INFLATION),
            ("Rupiah menguat ke Rp 15.800", NewsCategory.RUPIAH),
            ("Emiten bagikan dividen Rp 120 per saham", NewsCategory.DIVIDEND),
            ("Perusahaan umumkan rights issue", NewsCategory.RIGHTS_ISSUE),
            ("Laba bersih naik 20% pada kuartal II", NewsCategory.EARNINGS),
            ("Binance exchange hacked, funds stolen", NewsCategory.SECURITY),
            ("Ethereum mainnet upgrade goes live", NewsCategory.PROTOCOL_UPGRADE),
            ("Harga batu bara melonjak", NewsCategory.COMMODITY),
        ],
    )
    def test_known_headlines(self, text: str, expected: NewsCategory) -> None:
        category, terms = classify_category(text)
        assert category is expected
        assert terms

    def test_unmatched_text_is_unclassified(self) -> None:
        category, terms = classify_category("Sesuatu yang tidak berhubungan sama sekali")
        assert category is NewsCategory.UNCLASSIFIED
        assert terms == ()


class TestNewsSentiment:
    def test_positive_headline(self) -> None:
        sentiment, confidence, terms = classify_sentiment("Laba naik, saham menguat, rekor")
        assert sentiment is Sentiment.POSITIVE
        assert confidence > 0
        assert terms

    def test_negative_headline(self) -> None:
        sentiment, _, _ = classify_sentiment("Saham anjlok, rugi besar, krisis")
        assert sentiment is Sentiment.NEGATIVE

    def test_no_signal_is_unknown_not_neutral(self) -> None:
        """UNKNOWN admits the lexicon had nothing to go on. NEUTRAL would claim
        the item is balanced, which is a finding this cannot support."""
        sentiment, confidence, terms = classify_sentiment("Rapat dijadwalkan hari Selasa")
        assert sentiment is Sentiment.UNKNOWN
        assert confidence == 0.0
        assert terms == ()

    def test_balanced_terms_are_neutral(self) -> None:
        sentiment, _, _ = classify_sentiment("Saham naik lalu turun lagi")
        assert sentiment is Sentiment.NEUTRAL

    def test_topic_nouns_do_not_carry_sentiment(self) -> None:
        """'laba' and 'rugi' signal the EARNINGS category, not direction.
        Scoring them as sentiment made 'laba turun' read as balanced."""
        sentiment, _, _ = classify_sentiment("Laba emiten turun tajam")
        assert sentiment is Sentiment.NEGATIVE

    def test_confidence_stays_modest(self) -> None:
        """A word list must never look highly confident about meaning."""
        _, confidence, _ = classify_sentiment(
            "naik menguat melonjak untung tumbuh positif rekor optimis surplus"
        )
        assert confidence <= 0.75


class TestNewsImportance:
    def test_security_is_critical(self) -> None:
        assert classify_importance(NewsCategory.SECURITY, 0.0) is Importance.CRITICAL

    def test_bi_rate_is_critical(self) -> None:
        assert classify_importance(NewsCategory.BI_RATE, 0.0) is Importance.CRITICAL

    def test_earnings_is_high(self) -> None:
        assert classify_importance(NewsCategory.EARNINGS, 0.0) is Importance.HIGH

    def test_unclassified_is_low(self) -> None:
        assert classify_importance(NewsCategory.UNCLASSIFIED, 0.9) is Importance.LOW


class TestSymbolLinking:
    def test_ticker_matched_as_whole_word(self) -> None:
        aliases = {"BBCA": ("BBCA",), "ANTM": ("ANTM",)}
        assert link_symbols("Saham BBCA ditutup menguat", aliases) == ("BBCA",)

    def test_ticker_inside_another_word_is_not_a_mention(self) -> None:
        """Four-letter tickers collide easily with ordinary text."""
        assert link_symbols("BBCADEF is not a ticker", {"BBCA": ("BBCA",)}) == ()

    def test_crypto_matched_by_common_name(self) -> None:
        aliases = {"BTC/USDT": ("btc", "bitcoin")}
        assert link_symbols("Bitcoin tembus rekor baru", aliases) == ("BTC/USDT",)

    def test_multiple_assets_in_one_story(self) -> None:
        aliases = {"BBCA": ("BBCA",), "BBRI": ("BBRI",)}
        assert link_symbols("BBCA dan BBRI kompak naik", aliases) == ("BBCA", "BBRI")

    def test_crypto_market_inferred_from_text(self) -> None:
        assert infer_market("Bitcoin rally continues") is Market.CRYPTO
        assert infer_market("Laba emiten naik", Market.IDX) is Market.IDX


class TestNewsItem:
    def _item(self, **kwargs) -> NewsItem:
        base = {
            "title": "Test",
            "url": "https://example.com/a",
            "source": "test",
            "published_at": NOW,
            "fetched_at": NOW,
        }
        return NewsItem(**(base | kwargs))

    def test_fingerprint_is_stable_and_url_based(self) -> None:
        """Outlets syndicate each other; one story must not count as several
        independent pieces of evidence (SPEC 17)."""
        a = self._item(title="One headline")
        b = self._item(title="A rewritten headline")
        assert a.fingerprint == b.fingerprint

    def test_different_urls_differ(self) -> None:
        assert self._item().fingerprint != self._item(url="https://x.test/b").fingerprint

    @pytest.mark.parametrize(
        ("age", "expected"),
        [
            (timedelta(minutes=10), "FRESH"),
            (timedelta(hours=6), "RECENT"),
            (timedelta(days=3), "STALE"),
            (timedelta(days=30), "OLD"),
        ],
    )
    def test_freshness_labels(self, age: timedelta, expected: str) -> None:
        item = self._item(published_at=NOW - age)
        assert item.freshness(reference=NOW) == expected

    def test_missing_publish_time_is_unknown_freshness(self) -> None:
        assert self._item(published_at=None).freshness(reference=NOW) == "UNKNOWN"


class TestRssParsing:
    def test_rss_two_point_zero(self) -> None:
        xml = """<?xml version="1.0"?><rss version="2.0"><channel>
        <title>Feed</title>
        <item>
          <title>Laba BBCA naik 15%</title>
          <link>https://example.test/a</link>
          <description>Bank mencatat kinerja positif</description>
          <pubDate>Fri, 15 Aug 2026 03:00:00 +0000</pubDate>
        </item></channel></rss>"""
        items = RssNewsProvider().parse(
            xml, Feed("test", "https://x.test", Market.IDX), symbol_aliases={"BBCA": ("BBCA",)}
        )
        assert len(items) == 1
        item = items[0]
        assert item.title == "Laba BBCA naik 15%"
        assert item.category is NewsCategory.EARNINGS
        assert item.symbols == ("BBCA",)
        assert item.published_at is not None

    def test_atom_feed(self) -> None:
        xml = """<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Bitcoin surges</title>
            <link href="https://example.test/b"/>
            <updated>2026-08-15T03:00:00Z</updated>
          </entry>
        </feed>"""
        items = RssNewsProvider().parse(xml, Feed("test", "x", Market.CRYPTO))
        assert len(items) == 1
        assert items[0].url == "https://example.test/b"

    def test_item_without_title_is_dropped(self) -> None:
        xml = """<rss><channel><item><link>https://x.test/a</link></item></channel></rss>"""
        assert RssNewsProvider().parse(xml, Feed("t", "x")) == []

    def test_malformed_xml_is_reported(self) -> None:
        from aruna.core.errors import DataSourceUnavailableError

        with pytest.raises(DataSourceUnavailableError, match="unparseable"):
            RssNewsProvider().parse("<rss><channel>", Feed("t", "x"))


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------


class TestFundamentals:
    def test_coverage_counts_reported_metrics(self) -> None:
        data = Fundamentals(symbol="X", source="t", eps=100.0, roe_pct=15.0)
        assert data.available_metrics == ("eps", "roe_pct")
        assert data.coverage == pytest.approx(2 / len(METRIC_FIELDS))

    def test_thin_coverage_is_not_usable(self) -> None:
        assert not Fundamentals(symbol="X", source="t", eps=1.0).is_usable

    def test_missing_is_none_never_zero(self) -> None:
        """A missing figure and a figure of zero mean opposite things."""
        data = Fundamentals(symbol="X", source="t")
        assert data.roa_pct is None
        assert data.coverage == 0.0


class TestValuation:
    def _data(self, **kwargs) -> Fundamentals:
        base = {
            "symbol": "TEST",
            "source": "test",
            "price_to_earnings": 15.0,
            "price_to_book": 2.0,
            "roe_pct": 12.0,
            "roa_pct": 5.0,
            "debt_to_equity": 1.0,
            "earnings_growth_pct": 5.0,
            "revenue_growth_pct": 5.0,
            "dividend_yield_pct": 2.0,
        }
        return Fundamentals(**(base | kwargs))

    def test_thin_data_is_uncertain(self) -> None:
        report = FundamentalEngine().evaluate(Fundamentals(symbol="X", source="t"))
        assert report.verdict is ValuationVerdict.UNCERTAIN
        assert report.confidence == 0.0

    def test_cheap_on_both_price_measures(self) -> None:
        report = FundamentalEngine().evaluate(
            self._data(price_to_earnings=6.0, price_to_book=0.7)
        )
        assert report.verdict is ValuationVerdict.UNDERVALUED

    def test_expensive_on_both_price_measures(self) -> None:
        report = FundamentalEngine().evaluate(
            self._data(price_to_earnings=40.0, price_to_book=8.0)
        )
        assert report.verdict is ValuationVerdict.OVERVALUED

    def test_quality_alone_does_not_make_a_stock_cheap(self) -> None:
        """A high-ROE, growing, dividend-paying company at an ordinary P/E is a
        good business, not a cheap one. Conflating the two made the engine call
        four of five IDX blue chips undervalued."""
        report = FundamentalEngine().evaluate(
            self._data(roe_pct=25.0, earnings_growth_pct=20.0, dividend_yield_pct=5.0)
        )
        assert report.verdict is not ValuationVerdict.UNDERVALUED

    def test_strengths_still_appear_in_reasons(self) -> None:
        report = FundamentalEngine().evaluate(self._data(roe_pct=25.0))
        assert any("ROE" in reason for reason in report.reasons)

    def test_weakness_becomes_a_concern(self) -> None:
        report = FundamentalEngine().evaluate(
            self._data(roe_pct=2.0, earnings_growth_pct=-15.0, debt_to_equity=5.0)
        )
        assert len(report.concerns) >= 3

    def test_negative_earnings_is_a_concern(self) -> None:
        report = FundamentalEngine().evaluate(self._data(price_to_earnings=-8.0))
        assert any("negative earnings" in c for c in report.concerns)

    def test_verdict_is_never_a_recommendation(self) -> None:
        """SPEC 7: undervalued is never an automatic BUY."""
        report = FundamentalEngine().evaluate(
            self._data(price_to_earnings=5.0, price_to_book=0.5)
        )
        assert report.verdict is ValuationVerdict.UNDERVALUED
        assert report.is_recommendation is False

        payload = report.to_dict()
        assert payload["is_recommendation"] is False
        assert "never an automatic BUY" in payload["note"]
        assert "direction" not in payload
        assert "action" not in payload
