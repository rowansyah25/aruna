# PHASE 3 delivery report

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

Per SPEC 49. Written after the build, from actual runs against stored live data.

- **Scope delivered:** technical, structure, momentum, volume, volatility,
  regime (SPEC 45, PHASE 3)
- **Still PAPER only.** No signals, no council, no direction.

---

## What PHASE 3 deliberately does not produce

A technical snapshot contains **no direction, no trade confidence, and no
target**. SPEC 6 says indicators are *evidence, not absolute truth*, and turning
evidence into a decision is the council's job in PHASE 6 — with cross-protest,
veto and judge. Emitting BUY here would bypass all of it. A test asserts the
snapshot payload carries no `direction`, `signal`, or trade `confidence` key.

## 1. Project structure (additions)

```
src/aruna/analysis/
├─ reading.py     Reading: value + sample size + reliability
├─ series.py      CandleSeries — refuses unclosed bars (SPEC 24)
├─ indicators.py  SMA, EMA, RSI, MACD, ATR, Bollinger, VWAP, volume, momentum
├─ structure.py   swings, HH/LL, S/R, breakout, false breakout, retest,
│                 rejection, compression, expansion, gap
├─ regime.py      SPEC 9 classification
├─ engine.py      candles -> TechnicalSnapshot
└─ service.py     stored candles -> snapshots -> storage
src/aruna/db/repositories/analysis.py
migrations/0004_analysis.sql
tests/test_analysis.py
```

## 2. Files created

8 new modules, 1 migration, 1 test module (51 tests).

## 3. Dependencies

**None added.** Indicators are pure Python over a few hundred bars — no numpy.
The arrays are small and the honest dependency surface is worth more than
microseconds.

Two hygiene fixes while here:

- **`httpx` is now declared.** It was imported directly by the provider
  adapters but only arrived transitively via python-telegram-bot. Depending on
  that would leave ARUNA broken with no declared reason if ptb ever swapped its
  HTTP client.
- **`yfinance`, `websockets`, `pandas`, `numpy` removed** from the environment.
  All four were installed during PHASE 2 exploration and never imported.

## 4. Windows setup

Unchanged.

## 5. `.env.example`

Unchanged — PHASE 3 reads stored candles and adds no configuration.

## 6. How to run

```powershell
.\.venv\Scripts\python.exe -m aruna analyze --verbose
```

Takes `--market`, `--symbols`, `--intervals` (default `1h,1d`), `--dry-run`,
`--verbose`.

## 7. How to test

Unchanged.

## 8. Test results

```
428 passed in 102.23s
ruff check src tests — All checks passed
```

Up from 377 at the end of PHASE 2. The 51 new tests use hand-checkable vectors
rather than recording current output — a test that asserts whatever the code
happens to produce cannot catch a wrong formula. Examples: SMA of `[1,2,3,4,5]`
period 3 is `[2,3,4]`; EMA seeds from the first SMA and the second value is
exactly 3.0; a series spanning exactly 2.0 per bar has ATR 2.0; an all-gains
series pins RSI to 100 and an all-losses series to 0.

### Verified against stored live data

`aruna analyze` over real Indodax and Yahoo candles:

```
BTC/IDR 1d   TRENDING   conf=1.00  DOWNTREND  bars=199  reliable=16/16
ETH/IDR 1h   TRENDING   conf=0.67  UPTREND    bars=199  reliable=16/16
BNB/IDR 15m  RANGING    conf=0.76  RANGE      bars=199  RETEST
SOL/IDR 15m  UNCERTAIN  conf=0.20  UPTREND    bars=199  REJECTION
BBRI 1d      TRENDING   conf=0.67  UPTREND    bars=120  reliable=16/16
BMRI 1d      RANGING    conf=1.00  RANGE      bars=120  reliable=16/16
```

Regime spread across 15 crypto series: RANGING 5, TRENDING 4, REVERSAL 2,
UNCERTAIN 2, BREAKOUT 1, LOW_VOLATILITY 1. `bars=199` from a 200-bar fetch is
the leakage guard working — the newest bar was still forming and was excluded.

### Two defects found and fixed

1. **Every asset reported `BREAKOUT_DOWN` at once.** `detect_breakout` selected
   `support[0]` — the *most-touched* level — instead of the level near current
   price. When the strongest cluster sat far from price, `close < level` was
   trivially true and everything looked like a downside break. Now the nearest
   level is used, and a level only produces a verdict if price actually reached
   it inside the confirmation window. The regime spread above is the result;
   before the fix it was a wall of one label.
2. **Analysis snapshots would have been written with sub-second precision loss.**
   Values now round in Python before insert (`_round`), the same fix pattern as
   PHASE 2's `spread_bps` — MySQL narrowing a value on insert is a silent data
   change.

## 9. Data sources

Unchanged from PHASE 2: Indodax (crypto, live) and Yahoo (IDX, delayed ~15
min). PHASE 3 adds no external calls — it reads stored candles only, which is
also why it cannot leak: the query itself asks for closed bars.

## 10. Features implemented

