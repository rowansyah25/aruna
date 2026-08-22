# PHASE 5 delivery report

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

- **Scope delivered:** AI agents, self-critic, prosecutor, risk, no-trade
  engine (SPEC 45, PHASE 5)
- **Still PAPER only. Still no signals.** Round one of SPEC 14 exists; the
  council does not.

---

## What "AI agent" means here — read this first

These agents are **deterministic rule-based reasoners, not language models.**
That was a deliberate reading of the specification, and it is the most
important thing in this report.

Two SPEC requirements force it:

* **SPEC 29** requires confidence to be measured against realised accuracy.
  Calibration needs the same inputs to produce the same number.
* **SPEC 39** requires a decision to be replayable in chronological order,
  showing only what was known at the time. An LLM's output is not reproducible,
  so a replay could not be verified against the original.

An LLM-backed agent can implement the same `Agent` protocol later without
anything else changing. But calling the current build "AI" without saying this
would be overclaiming, so it is said plainly here and in the code.

## What PHASE 5 deliberately does not do

`Deliberation` carries `is_council_decision=False` and a phase note, and a test
asserts both. SPEC 14 requires four rounds — independent opinions, **mandatory**
cross-protest, rebuttal, and adversarial review on high disagreement — plus veto
and veto review (SPEC 18, 19) and the evidence-weighted judge (SPEC 16). PHASE 5
delivers round one only.

## 1–2. Structure and files

```
src/aruna/agents/
├─ base.py            Agent protocol, AgentOpinion, EvidenceRef, confidence caps
├─ context.py         DecisionContext - the frozen evidence pool
├─ market.py          TECHNICAL, STRUCTURE, MOMENTUM, VOLUME, REVERSAL, REGIME
├─ context_agents.py  NEWS, FUNDAMENTAL
├─ risk.py            SPEC 32 risk engine + RISK agent
├─ notrade.py         SPEC 33 no-trade engine
├─ analyst.py         ARUNA ANALYST, PROSECUTOR, SELF-CRITIC
├─ deliberation.py    round-one orchestration
└─ service.py         context assembly and storage
src/aruna/db/repositories/agents.py
migrations/0006_agents.sql
tests/test_agents.py
```

9 new modules, 1 migration, 55 tests.

## 3. Dependencies

**None added.**

## 4–7. Setup, config, running, testing

Setup and `.env` unchanged.

```powershell
.\.venv\Scripts\python.exe -m aruna deliberate --market CRYPTO --interval 1h --verbose
```

Takes `--market`, `--symbols`, `--interval`, `--dry-run`, `--verbose`.

## 8. Test results

```
543 passed in 167.27s
ruff check src tests — All checks passed
```

Up from 488 at the end of PHASE 4.

### Verified against stored evidence

```
BNB/IDR 1h  WAIT  conf=0.00  agents=6/9  risk=MODERATE  blocked[NO_EDGE]
BTC/IDR 1h  WAIT  conf=0.00  agents=6/9  risk=MODERATE
ETH/IDR 1h  BUY   conf=0.60  agents=5/9  risk=MODERATE
SOL/IDR 1h  SELL  conf=0.40  agents=5/9  risk=MODERATE
XRP/IDR 1h  SELL  conf=0.50  agents=6/9  risk=MODERATE
```

Mixed outcomes, moderate confidences, one blocked by the no-trade engine.

### Two defects found and fixed

Both were caught by *looking at the live output and disbelieving it*, not by a
failing test.

1. **Agents reported 100% confidence on a single weak signal.** `_decide`
   returned `|margin| / total`, which is 1.0 whenever only one side votes —
   regardless of how weak that lone vote was. The visible symptom was
   `STRUCTURE: SELL 100%` printed directly above its own reasoning, "swing
   sequence is mixed". Confidence now multiplies *agreement* (how one-sided the
   vote was) by *conviction* (how much weight was actually behind it).

2. **A single strong vote could look unanimous.** The analyst weighted only the
   two directional camps, so five agents explicitly declining to commit did not
   dilute one confident vote. Confidence is now scaled by the share of
   participating agents that actually took a side. Both fixes together moved
   the live output from spurious 0.87–1.00 confidences to a plausible
   0.40–0.60.

A third change came out of testing: **no single agent may now exceed 0.95
confidence.** A lens that cannot see news, structure and risk at once has no
business reporting certainty (SPEC 6). The RISK agent is exempt when it vetoes,
because "this data is unusable" is a statement of fact, not a market prediction.

## 9. Data sources

Unchanged. PHASE 5 adds no external calls — it reads stored evidence only.

## 10. Features implemented

