# PHASE 4 delivery report

> **SUPERSEDED IN PART — dated correction, 2026-08-17.**
>
> Everything below is preserved exactly as written and is **still a true record
> of what was measured on the day it was written**. It is not edited to match
> today, because a measurement rewritten after the fact stops being a
> measurement (SPEC 49).
>
> What changed since:
>
> - ARUNA's crypto source is no longer Indodax. It is **Binance spot, USDT
>   pairs only** (PASAL 5, 6, 33). The Indodax client, config, symbol mapping
>   and environment variables were removed entirely; there is no fallback.
> - Crypto pairs are quoted **USDT**, not IDR. Any `BTC/IDR` in this document
>   is a symbol that no longer exists in the universe.
> - Statements here that Binance is unreachable from Indonesian networks, or
>   blocked by Kominfo TrustPositif, **were true as measured then and are not
>   true on this machine now**: on 2026-08-17 `api.binance.com`, `api1`, `api2`
>   and `fapi.binance.com` each answered. Reachability is a property of the
>   network a deployment sits on. Binance is still not registered with
>   Bappebti — that part was never a network claim and has not changed.
> - Crypto paper-trading costs moved from 0.30% to 0.10% per side with the
>   venue. **PnL figures in this document are not comparable to newer ones.**
> - `market_ticks` was dropped by migration 0020 (PASAL 26).
> - The IDR-quoted crypto history this report describes — candles, snapshots,
>   signals, paper trades and council sessions — was deleted by migration 0020
>   at the operator's decision. Backup: `backup/aruna_sebelum_binance_2026-08-17.sql`.
>
> See `README.md` and `migrations/0020_crypto_usdt_binance.sql` for the
> current state.

Per SPEC 49. Written after the build, from actual runs against live sources.

- **Scope delivered:** news, fundamental, correlation (SPEC 45, PHASE 4)
- **Still PAPER only.** No signals, no council, no direction.

---

## 1. Project structure (additions)

```
src/aruna/news/
├─ models.py    NewsItem with all seven SPEC 8 fields
├─ classify.py  category, importance, sentiment, asset linkage
├─ rss.py       RSS/Atom provider
└─ service.py   fetch -> dedupe -> link -> store
src/aruna/fundamental/
├─ models.py    Fundamentals, coverage
├─ engine.py    SPEC 7 valuation verdict
├─ yahoo.py     yfinance provider
└─ service.py
src/aruna/analysis/correlation.py
src/aruna/db/repositories/{news,fundamental}.py
migrations/0005_news_fundamental.sql
tests/test_phase4.py
```

## 2. Files created

11 new modules, 1 migration, 1 test module (58 tests).

## 3. Dependencies

**`yfinance` returns** — and with it pandas and numpy, which PHASE 3 removed.

That is a real cost and worth stating plainly. Yahoo's `quoteSummary` endpoint
now answers `401 Invalid Crumb` to plain HTTP clients. `yfinance` performs the
cookie/crumb handshake Yahoo's own site uses. Reimplementing that by hand would
be working around an access control we were not granted, so the library is used
instead. This is exactly the swap the PHASE 2 report said would be needed if
Yahoo tightened access.

Candles still come from the plain chart endpoint. Only fundamentals need this
path.

News adds nothing: RSS is parsed with the stdlib XML parser.

## 4. Windows setup

Unchanged.

## 5. `.env.example`

`ARUNA_NEWS_PROVIDER=rss` and `ARUNA_FUNDAMENTAL_PROVIDER=yahoo` are now set,
each with its licensing note. Blank still means `DATA SOURCE UNAVAILABLE` for
that stream.

## 6. How to run

```powershell
.\.venv\Scripts\python.exe -m aruna news
```

```powershell
.\.venv\Scripts\python.exe -m aruna fundamental
```

```powershell
.\.venv\Scripts\python.exe -m aruna correlate --market CRYPTO --interval 1h
```

## 7. How to test

Unchanged.

