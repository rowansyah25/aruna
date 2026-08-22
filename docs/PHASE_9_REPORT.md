# PHASE 9 — Backtest, walk-forward, out-of-sample, decision replay

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

**Version 0.9.0 · 758 tests passing · ruff clean · migrations 0001–0013 applied**

*(§8a records four defects found in an audit pass after first delivery.)*

---

## The finding that matters

**Measured over its own recorded history, ARUNA loses money.**

Backtesting the 1h horizon across all five crypto assets, over the ~3 weeks of
bars ARUNA has stored:

```
decisions simulated: 940
published:           218
direction accuracy:  41%
net PnL:             -1,514,560 IDR
cost ratio:          132.7  (costs against absolute gross)
```

Two things about those numbers.

**41% is not a near miss.** Of 218 published predictions, about 89 were right;
a coin flip would give 109, with a standard deviation of 7.4. That is 2.7
standard deviations below chance. The predictions overlap in time — adjacent
hourly decisions on the same asset are correlated — so the effective sample is
smaller than 218 and the true significance is weaker than that z-score suggests.
It is still not a rounding error.

**A cost ratio of 132 is the more damning number.** It means the gross edge is
approximately zero and trading costs consume everything several times over. A
strategy can survive being slightly wrong about direction; it cannot survive
having no edge to pay the spread with.

Walk-forward says the behaviour is at least *consistent*: 52%, 38%, 38%, 31%
across four folds — within 21 points, and trending down.

Every caveat in the harness pushes results in the flattering direction (no
spread charged, no market impact, full fills). The real figures would be worse.

**This is the first honest measurement of whether ARUNA's rules work. The answer
so far is no.** Nothing in the previous eight phases established otherwise; they
established that the system records what it does, which is what made this
measurable at all.

---

## 1. Project structure

```
src/aruna/
  backtest/
    window.py       point-in-time slicing and the leakage guard   (188)
    engine.py       the replay loop over the live decision path   (247)
    walkforward.py  folds, holdout, and what they mean here       (195)
    replay.py       SPEC 39 reproduction checks                   (165)
    service.py      database-backed runs and replays              (243)
  db/repositories/
    backtest.py     append-only runs and replay checks            (122)
migrations/
  0012_backtest.sql 2 tables, 1 append-only trigger                (77)
tests/
  test_backtest.py  35 tests                                     (356)
```

Changed: `learning/history.py` (rebuild factors from stored snapshots),
`learning/service.py` (`history_as_of`), `db/repositories/learning.py`,
`app.py`, `cli.py`, `core/config.py`, `__init__.py`.

## 2. Files created

| File | What it holds |
|---|---|
| `backtest/window.py` | The only thing that hands out bars, clipped to an instant |
| `backtest/engine.py` | The loop; contains no analysis of its own |
| `backtest/walkforward.py` | Folds, the reserved tail, and honest framing |
| `backtest/replay.py` | Field-by-field divergence reporting |
| `backtest/service.py` | Runs against stored bars; replay with period-correct weights |
| `db/repositories/backtest.py` | Append-only storage |
| `migrations/0012_backtest.sql` | `backtest_runs`, `replay_checks` |
| `tests/test_backtest.py` | 35 tests, leakage first |

## 3. Dependencies

**None added.**

## 4. Windows setup

Unchanged from PHASE 1.

## 5. `.env.example`

Unchanged. The holdout fraction and fold minimums are code: an operator who
could shrink the holdout from a `.env` file could spend the only untouched data
the system has.

## 6. How to run

```bash
python -m aruna migrate
python -m aruna backtest --market CRYPTO --interval 1h
python -m aruna backtest --interval 1h --every 4 --verbose
python -m aruna replay --limit 20
```

`--include-holdout` evaluates the reserved tail. The help text says not to use
it while choosing between model variants, and PHASE 10 is when that matters.

## 7. How to test

```bash
python -m pytest tests/test_backtest.py -v
python -m pytest
python -m ruff check src tests
```

## 8. Test results

**751 passed** (up from 716), 6m16s. ruff clean.

**One defect, and it is the instructive one.** The first working version of the
engine published **zero signals across 94 decisions**. It did not crash and it
did not warn — it reported a cautious strategy.

The cause: the reconstructed market state carried `data_quality="RECONSTRUCTED"`
to be honest about replaying history. But `data_quality` in ARUNA means the
SPEC 5 gate on a *live feed* — STALE, MISSING, ABNORMAL_SPREAD — and anything
other than `OK` sets risk to EXTREME and trips the no-trade engine. Every single
step was blocked.

A settled historical bar is not a misbehaving feed; it is the settled truth, and
it passed that gate when it was ingested. What is actually missing is the order
book, and that was *already* represented honestly by leaving bid, ask and spread
as `None` — which the cost model reads as "no quote observed, charge no spread".
The flag was redundant as well as wrong.

There is now a test that pins the difference between "found no opportunities"
and "structurally cannot produce a signal", because those look identical in a
report and mean opposite things.

Also fixed while building: the synthetic test fixture was a ruler-straight line,
which has no swing highs or lows, so the council correctly said WAIT at every
step. A test built on it would have proven only that the engine can decline.

## 8a. Corrections after delivery

**The SPEC 38 holdout guard was never called.** `check_within_evaluation` was
written, exported and unit-tested, and nothing invoked it. The reserved
out-of-sample data was protected only by the caller's date arithmetic happening
to be correct — a boundary error would have spent it silently, and the run would
have looked normal. The engine now takes a `guard` callable and invokes it on
every decision instant, outside the try block so a violation stops the run
rather than being filed as one more failed step.