**The SPEC 12 roster.** TECHNICAL, STRUCTURE, MOMENTUM, VOLUME, REVERSAL,
REGIME, NEWS, FUNDAMENTAL, RISK — each reading a narrow slice so they can
genuinely disagree, which is what SPEC 17 independence requires.

**No permanent stance (SPEC 12, 48).** Every agent is run across five market
shapes in the tests, and any agent that only ever reached BUY, or only ever
SELL, fails. REVERSAL calls a bullish turn out of oversold as readily as a
bearish one out of overbought.

**Abstention is a real answer.** An agent with nothing to judge on returns WAIT
with `abstained=True` rather than inventing a weak view. A fabricated 50/50
adds a vote without adding information.

**Evidence is declared (SPEC 17).** Every opinion lists the keys it consulted.
The pool computes an independence score, and both the analyst and the
prosecutor scale confidence by it — six agents reading one RSI cannot look like
a consensus.

**PROSECUTOR (SPEC 13).** Attacks the proposal — shared evidence, dissenting
agents, votes with no reliable backing, a delayed price feed — and *then*
re-weighs the pool itself to reach an independent conclusion, which may agree.
Tests cover it agreeing, disagreeing, and reaching WAIT.

**SELF-CRITIC (SPEC 15).** Asks what the strongest evidence against the decision
is. When counter-evidence outweighs the proposal it sets
`reassessment_required`, and the engine stands down to WAIT rather than
flipping — flipping on the critic's word alone would just replace one
unexamined verdict with another, and there is no judge yet to arbitrate.

**RISK engine (SPEC 32).** Volatility, spread, liquidity, gap, event,
concentration via correlation, data quality, data freshness, plus per-market
factors. Worst factor wins; risk does not average out.

**NO-TRADE engine (SPEC 33).** Every named reason, including the closing rule:
below a confidence floor there is no demonstrable edge, so WAIT. Untrustworthy
inputs yield `NO_SIGNAL` rather than WAIT — there is nothing to say, which is a
different statement from "no setup".

**Honest gaps recorded as risk.** SPEC 32 names corporate-action risk; ARUNA has
no such feed, so a MODERATE factor is raised saying the risk is *unmeasured, not
absent*.

## 11. Dummy / not implemented

**No council.** Cross-protest, rebuttal, adversarial review, veto, veto review
and the judge are PHASE 6. Round one runs once, each agent in isolation.

**Registered but not built** — 11 Telegram commands, unchanged from PHASE 4.

**COUNCIL_JUDGE** exists in the enum and has no implementation.

**Absent** — prediction lock, paper trading, outcome sampling (PHASE 7);
autopsy, counterfactual, calibration (PHASE 8); backtest, walk-forward, replay
(PHASE 9); shadow models, drift (PHASE 10).

## 12. Limitations

**Every threshold is unvalidated.** `FULL_CONVICTION_MARGIN = 3.0`,
`MIN_EDGE_CONFIDENCE = 0.35`, the risk bands, the agent weights — all are
reasoned defaults, none are calibrated. Nothing has been backtested. **Treat
every confidence in this phase as an internal score, not a probability.** SPEC
29 calibration needs outcomes, which arrive in PHASE 7–8.

**The agents are simple.** Each is a handful of thresholds over PHASE 3–4
readings. They are auditable and reproducible, which is what the specification
needs — but they are not sophisticated, and no claim to the contrary is made.

**Confidence arithmetic is a design choice, not a measurement.** Multiplying
agreement by conviction by independence is defensible reasoning about weight of
evidence. It is not derived from data, and it will need revisiting once
calibration exists.

**Self-critic stands down rather than reassessing properly.** SPEC 15 says ARUNA
must reassess when counter-evidence is stronger. With no judge, the safe reading
is to withdraw the proposal. A genuine reassessment loop belongs in PHASE 6.

**NEWS confidence is capped at 0.5 and FUNDAMENTAL at 0.45**, because the
lexicon and the sector-blind valuation behind them cannot support more.

**No multi-horizon reconciliation.** SPEC 10 expects horizons to disagree
legitimately. Each deliberation covers one interval; `CONFLICTING_HORIZON`
exists in the no-trade engine but nothing yet feeds it.

**No agent performance tracking.** SPEC 30 wants accuracy, calibration, and
successful-versus-false objections per agent. The storage records every opinion
so that becomes possible, but nothing scores them — there are no outcomes yet.

**Not verified during IDX trading hours.** As with PHASE 2–4, this was exercised
on a Saturday.

## Next phase

PHASE 6: council, cross-protest, rebuttal, veto, judge. SPEC 45's precondition
is met — PHASE 5 is runnable and its full suite passes.
