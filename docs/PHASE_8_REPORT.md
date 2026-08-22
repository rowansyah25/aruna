# PHASE 8 — Loss autopsy, counterfactual, ghost signal, calibration

**Version 0.8.0 · 704 tests passing · ruff clean · migrations 0001–0010 applied**

The phase where ARUNA starts learning from what it got wrong — and where the
most important behaviour is refusing to draw a conclusion from four
observations.

---

## 1. Project structure

```
src/aruna/
  learning/
    calibration.py     SPEC 29 confidence vs realised accuracy      (204)
    reliability.py     SPEC 30 per-agent track record               (165)
    autopsy.py         SPEC 25 loss autopsy, SPEC 26 objections     (263)
    counterfactual.py  SPEC 27 counterfactual, SPEC 28 ghosts       (211)
    history.py         the measured SPEC 16 factors for the judge    (53)
    service.py         one pass over resolved predictions           (222)
  db/repositories/
    learning.py        the joins, and append-only measurements      (302)
migrations/
  0010_learning.sql    5 tables, 2 append-only triggers             (144)
tests/
  test_learning.py     43 tests                                    (418)
```

Changed: `council/judge.py` (the two SPEC 16 factors are now suppliable),
`council/session.py`, `council/service.py`, `signals/service.py`, `app.py`,
`cli.py`, `notify/telegram/bot.py`, `notify/telegram/formatting.py`,
`core/config.py`, `__init__.py`.

## 2. Files created

| File | What it holds |
|---|---|
| `learning/calibration.py` | Buckets, Brier score, and the refusal to report |
| `learning/reliability.py` | Per-agent accuracy on the calls it actually backed |
| `learning/autopsy.py` | Why one prediction failed; which objections were right |
| `learning/counterfactual.py` | The road not taken; the WAITs that cost something |
| `learning/history.py` | `MeasuredHistory` — the judge's `HistoryFactors` source |
| `learning/service.py` | One pass producing every SPEC 25–30 finding |
| `db/repositories/learning.py` | Reads the joined record, writes findings |
| `migrations/0010_learning.sql` | Findings upsertable, measurements append-only |
| `tests/test_learning.py` | 43 tests, the refusals first |

## 3. Dependencies

**None added.** Everything here is arithmetic over rows already stored.

## 4. Windows setup

Unchanged from PHASE 1.

## 5. `.env.example`

Unchanged. The sample thresholds (`MIN_BUCKET_SAMPLE`, `MIN_TOTAL_SAMPLE`,
`MIN_RELIABILITY_SAMPLE`) and the multiplier bounds are code, not configuration
— an operator who could lower them from a `.env` file could make the system
claim calibration it has not earned, which is precisely what they exist to
prevent.

## 6. How to run

```bash
python -m aruna migrate            # applies 0010
python -m aruna autopsy            # review every resolved prediction
python -m aruna autopsy --verbose  # weights, counterfactuals, objections
python -m aruna autopsy --dry-run  # analyse, store nothing
```

Telegram: `/performance`, `/weekly`, `/monthly`, `/autopsy`.

The measured factors are loaded at startup and handed to the council, so
`aruna signal` and `aruna council` pick them up automatically once there is
enough history to apply.

## 7. How to test

```bash
python -m pytest
python -m pytest tests/test_learning.py -v
python -m ruff check src tests
```

## 8. Test results

**704 passed** (up from 660), 5m32s. `ruff check src tests` clean. `aruna
autopsy` runs against the live database and reports `INSUFFICIENT SAMPLE`,
which is the correct answer at this point.

One defect found while building, of the family the PHASE 7 audit was about:
`reclassify_with_lookahead` was written, exported and tested but nothing called
it — `HORIZON_MISMATCH` would have stayed unreachable for a second phase
running. It is now wired into the review pass, with the market-data lookup it
needs.

One PHASE 7 gap had to be repaired before any of this could work:
**`signal_snapshots.council_session_id` was never populated.** The column
existed and documented itself as the link to the debate; `lock_signals` passed
`None` every time. Without it a loss autopsy has the forecast and the outcome
but no access to the argument — the only part that can be learned from. The
council session is now stored before the prediction is locked, and the
prediction names it. Verified live: new signals carry a session id, and rows
locked before the fix correctly carry `NULL` rather than a guess.

One design point worth flagging because it is easy to get wrong: an objection
counts as *answered* only when a rebuttal conceded on **its own ground by its
own accuser**. Matching any conceded rebuttal in the session would have made
SPEC 26 silently undercount vindicated objections — the join checks both.

## 9. Data sources

Unchanged. PHASE 8 reads only what ARUNA has already recorded — no external
source is consulted, and none could be: the questions here are about ARUNA's own
past decisions.

## 10. Features implemented

**SPEC 29 — calibration.** Confidence bucketed into four bands, compared against
realised accuracy, with a Brier score. A bucket reports `accuracy = None` until
it holds 20 predictions, and the overall verdict needs 50. Fifty predictions
spread thinly across four buckets still reports `INSUFFICIENT SAMPLE`, because a
total says nothing about any particular confidence level — there is a test for
exactly that.