## 8. Test results

```
487 passed
ruff check src tests — All checks passed
```

Up from 428 at the end of PHASE 3.

### Verified against live sources

**News** — 280 items from 5 feeds in one pass:

```
cnbc-indonesia    100    coindesk        25    cointelegraph   30
detik-finance     100    kontan          25
idx-announcements   -    DATA SOURCE UNAVAILABLE: HTTP 403 to non-browser clients
bisnis-com          -    DATA SOURCE UNAVAILABLE: HTTP 403 to non-browser clients
```

39 asset links stored (BTC 19, GOTO 7, SOL 4, ETH 3, ANTM 2, XRP 2, BBRI 1,
BBCA 1). Classification working on real headlines: "Rupiah Menguat…" → RUPIAH /
POSITIVE / MEDIUM; "Harga Emas Antam Naik…" → COMMODITY / POSITIVE.

Sentiment over 7 days: `UNKNOWN 170, POSITIVE 71, NEGATIVE 31, NEUTRAL 8`.
**61% unclassified is the honest headline number** — see limitations.

**Fundamentals** — 5 IDX symbols, all FAIR_VALUE, coverage 78–100%.

**Correlation** — crypto 1h over 198 overlapping bars:

```
BTC/ETH  +0.779 STRONG      BTC/SOL  +0.629 MODERATE
ETH/SOL  +0.615 MODERATE    SOL/XRP  +0.600 MODERATE
BNB/*    +0.21..+0.28 WEAK  average |r| 0.465
! 1 strongly correlated pair - these move as one position, not several
```

### Three defects found and fixed

1. **Circular import.** `aruna.news.__init__` exported the service, which
   imports the repository, which imports the news models — the package could
   not finish initialising. Domain packages now export types and providers
   only; services are imported from their own module.
2. **Asset links were counted but never stored.** `NewsService` incremented
   `linked` and never called `link_asset`, so `news_asset_links` stayed empty
   while the summary claimed otherwise. Found by querying the table rather than
   trusting the counter.
3. **The valuation engine conflated quality with cheapness.** ROE, earnings
   growth and dividend yield were counted as evidence a stock was *cheap*, so
   four of five IDX blue chips came back UNDERVALUED. A high-ROE company can be
   expensive; that is a different claim. Valuation (P/E, P/B, and yield at half
   weight) now decides the verdict, while quality shapes confidence and appears
   as reasons and concerns. All five now read FAIR_VALUE, which is plausible
   for those names.

A fourth issue was fixed while writing tests: `laba` and `rugi` (profit/loss)
sat in the sentiment lexicon, so "laba turun" — profit fell — scored as
balanced. They are topic nouns that already drive the EARNINGS category, and
have been removed from the sentiment lists.

## 9. Data sources

| Stream | Provider | Status |
|---|---|---|
| Crypto prices | Indodax | LIVE, Bappebti/OJK registered |
| IDX prices | Yahoo chart | LIVE, delayed ~15 min |
| News | RSS × 5 | LIVE |
| Fundamentals | Yahoo via yfinance | LIVE, unofficial |

RSS was chosen because it is the one news channel publishers explicitly offer
for syndication — no scraping, no circumvented access control — and the source
URL travels with every item so any classification stays auditable (SPEC 8).

## 10. Features implemented

**News (SPEC 8).** All seven required fields per item: timestamp, source,
asset, category, importance, sentiment, freshness. 21 categories covering both
the crypto and IDX lists. Deduplication by URL fingerprint, because outlets
syndicate each other and one story must not read as several independent pieces
of evidence (SPEC 17). Both publisher time and receipt time are kept — the gap
between them is freshness, and a stale feed is invisible if only one is
recorded.

**Fundamentals (SPEC 7).** Revenue and earnings growth, EPS, ROE, ROA, DER,
free cash flow, debt, P/E, P/B, book value, dividend yield, margin, market cap.
Verdict UNDERVALUED / FAIR_VALUE / OVERVALUED / UNCERTAIN with coverage
recorded, so a verdict from three metrics stays distinguishable from one built
on twelve.

