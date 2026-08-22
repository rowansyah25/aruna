# PHASE 6 delivery report

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

Per SPEC 49. Written after the build, from actual runs against stored evidence.

- **Scope delivered:** council, cross-protest, rebuttal, veto, judge
  (SPEC 45, PHASE 6)
- **Still PAPER only. Still no signal.** A council verdict is not a locked
  prediction — SPEC 20 arrives in PHASE 7.

---

## 1–2. Structure and files

```
src/aruna/council/
├─ protest.py   SPEC 14 rounds 2-4: cross-protest, rebuttal, adversarial review
├─ veto.py      SPEC 18 veto grounds + SPEC 19 review
├─ judge.py     SPEC 16 evidence-weighted verdict
├─ session.py   the full sequence
└─ service.py   context assembly and storage
src/aruna/db/repositories/council.py
migrations/0007_council.sql   (6 tables)
tests/test_council.py         (32 tests)
```

## 3. Dependencies

**None added.**

## 4–7. Setup, config, running, testing

```powershell
.\.venv\Scripts\python.exe -m aruna council --market CRYPTO --interval 1h --verbose
```

## 8. Test results

```
583 passed in 217.89s
ruff check src tests — All checks passed
```

Up from 543 at the end of PHASE 5.

A post-delivery audit pass found one genuine gap and closed it: `/council` was
registered but never bound, so a command whose phase had arrived was still
answering "NOT IMPLEMENTED". Two tests now lock the invariant in both
directions — nothing may stay unbound past its phase, and nothing may claim to
work before it. That check is what would have caught this immediately.

Database integrity verified: 5 council sessions, 24 objections, 8 rebuttals,
5 judge decisions, and **0 vetoes without a review** — SPEC 19's requirement
holds in the stored data, not only in the code.

### Verified against stored evidence

```
BNB/IDR 1h  WAIT  conf=0.00  agents=6/9  rounds=3  obj=3  blocked[NO_EDGE]
BTC/IDR 1h  WAIT  conf=0.00  agents=6/9  rounds=4  obj=3  blocked[NO_EDGE]
ETH/IDR 1h  BUY   conf=0.58  agents=5/9  rounds=3  obj=1
SOL/IDR 1h  WAIT  conf=0.00  agents=5/9  rounds=3  obj=0  blocked[NO_EDGE]
XRP/IDR 1h  WAIT  conf=0.00  agents=6/9  rounds=3  obj=1  blocked[NO_EDGE]
```

BTC ran four rounds — disagreement crossed the threshold and triggered the
SPEC 14 adversarial review. **The council is markedly more conservative than
PHASE 5 on identical evidence** (4 blocked vs 1), which is the intended effect:
cross-examination discounts weight, and weaker cases stop clearing the edge
floor.

### Two defects found and fixed

Both were caught by reading the live output and disbelieving it.

1. **The judge reported 100% confidence on a total weight of 0.288.** Confidence
   was the weighted *share*, which is 1.0 whenever only one side has any weight
   at all — regardless of how little that side actually carried. Now
   `margin × conviction`, where conviction saturates against an absolute weight
   floor. Same class of bug as PHASE 5's `_decide`, in a new place.

2. **One defect produced fifteen corrections.** Every accuser filed the same
   `shared_evidence` objection against the same target, so MOMENTUM conceded
   "not independent of reading:rsi" four times and was charged four separate
   correction penalties for a single fact. Grounds that are properties of the
   *target* are now raised once; only a direction conflict stays per-pair.
   Objections dropped from 15 to 0–3 per session.

## 9. Data sources

Unchanged. PHASE 6 adds no external calls.

## 10. Features implemented

**All four SPEC 14 rounds.** Round 1 independent opinions; round 2 **mandatory**
cross-protest; round 3 rebuttal; round 4 adversarial review, run only when
weighted disagreement crosses the threshold. All four stances are used —
SUPPORT, OBJECT, COUNTER_PROPOSE, ACCEPT_CORRECTION.

**Objections are checkable defects, not moods.** Shared evidence, thin samples,
confidence the evidence cannot support, a stale feed, or a substantive direction
conflict. Each is verifiable against the record, which is what makes a
deterministic council meaningful rather than theatrical.

