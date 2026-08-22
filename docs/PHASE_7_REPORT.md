# PHASE 7 — Prediction lock, paper trading, multi-horizon, outcomes

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

**Version 0.7.0 · 660 tests passing · ruff clean · migrations 0001–0009 applied**

*(§10a records five defects found in an audit pass after first delivery. The
counts above are post-fix.)*

The phase that makes ARUNA accountable. Everything before it was analysis;
from here the system makes claims that can be scored, and it cannot take them
back.

---

## 1. Project structure

```
src/aruna/
  signals/
    models.py         LockedSignal, OutcomeSample, SignalOutcome, PaperTrade  (309)
    lock.py           build, judge fitness, verify, supersede                 (258)
    outcome.py        SPEC 22/23 scoring and classification                   (293)
    paper.py          SPEC 34 cost model and net PnL                          (216)
    multihorizon.py   SPEC 10 unreconciled horizon views                       (85)
    report.py         the SPEC 21 publication block                            (63)
    service.py        lock_signals() and resolve_due()                        (253)
  db/repositories/
    signals.py        append-only storage, outcomes, trades, performance      (307)
migrations/
  0008_signals.sql    5 tables, 2 triggers, paper-only constraint             (207)
tests/
  test_signals.py     47 tests                                                (585)
```

Existing files changed: `app.py`, `cli.py`, `notify/telegram/bot.py`,
`notify/telegram/formatting.py`, `agents/service.py`, `council/session.py`,
`council/judge.py`, `analysis/series.py`, `analysis/engine.py`,
`core/config.py`, `__init__.py`.

## 2. Files created

| File | What it holds |
|---|---|
| `signals/models.py` | The frozen `LockedSignal` and its fingerprint |
| `signals/lock.py` | Building a prediction, and refusing to publish one |
| `signals/outcome.py` | Scoring against the original prediction |
| `signals/paper.py` | Simulated fills, modelled costs, net PnL |
| `signals/multihorizon.py` | Each horizon's own conclusion, unreconciled |
| `signals/report.py` | The SPEC 21 block published before the outcome |
| `signals/service.py` | The two entry points, deliberately separate |
| `db/repositories/signals.py` | Storage with no update path for the snapshot |
| `migrations/0008_signals.sql` | The schema, with SPEC 20 enforced in SQL |
| `tests/test_signals.py` | 47 tests, immutability first |

## 3. Dependencies

**None added.** Phase 7 uses `hashlib` and `decimal` from the standard library.

## 4. Windows setup

Unchanged from PHASE 1. Nothing new to install.

## 5. `.env.example`

Unchanged. No new configuration keys — the constants that shape locking
(`MIN_LOCK_CONFIDENCE`, `TARGET_ATR_MULTIPLE`, `MAX_EVIDENCE_AGE_MULTIPLE`, the
cost models) are code, not configuration, because changing them changes what
past records mean and that should be a reviewable commit.

## 6. How to run

```bash
python -m aruna migrate            # applies 0008
python -m aruna fetch              # evidence must be current - see §12
python -m aruna signal             # lock across 15m, 1h and 1d
python -m aruna signal --resolve   # score what is due, then lock
python -m aruna signal --resolve-only
```

Flags: `--market`, `--symbols`, `--horizons`, `--limit`, `--dry-run`,
`--verbose` (shows WAIT records and every horizon).

Telegram: `/signals` lists open predictions, `/today` shows the last 24 hours
with outcomes so far.

## 7. How to test

```bash
python -m pytest
python -m pytest tests/test_signals.py -v
python -m pytest tests/test_db_integration.py -k SignalLock
python -m ruff check src tests
```

The integration tests skip themselves when MySQL is unreachable.

## 8. Test results

**652 passed** (up from 583), in 4m43s. `ruff check src tests` clean.
`aruna health` exits 0 with every component UP and Telegram DISABLED.

Six bugs were found by writing these tests, or by reading the output of a live
run. Each is listed here because the fix is more informative than the feature:

**The fingerprint did not survive storage.** `Decimal("1000")` returns from
`DECIMAL(30,12)` as `1000.000000000000`, so the recomputed hash never matched
the stored one. `verify_integrity` would have rejected *every* untouched
prediction and marked it INVALIDATED — the one mechanism protecting past
predictions would have been destroying them. Numbers are now canonicalised to
the scale of the column they live in. Caught by the live round-trip test.

**A WAIT was scored as a wrong direction.** The result block printed
`DIRECTION: WRONG` for every decision to stand aside, and the daily count
included them. There was no position to be wrong about. It now prints `N/A`, and
`/today` counts only calls that took a position.