**`holdout_included` was read but never written.** The repository stored
`payload.get("holdout_included")` and `BacktestRun.to_dict()` never set the key,
so every recorded run claimed it had stayed out of the reserved data — including
any run that had not. That is exactly the audit trail PHASE 10 depends on to
verify a variant was chosen honestly.

**Two append-only triggers silently lost their message.** MySQL caps
`SIGNAL ... SET MESSAGE_TEXT` at 128 characters; past that it raises `1648 Data
too long for condition item` *instead of* the message. `calibration_snapshots`
and `backtest_runs` were both over. The write was still refused — the guarantee
held — but the explanation was replaced by an error that reads like a bug in
ARUNA rather than a deliberate rule. Migration 0013 rewrites both under the
limit, and a test now walks `information_schema.triggers` so a fresh database
cannot reintroduce it.

The migrations that created those triggers were left untouched: they are already
applied, and the checksum guard exists to stop exactly the edit-in-place that
would have been convenient here.

**Stale phase references in user-facing output.** `aruna council` still told the
operator that the prediction lock "arrives in PHASE 7" and that calibration was
"still unavailable (PHASE 8)"; `aruna signal` and `/today` said calibration
arrives in PHASE 8; `aruna config` said news and fundamental providers arrive in
PHASE 4. All four features exist. Text that tells an operator a built feature is
missing is the same class of dishonesty as claiming an unbuilt one works.

Also removed: seven repository query methods with no callers and no tests
(`minority_verdicts`, `veto_history`, `objection_grounds`,
`decision_distribution`, two `outcome_distribution`s, `latest_run`,
`replay_history`). Uncalled, untested SQL rots invisibly. Agent reliability was
the one genuinely missing surface — it was measured, stored, and shown nowhere,
so `/performance` now reports it, including the "tracked but not yet measurable"
state.

## 9. Data sources

None new. PHASE 9 reads stored candles and stored decisions. What history does
*not* contain is the subject of §12.

## 10. Features implemented

**SPEC 36 — leakage prevention, structurally.** `Window` owns the bars and hands
out only views clipped to an instant; no caller ever receives the full series.
`assert_no_leakage` raises rather than warns, and is called on every context the
engine builds. A leaked backtest produces a number people act on, and it looks
entirely plausible.

**SPEC 35 — backtest.** The engine contains no analysis, no agents and no
scoring: it is a loop feeding `Window` slices to the same `AnalysisEngine`,
`Council`, `build_signal`, `should_lock`, `open_trade` and `resolve` the live
system uses. A backtest that reimplemented the decision path would measure a
different system than the one that trades.

**SPEC 37 — walk-forward**, with the framing stated up front: ARUNA fits no
parameters, so this measures *consistency across market periods*, not resistance
to curve-fitting. It becomes an overfitting guard in PHASE 10, when variants
start being chosen on backtest numbers.

**SPEC 38 — out-of-sample.** The most recent 25% is reserved, not a random
slice: market regimes are serially correlated, so a random holdout leaks through
its neighbours. `check_within_evaluation` raises on any attempt to reach it. It
is built now because a holdout created after tuning begins is worthless.

**SPEC 39 — decision replay.** Rebuilds a stored decision from its own recorded
inputs and compares field by field. Crucially it reconstructs the SPEC 16
factors *as they stood then*, via `history_as_of` — which is what PHASE 8's
append-only `agent_reliability` and `calibration_snapshots` were for. Replaying
today's weights against yesterday's decision would diverge for reasons that have
nothing to do with determinism.

Live result: **6 of 6 stored decisions reproduced exactly.**

## 11. Dummy features

None.

## 12. Limitations

**The result above is one horizon, one market, three weeks.** 1h crypto only.
IDX is not backtested here because only ~120 daily bars are stored and the
1h IDX history is shorter still. Other horizons are untested.

**Backtested costs are understated.** No historical order book exists, so no
spread is charged on any fill. Real fills cross the spread on entry and exit.

**No point-in-time news or fundamentals.** Both are stored with a fetch time
rather than a history, so a replayed decision sees neither. The live council
does see them, which means backtested and live decisions are not strictly the
same decision — this is the largest gap between the two paths and it is not
closable without storing news with point-in-time validity.

**No market impact, no partial fills, no slippage beyond the modelled bps.**

**Survivorship.** Only assets currently in the universe are replayed. The five
USDT pairs retired in PHASE 2 are disabled rather than deleted, but they are not
included, so the backtest cannot see a decision made about an asset that later
left.

**Walk-forward is not yet an overfitting guard**, for the reason in §10.

**Replay covers 6 decisions.** All the ones that carry a council session id;
everything locked before that link was repaired cannot be replayed and is
reported as `NOT_REPLAYABLE` rather than counted as a pass.

**The engine decides at every bar close.** The live system runs when invoked, so
the backtest evaluates the rules at a higher frequency than the live system
currently trades at. `--every N` thins it, but the two cadences are not matched.

---

## What PHASE 10 needs from this

Shadow models, drift detection and human-approved model changes all rest on
being able to compare a variant against the current rules honestly. Three things
here exist for that:

- `backtest_runs` is append-only, so every number a human saw while choosing is
  still on the record;
- the holdout is reserved and enforced *now*, before any tuning has happened;
- `known_optimism` is stored per run, so a two-year-old result keeps the caveats
  that applied when it was produced rather than inheriting today's.

And the finding in this report is the first thing PHASE 10 should be pointed at:
there is a measured, negative result to improve on, which is a better starting
position than an untested system that looks fine.