**SPEC 7's standing rule is enforced structurally.** `ValuationReport` carries
`is_recommendation=False` and a note saying undervalued is never an automatic
BUY; a test asserts the payload has no `direction` or `action` key.

**Correlation (SPEC 17, 32).** Pearson on **returns**, never raw prices — two
assets that both drift upward correlate on price while saying nothing about
co-movement. Bars are joined by timestamp, not by index, so a gap in one series
cannot silently misalign the pair. Overlap count is stored, and a concentration
warning names pairs that move as one position.

**Honest absences throughout.** A flat series returns `None` rather than 0
correlation — "no variance" and "independent" are different claims. Sentiment
returns UNKNOWN rather than NEUTRAL when the lexicon finds nothing. A missing
fundamental metric stays `NULL`, never 0.

## 11. Dummy / not implemented

**Registered but not built** — 11 Telegram commands: `/council`, `/signals`,
`/today`, `/performance`, `/weekly`, `/monthly`, `/autopsy`, `/research`,
`/proposals`, `/approve`, `/reject`.

**SPEC 7 fields with no source**: earnings quality, sector growth, corporate
action history, management assessment. Yahoo does not supply them and nothing
fabricates a substitute.

**News is not wired into regime detection.** `NEWS_SHOCK` still never fires —
connecting news to the regime classifier needs a considered rule about what
constitutes a shock, and that belongs with the council in PHASE 5–6 rather than
as a threshold guessed here.

**Absent** — every AI agent, council, protest rounds, veto, judge (PHASE 5–6);
prediction lock, paper trading, outcome sampling (PHASE 7); autopsy,
counterfactual, calibration (PHASE 8); backtest, walk-forward, replay
(PHASE 9); shadow models, drift (PHASE 10).

## 12. Limitations

**Sentiment is a keyword lexicon, not language understanding.** 61% of live
headlines came back UNKNOWN. It cannot handle negation, sarcasm, or context —
"Harga Bright Gas Pertamina Turun!" scored NEGATIVE, though a falling gas price
is good news for consumers. Every verdict carries a confidence capped at 0.75
for this reason. A real classifier is a model change and belongs under SPEC 36:
research, backtest, validation, human approval.

**Category classification is first-match.** A headline covering both a rate
decision and inflation gets one label, chosen by list order.

**Valuation thresholds are sector-blind.** A bank and a miner do not share a
fair P/B. `CHEAP_PE = 10` and friends are broad defaults, not calibrated ones,
and nothing here has been backtested — that is PHASE 9.

**Yahoo fundamentals are an aggregation, not filed statements.** Trailing twelve
months, so a fresh quarterly appears late. Banks often report no debt/equity,
where the ratio is not meaningful anyway. Restatements can change history
silently.

**Two Indonesian feeds are unreachable.** IDX's own announcements and
Bisnis.com both return 403 to non-browser clients. They are recorded as
unavailable rather than worked around — which means ARUNA has no official IDX
corporate-action feed, and SPEC 8 lists that as an IDX news category.

**Correlation is a single window, unweighted.** No rolling series, no decay, no
regime conditioning. Correlations rise sharply in a crash, and a single static
number will understate exactly the concentration risk SPEC 32 cares about most.

**No BTC dominance or market breadth** (SPEC 2). Both need broader market data
than the five-asset universe provides.

**Correlation across markets is not meaningful yet.** Crypto trades 24/7 and
IDX does not, so the timestamp join finds almost no overlap between them. Doing
this properly needs session-aligned daily returns.

**Not verified during IDX trading hours** — this build was exercised on a
Saturday, as with PHASE 2 and 3.

## Next phase

PHASE 5: AI agents, self-critic, prosecutor, risk, no-trade engine. SPEC 45's
precondition is met — PHASE 4 is runnable and its full suite passes.