**Rebuttals can lose.** An agent concedes when the ground actually holds and
defends when it does not — and conceding costs it weight at the judge. An
objection nobody could lose to would be decoration.

**Agreement on shared evidence is not corroboration.** Two agents agreeing from
the same RSI is one observation counted twice (SPEC 17), so it is not recorded
as support.

**Veto is narrow and reviewable (SPEC 18, 19).** Grounds differ by market —
`TRADING_HALT` is an IDX ground, `EXTREME_SPREAD` a crypto one. **PROTEST is not
VETO**: however strongly an agent disagrees about direction, that can never
raise one. Every veto is reviewed, and one whose stated condition is not present
is **rejected**, so a faulty probe cannot freeze the system. Rejected vetoes are
stored, not discarded.

**The judge weighs, never counts (SPEC 16).** Evidence quality, freshness,
sample size, independence, feature redundancy, accepted corrections, horizon
mismatch, regime confidence and risk. A test proves a minority wins: three
agents sharing one under-sampled reading lose to one agent with strong
independent evidence, and `minority_prevailed` is recorded for audit.

**Two SPEC 16 factors are declared unavailable rather than invented.**
Historical reliability (SPEC 30) and confidence calibration (SPEC 29) both need
realised outcomes, which arrive in PHASE 7–8. They are applied as a neutral 1.0
and named in `unavailable_factors` on every stored decision. A judge that
quietly assigned values to them would look better-grounded than it is — and
would corrupt the calibration those phases exist to establish.

**Ordering is deliberate.** The judge runs on opinions that have already
survived cross-examination; an upheld veto overrides the judge; the no-trade
engine runs last, so a well-argued verdict on untrustworthy data is still
refused.

## 11. Dummy / not implemented

**A verdict is not a signal.** No entry, no target, no expected move, no
immutable snapshot. `is_locked_signal=False` is stored on every session and
asserted by a test. SPEC 20's prediction lock is PHASE 7.

**Registered but not built** — 10 Telegram commands, all for PHASE 7, 8 or 10.

`/council` **is** wired and serves real verdicts. It deliberately does not use
the SPEC 21 signal layout: that block has an entry, a target and a predicted
move, and borrowing it would imply a locked prediction that does not exist. A
test asserts those fields never appear in the report body.

**Absent** — prediction lock, paper trading, outcome sampling (PHASE 7); loss
autopsy, counterfactual, ghost signal, calibration (PHASE 8); backtest,
walk-forward, replay (PHASE 9); shadow model, drift (PHASE 10).

## 12. Limitations

**Every threshold remains unvalidated.** `HIGH_DISAGREEMENT = 0.4`,
`DECISION_MARGIN = 0.15`, `FULL_CONVICTION_WEIGHT = 1.5`,
`CORRECTION_PENALTY = 0.5`, and the nine weighting factors. All are reasoned,
none are calibrated. **Treat every confidence as an internal score, not a
probability.**

**The judge's weighting formula is a design choice.** Multiplying nine factors
is defensible reasoning about weight of evidence; it is not derived from data,
and the relative importance of each factor is a guess until outcomes exist.

**Protest is deterministic and therefore narrow.** Five grounds. A human analyst
would raise objections these rules cannot express. The trade-off buys
reproducibility, which SPEC 29 and 39 require.

**The council is conservative on thin universes.** With nine agents of which
three routinely abstain, and weights discounted by shared evidence, most
sessions fail the edge floor. That is arguably correct behaviour, but whether
the floor sits in the right place is unknown until PHASE 9 backtesting.

**Veto grounds cannot all be verified.** `CRITICAL_SECURITY_EVENT` and
`CRITICAL_CORPORATE_ACTION_UNCERTAINTY` are permitted by SPEC 18 but no feed
supports them, so nothing raises them and the review would reject them if
anything did. The gap is in the code, stated, not silently filled.

**No agent performance history (SPEC 30).** Every opinion, objection and
rebuttal is stored so the analysis becomes possible, but nothing scores them —
there are no outcomes yet.

**Not verified during IDX trading hours**, as with PHASE 2–5.

## Next phase

PHASE 7: prediction lock, paper trading, multi-horizon, outcome engine. This is
the phase where ARUNA first produces something it can be judged by — and where
SPEC 20's immutability rule starts to matter. SPEC 45's precondition is met:
PHASE 6 is runnable and its full suite passes.