**Every SPEC 6 indicator:** SMA, EMA, RSI (Wilder), MACD with signal and
histogram, ATR (Wilder), Bollinger bands with %B and bandwidth, VWAP, realised
volatility, momentum, volume anomaly, volume trend.

**Every SPEC 6 structure element:** confirmed swing highs/lows, higher-high /
lower-low sequencing, clustered support and resistance, breakout, false
breakout, retest, rejection, compression, expansion, gap.

**SPEC 9 regime classification** — TRENDING, RANGING, BREAKOUT, REVERSAL,
HIGH/LOW_VOLATILITY, ACCUMULATION, DISTRIBUTION, UNCERTAIN, ANOMALY.

**Evidence, not truth (SPEC 6).** Every computation returns a `Reading`
carrying its value, sample size, and required minimum. A reading without enough
data is `reliable=False` and **does not vote** in regime classification — a
20-bar series cannot outvote the fact that it is only 20 bars.

**UNCERTAIN is a real answer.** With no reliable evidence, or when the top two
regimes are within 0.5 of each other, the verdict is UNCERTAIN with the
runners-up listed. Reporting the leader of a tie would manufacture certainty.

**Leakage guard is structural (SPEC 24).** `CandleSeries` refuses unclosed
bars, so no indicator can read an unsettled price — and no future indicator can
forget to. `as_of` on every stored row is the newest settled bar behind it,
which is what lets a PHASE 9 replay prove no future data leaked in.

**Rules, not a model.** The regime thresholds are transparent constants a human
can argue with. SPEC 36 requires model changes to pass research, backtest,
walk-forward and human approval; a learned classifier is exactly that kind of
change and does not belong smuggled in here.

**Storage.** `technical_snapshots`, `volume_snapshots`, `regimes`, keyed on
`(asset, interval, as_of)` so recomputation refreshes rather than duplicates.
Readings persist as JSON *with their sample sizes* — a column holding `47.3`
with no record of how many bars produced it can only be believed, not weighed.

## 11. Dummy / not implemented

**NEWS_SHOCK is never returned.** It requires news, which arrives in PHASE 4.
Returning it from price action alone would be a guess wearing a specific label.
A test asserts this holds for every trend structure.

**Registered but not built** — 11 Telegram commands: `/council`, `/signals`,
`/today`, `/performance`, `/weekly`, `/monthly`, `/autopsy`, `/research`,
`/proposals`, `/approve`, `/reject`.

**Absent** — news, fundamentals, correlation (PHASE 4); every AI agent, council,
protest rounds, veto, judge (PHASE 5–6); prediction lock, paper trading,
outcome sampling (PHASE 7); autopsy, counterfactual, calibration (PHASE 8);
backtest, walk-forward, replay (PHASE 9); shadow models, drift (PHASE 10).

**No analysis in the Telegram commands yet.** `/btc` and friends still show
market data only. Wiring regime output into them is cosmetic and was left until
the council can explain *why* a regime matters.

## 12. Limitations

**Thresholds are unvalidated.** `HIGH_VOL_ATR_PCT = 3.0`, `TREND_MOMENTUM_PCT
= 1.5`, and the rest are reasonable defaults, not calibrated ones. Nothing here
has been backtested — that is PHASE 9. Treat the regime output as a structured
opinion, not a measured one.

**Regime confidence is not a probability.** It blends margin of victory with
how much evidence was usable. It says "how much backs this call", not "how
likely this is to be right". Real calibration needs outcomes (PHASE 8).

**Swing detection lags by design.** A pivot needs `lookback` bars on both
sides, so the last 3 bars never contain a confirmed swing. That is correct —
calling a pivot before the confirming bars exist is look-ahead — but it means
structure reacts late, and a fast reversal will be named only after the fact.

**Support/resistance uses percentage clustering.** A fixed 0.35% tolerance
suits liquid pairs; a thin asset with wide ticks may cluster poorly. An
ATR-relative tolerance would adapt better and is not implemented.

**Volume quality varies by source.** Yahoo IDX volume is exchange-reported and
reliable; Indodax volume is single-venue, so a "volume anomaly" reflects that
venue, not global crypto activity. VWAP returns unavailable rather than falling
back to an unweighted mean when volume is missing.

**Only one interval is analysed at a time.** SPEC 10 wants multi-horizon
comparison where horizons may legitimately disagree. The storage supports it —
snapshots are keyed per interval — but nothing yet reconciles across them. That
belongs with the council in PHASE 6.

**Not validated against a reference implementation.** Indicators are tested
against hand-computed vectors, which catches wrong formulas but not subtle
convention differences (Wilder vs EMA smoothing, for instance). Comparing
against TradingView or TA-Lib on the same candles would be a worthwhile check
before any of this drives money, even paper money.

**IDX intraday analysis is thin.** Only daily IDX candles have meaningful
history stored; Yahoo caps 1m history at 7 days.

## Next phase

PHASE 4: news, fundamentals, correlation. SPEC 45's precondition is met —
PHASE 3 is runnable and its full suite passes.