**A WAIT's market excursion was zeroed.** SPEC 28 has to judge whether standing
aside cost anything; the code threw away the only evidence that could answer it.
The excursion range is now recorded, labelled as the market's move rather than
a position's.

**`as_of` was a bar's open time, not its close.** A daily bar's content is only
known when it closes, so every snapshot reported its evidence as a full interval
older than it was. Harmless until PHASE 7 built a freshness gate on it, which
then refused current daily analysis as 32 hours stale. Corrected in
`analysis/series.py`; `data_through` is now distinct from `last_time`.

**The council reported 100% confidence.** Five agreeing agents produced
`CONFIDENCE: 100%` on a live run. Individual agents have been capped at 0.95
since PHASE 5 for a reason that applies at least as strongly to the council:
SPEC 29 will score stated confidence against realised accuracy, and a published
100% can only ever be measured as overconfident. `MAX_COUNCIL_CONFIDENCE = 0.95`
now matches the agent ceiling.

**Withheld signals were all labelled "stale".** The counter conflated the
confidence floor with the evidence-age gate and printed the wrong reason. The
actual reason is now carried per signal and printed verbatim.

Two more, found while running the CLI: a `SystemExit` carrying a message string
crashed `main()` with a `ValueError` traceback instead of printing the one-line
complaint (a mistyped `--intervals` produced a stack trace); and a stray NUL byte
in `models.py` made the module unimportable.

## 9. Data sources

Unchanged from PHASE 2 and 4 — Indodax for crypto, Yahoo (`.JK`, ~15 min
delayed) for IDX, RSS for news. PHASE 7 adds no source. It consumes stored
candles only: outcomes are computed from what the market did *during* the
horizon, never from a live quote taken afterwards.

## 10. Features implemented

**SPEC 20 — the prediction lock.** Immutable, enforced three ways that overlap
on purpose:

1. `LockedSignal` is a frozen dataclass — Python refuses to mutate it;
2. `signal_snapshots` carries `BEFORE UPDATE` and `BEFORE DELETE` triggers that
   raise `SQLSTATE 45000` — SQL refuses too, and the error names SPEC 20;
3. a SHA-256 fingerprint over exactly the forbidden fields makes any edit that
   got past both detectable afterwards.

Verified against live MySQL, not only in unit tests: `UPDATE` and `DELETE` both
raise, and `verify_integrity` passes on a real round trip.

The lifecycle lives in a separate `signals` table so a prediction can be marked
RESOLVED without the frozen record being touched. A revised view calls
`supersede()`, which returns a *new* signal and leaves the original's
fingerprint identical.

**SPEC 21 — publication before outcome.** `format_signal` prints direction,
confidence, entry, target, horizon, regime, risk, news state, reasoning, the
signal id and the fingerprint. `lock_signals()` and `resolve_due()` are separate
methods so the ordering is inspectable rather than asserted.

**SPEC 10 — multi-horizon, unreconciled.** Horizons are never averaged into one
verdict. A live run produced `1h WAIT 0%` and `1d SELL 95%` for BTC/IDR, scoped
as `SELL 1d ONLY` — the spec's own worked example in shape.

**SPEC 22, 23 — outcomes.** Scored against the original prediction, with samples
taken *during* the horizon so `WRONG_FROM_START` can be told apart from
`RIGHT_THEN_REVERSED`. Both close at the same price and demand opposite lessons.
`TARGET_REACHED` counts a touch at any point, so a target hit and given back is
not misfiled as a directional failure.

**SPEC 34, 46 — paper trading.** Net PnL after entry fee, exit fee, the spread
actually crossed, and slippage. Entry fills at the ask when buying and the bid
when selling — the side that costs you. `paper_trades` carries
`CHECK (is_paper = TRUE)`; an attempt to insert a real-trade row is rejected by
the database, verified live.

**Locking can decline.** Below `MIN_LOCK_CONFIDENCE` (0.35), or with evidence
older than the horizon, a directional verdict is recorded but not published, and
the caveat is written into the frozen reasoning so it cannot be separated from
the record. On a live run this correctly withheld two SELL calls built on
32-hour-old daily bars.

**SPEC 24 at the lock.** `build_signal` raises `LeakageError` when evidence
postdates the lock, with a sentence rather than a constraint name. The database
`CHECK (as_of <= locked_at)` catches it as well.

## 10a. Corrections after delivery

An audit pass found five more defects, all of the same family: code that was
written, exported and unit-tested, but never reached by the live path. A unit
test on an unwired function is worse than no test — it makes the feature look
present.

**SPEC 23's sampling scheme was not applied.** `sample_offsets` and
`SAMPLE_FRACTIONS` existed and were tested; nothing called them. Samples are now
taken at T+10/25/50/75/100% of the horizon.

