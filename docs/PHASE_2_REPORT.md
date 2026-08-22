# PHASE 2 delivery report

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

Per SPEC 49. Written after the build, from actual runs against live providers
and a live database.

- **Scope delivered:** crypto market data, IDX market data (SPEC 45, PHASE 2)
- **Crypto source:** Indodax — registered with Bappebti/OJK
- **IDX source:** Yahoo Finance chart endpoint — unofficial, **delayed**
- **Trading mode:** still PAPER only. No analysis, no signals.

---

## The provider decision changed during the build

You chose Binance. **Binance is unreachable from this network**, and the reason
is regulatory rather than technical:

```
api.binance.com -> internetbaik.telkomsel.com (202.3.218.139)
SOA record:        trustpositifkominfo
TLS certificate:   CN=internetbaik.telkomsel.com, O=PT Telekomunikasi Selular
```

That is the Kominfo TrustPositif block enforced by Telkomsel. Binance is not a
registered crypto asset trader in Indonesia. SPEC 47 anticipates exactly this
(*"Jangan menganggap semua exchange/platform legal"*), so ARUNA switched
sources rather than routing around a government block.

Candidates were probed rather than assumed:

| Source | Reachable | Verdict |
|---|---|---|
| **Indodax** | yes | Bappebti/OJK registered, order book, 1m candles — **chosen** |
| Tokocrypto | partly | registered, but klines/depth now demand an API key (`code 3701`) |
| CoinGecko | yes | 30-minute minimum candle granularity, no order book — cannot serve 1m–15m |
| Coinpaprika | yes | aggregate stats only |
| Binance | **no** | TrustPositif block |

**Consequence you should know about:** Indodax lists SOL, BNB and XRP against
IDR only. The universe is therefore IDR-quoted (`BTC/IDR`, `ETH/IDR`, `SOL/IDR`,
`BNB/IDR`, `XRP/IDR`) rather than the USDT pairs in the specification. Both
markets now share one quote currency, which keeps cross-market comparison from
silently mixing two. The five retired USDT rows were **disabled, not deleted**
— their history stays intact (SPEC 35).

## 1. Project structure (additions)

```
src/aruna/data/
├─ models.py       Quote, Candle, OrderBook, Snapshot, Provenance
├─ provider.py     MarketDataProvider + ProviderCapabilities
├─ quality.py      SPEC 5 gate, gap detection
├─ resample.py     exact aggregation for non-native intervals
├─ registry.py     provider selection from config
├─ http.py         shared client: timeout, retry, latency measurement
├─ ingest.py       provider -> gate -> storage, poll loop
├─ crypto/indodax.py
└─ idx/yahoo.py
src/aruna/db/repositories/market_data.py
src/aruna/health/providers.py
migrations/0003_market_data.sql
tests/test_data_quality.py, tests/test_data_models.py
```

## 2. Files created

63 Python files (9,982 lines), 3 SQL migrations (342 lines) — up from 47 files
and 2 migrations at the end of PHASE 1.

## 3. Dependencies

Added `httpx` (already present via python-telegram-bot) and `websockets`.

**`yfinance` was installed and then not used.** Yahoo's chart endpoint — the one
`yfinance` wraps — is reachable directly, is async-native, and returns the
metadata ARUNA needs. Using it directly avoids pulling pandas and numpy into a
runtime that needs neither. If Yahoo tightens access, swapping in `yfinance` is
a change confined to `data/idx/yahoo.py`.

## 4. Windows setup

Unchanged: `scripts\setup.ps1 -Dev`.

## 5. `.env.example`

New sections: provider selection (`ARUNA_CRYPTO_PROVIDER=indodax`,
`ARUNA_IDX_PROVIDER=yahoo`) and the SPEC 5 quality thresholds
(`ARUNA_DATA_*`). A blank provider disables that market and ARUNA reports
`DATA SOURCE UNAVAILABLE` for it rather than substituting another feed.

## 6. How to run

```powershell
.\.venv\Scripts\python.exe -m aruna providers   # what each source offers, and probe it
```

```powershell
.\.venv\Scripts\python.exe -m aruna fetch       # backfill candles + snapshot
```

`fetch` takes `--market`, `--symbols`, `--intervals`, `--limit`,
`--no-snapshot`. `seed --prune` retires assets that left the universe.

## 7. How to test

Unchanged: `pytest`, `pytest -m "not integration"`, `ruff check src tests`.

## 8. Test results