**SPEC 30 — agent reliability.** Each agent is scored on the calls *it* backed,
read from the stored judge weights: an agent that argued SELL is not credited
when the council's BUY works out. Abstentions are not scored — declining to read
thin evidence is a legitimate act. Needs 25 scored opinions; the multiplier is
bounded to ×0.7–×1.2 so one bad stretch cannot silence an agent, because a
silenced agent can never accumulate the record that would clear it.

**SPEC 16, closed.** The judge has declared `historical_reliability` and
`confidence_calibration` unavailable since PHASE 6. It now accepts a
`HistoryFactors` provider, applies whichever factor can be measured, and reports
`applied_factors` alongside `unavailable_factors` on every stored decision. The
provider is snapshotted once per run: weights that shifted mid-batch would make
the decisions in that batch incomparable and break SPEC 39's replay guarantee.

**SPEC 25 — loss autopsy.** For each losing prediction: which agents backed it
and with what weight, who dissented and was overruled, which objections went
unanswered, which vetoes were rejected on review, and a stated hypothesis about
the failure mode drawn from the SPEC 23 class. Findings are observations, each
checkable against the record.

**SPEC 26 — successful objections.** Objections raised, overruled, and then
vindicated by the outcome, aggregated by accuser and ground and sorted so the
blind spot that keeps being dismissed surfaces first.

**SPEC 27 — counterfactual.** What the mirror decision would have returned.
Labelled as a gross price move, before the costs a real position pays.

**SPEC 28 — ghost signals.** WAITs the market punished, using the excursion
range PHASE 7 preserved for non-directional outcomes. Never framed as an error:
standing aside on thin data is what the no-trade engine is for, and a system
that treated every missed move as a failure would learn to trade everything.

**`HORIZON_MISMATCH` is now reachable.** Deciding that a call was right but
early needs data from *after* the horizon, which the scoring path is forbidden
to touch (SPEC 24). The look-ahead lives in the learning module, is bounded to
one horizon — given enough time every direction is right eventually — and adds
a *finding* without altering the recorded score. Letting a later move upgrade a
past loss would be marking one's own homework with the answers in hand.

## 11. Dummy features

None. The PHASE 7 report listed `HORIZON_MISMATCH` as an unreachable enum value;
it is assigned in this phase.

What this phase produces instead of dummy figures is an explicit refusal.
Running `aruna autopsy` today prints:

```
INSUFFICIENT SAMPLE: 0 resolved directional prediction(s), 50 needed
before calibration can be assessed. No claim is made about confidence.
  35-50%  n=0  needs 20 more
```

That is the honest output, and it will stay that way for a long time.

## 12. Limitations

**Nothing has been measured yet.** At the time of writing five predictions have
resolved, all WAITs. Every SPEC 29 and SPEC 30 figure reports
`INSUFFICIENT_SAMPLE`, both judge factors are neutral, and no weight has been
adjusted by anything in this phase. The machinery is built and wired; it has no
data. **This is the single most important sentence in this report.**

**A loss autopsy has been run end-to-end against zero losses.** The analysis
functions are covered by 43 tests over constructed records, and the review pass
runs live, but no real losing prediction has passed through it. The first one
that does may well expose something these tests do not.

**The thresholds are floors for saying a number at all, not marks of a good
sample.** At 20 observations, a bucket that is truly 70% accurate still shows
anywhere from roughly 50% to 90% by luck. Twenty is where reporting stops being
indefensible, not where it becomes reliable.

**Reliability infers an agent's correctness from the council's.** An agent that
agreed shares the council's result; a dissenter was right exactly when the
council was wrong. That is sound for a two-way directional call and says nothing
about *degree* — an agent that was directionally right and wildly wrong about
magnitude scores as a win.

**Counterfactuals are gross.** The mirror decision's return does not deduct
fees, spread or slippage, so an alternative that looks better by less than about
0.7% (crypto) would not actually have been.

**Ghost signals only see inside the horizon.** A WAIT whose move arrived shortly
after its window closes is not counted.

**The look-ahead reclassification is bounded to one horizon** and only ever
applies to `WRONG_FROM_START`. A call that worked and then reversed was not
early, and `RIGHT_THEN_REVERSED` already says so.

**IDX still measures horizons on the wall clock.** Carried forward from PHASE 7
and now stated once per resolution run.

---

## What PHASE 9 needs from this

Backtesting, walk-forward and decision replay all need a decision to be
reproducible from its stored inputs. Two things here were built with that in
mind:

- the history snapshot is taken once per run and never changes mid-batch, so
  every decision in a run was judged under identical weights;
- `calibration_snapshots` and `agent_reliability` are append-only, so the
  weights in force at any past moment can be reconstructed — which is what
  makes replaying an old decision meaningful rather than a fresh computation
  wearing an old date.