**Predictions were sampled at their own interval.** A 1d signal read 1d candles
and got exactly one observation — the close. Every SPEC 23 class that depends on
the *path* was unreachable for the daily signals ARUNA actually locks. The
sampler now steps down to a finer stored interval, and reports when it could
not. Measured on real stored candles: a 1d window that yielded one sample now
yields 25, and a real BTC/IDR path (+1.10% → +0.92% → −0.03%) classifies as
`RIGHT_THEN_REVERSED` where the single closing sample had filed it
`WRONG_FROM_START` — the opposite lesson.

**`FLAT_THRESHOLD_PCT` was declared and never used.** A +0.001% drift counted as
a correct BUY. A move inside the noise band is now favourable to nobody.

**`is_resolvable` was never called.** The IDX wall-clock caveat it exists to
raise reached no output. It now gates resolution and its caveat is printed once
per run.

**`r_multiple` was not an R-multiple.** R is net PnL over the risk taken — the
distance to a stop. ARUNA has no stop loss, and the figure was computed against
the distance to the target. Renamed to `target_multiple` (migration 0009); the
values are unchanged, only their name was wrong.

Also fixed: one signal that could not be scored aborted the whole resolution
batch, and a redundant condition in the outcome classifier.

## 11. Dummy features

One, and it is in the schema rather than the code: **`HORIZON_MISMATCH` is
declared in `OutcomeClass` and permitted by the `paper_results` CHECK
constraint, but nothing ever assigns it.** Deciding that a prediction was right
on a different timescale requires looking at what happened *after* the horizon,
which SPEC 24 forbids while scoring. That look-back is PHASE 8's counterfactual
work, and the class is assigned there. Until then it is an unreachable value,
named here rather than left for a reader to discover.

Otherwise every figure produced comes from measured data or from a constant
declared in code, and the constants are named in §12.

Carried forward from PHASE 6, unchanged and still declared on every stored
decision: `historical_reliability` and `confidence_calibration` are two SPEC 16
factors that need realised outcomes. Outcomes now exist, but no phase has
consumed them yet, so both are still applied as a neutral 1.0 and reported as
`unavailable` — not silently assigned a value.

## 12. Limitations

**No accuracy figure here is calibrated.** Outcomes are recorded and classified;
confidence has not been compared against realised accuracy. Until PHASE 8, a
95% is an internal score, not a 95% chance.

**The paper-trade path has not yet run on live data.** It is covered by tests,
and resolution ran end-to-end against MySQL, but at the time of writing no
directional signal has reached its horizon — the two open predictions resolve
tomorrow. Nothing about the trade path is claimed as observed.

**Trading costs are assumptions.** 0.30%/0.30% for Indodax, 0.20%/0.30% for IDX,
plus 10–15 bps slippage. Published retail schedules, not observed fills. Check
them against your own account before trusting a PnL figure.

**The target is a modelled 1.5 × ATR.** Not a calibrated figure; PHASE 9's
backtesting is what would validate it. When ATR is unavailable the signal locks
with no target and says so, rather than inventing a round number that would
later be scored as though it had been a forecast.

**`aruna signal` does not fetch.** It reads stored candles, so stale data
produces withheld signals rather than bad ones — safe, but it means `fetch` must
run first or the freshness gate silences the run. The gate is what makes this
visible; without it the system would have published six-hour-old analysis as a
one-hour forecast, which it did before the gate existed.

**Resolution needs stored candles inside the horizon window.** A signal whose
window contains no ingested bars is counted as `no_prices` and left LOCKED
rather than being scored from whatever price is available now.

**IDX horizons are measured on the wall clock.** `is_resolvable` flags that an
overnight close fell inside the window, but the elapsed time is not converted to
trading time. A 1d IDX prediction spanning a weekend has seen less market than
its horizon claims.

**Position sizing does not exist.** Every paper trade uses a fixed IDR 1,000,000
notional so PnL is comparable across signals. Risk-based sizing is not built.

**The multi-horizon scope reports the council's view, not the published set.**
A horizon can appear in `SCOPE:` and still be withheld; the CLI prints the
withheld line immediately below, with the reason.

---

## What PHASE 8 needs from this

Loss autopsy, counterfactual analysis, ghost signals and calibration all read
from `paper_results`, `outcome_snapshots` and `paper_trades`. Three things were
built specifically so those tables can answer:

- WAIT signals are recorded with the market's excursion range, so SPEC 28 can
  ask what standing aside cost;
- outcomes are classified by *how* they went wrong, not just whether;
- every prediction carries the fingerprint of what was actually claimed, so a
  calibration curve cannot be drawn against a revised forecast.