```
377 passed in 114.41s
ruff check src tests — All checks passed
```

Up from 306 at the end of PHASE 1.

| Module | Tests | Covers |
|---|---:|---|
| `test_db_integration.py` | 42 | live MySQL round trips |
| `test_config.py` | 40 | guards, session pinning, secrets |
| `test_data_models.py` | 38 | provenance, spread maths, resampling exactness, registry, capability honesty |
| `test_migrations.py` | 36 | splitter, checksums, schema invariants |
| `test_data_quality.py` | 32 | every SPEC 5 condition |
| `test_clock.py` | 30 | IDX sessions, crypto bands |
| `test_telegram.py` | 29 | registry, honest unavailability |
| `test_enums.py` | 27 | domain vocabulary |
| `test_health.py` | 27 | aggregation, debounce, transitions |
| `test_seed.py` | 22 | universe config |
| `test_redaction.py` | 21 | secret scrubbing |
| `test_cli.py` | 14 | command wiring |
| `test_runtime_state.py` | 11 | kill switch |
| `test_logging.py` | 8 | pipeline and redaction |

### Verified against live sources

- **Indodax** reachable, 173 ms, quality `OK`, spread 1.57 bps on BTC/IDR,
  order book depth captured.
- **Yahoo** reachable, 147 ms, correctly reported `STALE` (Saturday; last trade
  Friday 15:34 WIB) and `DELAYED ~15m`, `market_open=False`, `tradeable=False`.
- **4,720 candles** stored in MySQL: 5 crypto assets × 4 intervals × 200 bars,
  plus 3 IDX symbols × 2 intervals × 120 bars.
- Snapshots carry order-book depth, session, `is_realtime`, and the quality
  verdict. Candles still forming are stored `is_closed=0`.
- `aruna run` starts, ingests continuously, reports 8 healthy components.

### Six defects found and fixed during verification

Every one surfaced from running the system or from a test, not from reading code.

1. **Clock-skew tolerance was too tight.** Indodax runs ~7s ahead of this
   machine, and the 5s `future_tolerance_sec` rejected *every* tick as
   `TIMESTAMP_MISMATCH`. Raised to 30s: that threshold exists to catch the
   minutes-to-hours leakage SPEC 24 forbids, not seconds of clock drift. The
   observed skew is now recorded and surfaced by health.
2. **`spread_bps` was truncated by MySQL.** Decimal division produces 28
   significant digits; the column is `DECIMAL(14,4)`. Rounding is now explicit
   in Python via `quantize_bps`, not a silent narrowing on insert.
3. **Weekend gaps were false alarms.** `find_candle_gaps` used calendar
   duration, so a daily IDX series reported every weekend as missing data —
   27 gaps / 63 bars for BBCA. Now session-aware: 6 gaps / 11 bars, and those
   remaining are public holidays (see limitations).
4. **Indodax `/api/server_time` hangs.** The health probe timed out at 15s
   against it. Reachability is now probed with a real ticker request, which
   also exercises the same path as production traffic.
5. **A frozen feed would never have been detected.** The duplicate counter was
   reset on every accepted quote, so it could never reach the threshold. Reset
   now happens only when the quote actually differs.
6. **One-shot CLI commands started background pollers.** `aruna fetch` raced
   its own explicit work against a poll loop it did not ask for. `startup()`
   now takes `background=False`.

## 9. Data sources

| Provider | Status | Notes |
|---|---|---|
| Crypto — **indodax** | LIVE | Bappebti/OJK registered. Public endpoints, no credentials. Order book, 1m–1w candles. |
| IDX — **yahoo** | LIVE, **DELAYED** | Unofficial. Personal, non-commercial use per Yahoo's terms. No order book. |
| News | UNAVAILABLE | PHASE 4 |
| Fundamental | UNAVAILABLE | PHASE 4 |

Every stored row carries `source`, `provider_timestamp`, `server_timestamp` and
`latency_ms` (SPEC 4). `provider_events` records disconnects, failures, quality
rejections and detected gaps, so a later loss autopsy (SPEC 25) can distinguish
"the model was wrong" from "the data was missing".

## 10. Features implemented

**Provider abstraction.** `ProviderCapabilities` forces each adapter to declare
`is_realtime`, `expected_delay_sec`, `transport`, supported intervals, and its
regulatory standing. "Do not claim realtime" (SPEC 3, 5) is enforced by the
type, not by remembering.

**SPEC 5 quality gate.** Every condition the specification names: stale,
duplicate, missing, timestamp mismatch, abnormal price, abnormal spread,
provider disconnect, latency spike. Stateful per symbol, since most are only
visible in sequence. IDX gets its own staleness threshold measured *on top of*
the declared delay. The gate never repairs anything — it returns a verdict, and
a non-OK verdict blocks signal generation downstream.

**Storage with provenance and verdict.** Rejected observations are stored,
flagged, not discarded. Candles use `INSERT ... ON DUPLICATE KEY UPDATE` so
re-fetching a window refreshes rather than duplicates.

**Exact resampling.** Indodax rejects 3m and 10m; those are built from 1m bars
by arithmetic. A bucket missing any constituent bar is dropped and reported,
never averaged, and derived bars are labelled `indodax:resampled(1m)` so nothing
can mistake them for published data.

**Ingestion service.** Backfill plus a poll loop. IDX is not polled outside
trading hours — the last price is already recorded and will not change.

**Health.** A provider is DOWN when unreachable, DEGRADED when it answers but
its data fails quality checks. A stalled feed returning HTTP 200 must not read
as healthy.

**Telegram.** `/crypto`, `/stocks`, `/btc`, `/eth`, `/sol`, `/bbca`, `/bbri`,
`/bmri` now serve real recorded data, labelled `REALTIME` / `DELAYED ~15m` /
`MARKET CLOSED`, and state plainly that this is data, not a recommendation.

**CLI.** `aruna providers` and `aruna fetch`; `aruna seed --prune`.

## 11. Dummy / not implemented

**Registered but not built** — 11 Telegram commands: `/council`, `/signals`,
`/today`, `/performance`, `/weekly`, `/monthly`, `/autopsy`, `/research`,
`/proposals`, `/approve`, `/reject`. Each names the phase it waits for.

**Absent from this build** — technical indicators, market structure, regime
detection, news, fundamentals, every AI agent, the council and its protest
rounds, veto, judge, prediction lock, paper trading, outcome sampling, loss
autopsy, counterfactuals, ghost signals, calibration, backtest, walk-forward,
out-of-sample, decision replay, shadow models, drift detection, reports.

**Vocabulary without behaviour** — the regime, agent, veto and loss-cause enums
remain a storage contract only. Nothing computes them.

## 12. Limitations

**Polling, not streaming.** Neither reachable provider offers a public
market-data websocket. SPEC 5 asks for streaming *if available*; it is not, so
ARUNA polls every 5 seconds and both adapters declare `transport=POLL`.

**IDX data is delayed and the real figure is unknown.** Yahoo does not return
`exchangeDataDelayedBy` for Jakarta symbols. ARUNA assumes 15 minutes and treats
it as a **floor** — the true delay may be larger, never smaller. Nothing
describes this feed as realtime.

**Yahoo is unofficial.** Terms permit personal, non-commercial use. It is not an
IDX-licensed feed, and corporate actions may restate history without notice. If
ARUNA ever becomes commercial, this source must be replaced.

**Still no IDX holiday calendar.** The remaining 11 "missing bars" for BBCA are
almost certainly public holidays. `IdxCalendar.load_holidays()` exists and is
unpopulated; it needs a holiday source.

**3m and 10m crypto candles are derived, not published.** Exact, but only where
1m coverage is complete — and they are labelled as derived.

**IDX 3D and 5D are not candle intervals.** SPEC 3's 3D/5D are *trading-day*
prediction horizons. Calendar resampling of daily bars cannot express them —
weekends make buckets uneven — so they belong to PHASE 7 outcome evaluation over
daily candles, not to this phase.

**Tick storage is sampled, not complete.** Every observation is quality-checked;
only storage is thinned to one row per 5 seconds per symbol.

**Indodax USDT coverage is thin.** Only 13 USDT pairs, of the five MVP coins
just BTC and ETH. Hence IDR quoting.

**No rate-limit budgeting.** Retries honour `Retry-After`, but ARUNA does not
model either provider's quota. With 16 assets on a 5-second poll this has not
been a problem; a larger universe would need it.

**Not verified on a VPS**, and not verified during IDX trading hours — this
build was exercised on a Saturday, so the IDX open-market path is covered by
unit tests over the exchange calendar rather than by live observation.

## Next phase

PHASE 3: technical indicators, market structure, momentum, volume, volatility,
regime. SPEC 45's precondition is met — PHASE 2 is runnable, ingesting from two
live sources, and its full suite passes.
