# FUTURES F5 — The signal

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

**1081 tests passing (+98 since F4) · ruff clean · migration 0015 applied**

Covers FUTURES SPEC 8–14 (the futures council), 37–39 (signal format), 47
(immutability), 48 (the daily account), 50–52 (ARUNA does not execute).

---

## What F5 delivers

| File | Covers |
|---|---|
| `futures/plan.py` | The four verdicts, the SPEC 51 guard, the rendered signal |
| `futures/service.py` | Council verdict → plan → stored, and the currency rebase |
| `db/repositories/futures.py` | Append-only storage, fingerprint verification |
| `migrations/0015_futures_plans.sql` | The schema, and the constraints that make a half plan unrepresentable |
| `cli.py` → `aruna plan` | End-to-end from the terminal |
| Telegram `/plans` | Recent plans and refusals, read-only |

## Four verdicts, and only one of them carries numbers

```
NO_SIGNAL   the inputs do not describe one moment (SPEC 46)
WAIT        the council took no side, so there is no position to size
REFUSED     a directional idea the arithmetic rejects
PLAN        everything cleared
```

**A refused plan carries no entry, size, leverage or liquidation price.** Half a
plan reads exactly like a plan to anyone skimming, and skimming is how these get
read. The application refuses to build one; the database refuses to store one.

The order of the gates is the design. Data coherence is checked before anything
is computed from the data; the direction is consulted before a position is
sized; the stop is placed before the size that depends on it; leverage is chosen
last, from a position that already exists. Nothing downstream can reach back and
adjust something upstream to make a number look better.

## SPEC 51 is enforced, not promised

`render()` scans its own finished output and raises `ForbiddenClaim` rather than
emitting "pasti profit", "leverage aman", "100% win" or any of the other
nineteen phrases. `to_dict()` does the same.

The check runs on the **output**, not on the templates, because this module
writes none of the caveat text — it arrives from six other modules and from the
venue. Guarding the templates would guard the one source that was never the
risk. Both doors are guarded: a bot, a CLI and an API all reach the reader
through `to_dict()`, and one path enforcing the rule while another does not is
worse than neither, because it makes the rule look enforced.

## The database refuses a half plan

Migration 0015, verified against live MySQL rather than inferred from the SQL:

```
complete PLAN                      ACCEPTED
REFUSED carrying entry+leverage    REFUSED  futures_plans_refusal_is_empty
refusal with no reason given       REFUSED  futures_plans_refusal_has_reasons
UPDATE an issued plan              REFUSED  1644, trigger
DELETE an issued plan              REFUSED  1644, trigger
```

Two CHECK constraints, in both directions: a non-PLAN carries no numbers, and a
PLAN carries all of them. A third requires a refusal to give a reason — a
verdict with an empty reason list is a shrug with a label on it.

`futures_plan_results` adds one more: an outcome of `LIQUIDATED` that does not
record having touched the liquidation level is a contradiction, and this is the
one outcome no arithmetic may quietly smooth over.

**The trigger returns 1644 with its message intact**, not 1648 — both are under
MySQL's 128-character `MESSAGE_TEXT` cap. Phase 7 learned that the hard way.

## Two defects found by running it, not by reading it

**The units did not match, and the error wore a costume.** The council's evidence
is priced in IDR; the perpetual is priced in USDT. The first live run refused
with *"the risk budget supports 0E-11 of notional, below the venue minimum"* —
which reads like a risk decision and was not one:

```
spot last_price : 1,121,301,000   IDR
spot atr        :     5,495,793   IDR
mark price      :        63,000   USDT
```

The stop sat 5.5 million "dollars" from a 63,000 entry, the risk budget divided
by that distance floored the quantity to zero, and every plan was refused for a
reason that had nothing to do with risk.

What transfers between quote currencies is a **proportion**, not an absolute.
An ATR of 5,495,793 IDR is 0.49% of price; 0.49% of a 63,000 mark is 308 USDT.
Structure levels are rebased for the same reason — a support level quoted in IDR
is not a price the perpetual can reach. When the ratio cannot be computed, the
ATR is reported unavailable rather than passed through raw.

After the fix the refusal is honest: *"net reward is -0.02x the risk after fees,
funding and slippage"* — the nearest resistance was too close to cover the costs.

**The fingerprint would not have survived the round trip.** MySQL warned "Data
truncated for column 'liquidation_price'": the price comes out of a division
with 23 decimal places and the column holds 12. The hash was computed on the
in-memory value and the database kept a rounded one. Phase 7 hit exactly this
and it would have invalidated every prediction. Values are now quantized to the
column's scale with the same rounding the fingerprint uses, before insert.

## The refusals are stored

Not bookkeeping. SPEC 48 wants a daily account of what ARUNA did, and a day of
two plans and forty refusals is a day it mostly said no. A store that kept only
the plans would describe a different system from the one that ran, and would
make the refusals invisible to the learning layer whose job is to judge whether
saying no was right.

Both surfaces lead with the tally, and both say the same thing when there is
nothing to show: *"No plan was issued. That is an output, not a failure to
produce one."*

## One more defect, in the council seam

`CouncilService.use_history()` replaces its inner `Council` when the measured
SPEC 16 factors arrive. A caller that grabbed the instance once at construction
would deliberate with neutral weights forever — and silently, because every
verdict would still look normal. It is now reached through a property.

## What F5 does not do

- **No scheduler.** `aruna plan` runs when a person runs it. Nothing plans on a
  timer yet, so the daily tally only covers what was asked for.
- **`/plans` reads; it does not plan.** A Telegram command that quietly issued
  new plans would make the record depend on who happened to send a message.
- **No paper trading.** A plan is analysis; nothing simulates taking it.
- **Margin brackets are still unavailable**, so every liquidation price rests on
  the base maintenance rate and is optimistic by an amount that grows with size.
- **Nothing has been measured.** Two refusals are stored and zero plans. Every
  figure F6 produces reports insufficient sample.

## The thing to keep in view

Unchanged, and F5 sharpens it rather than softening it.

ARUNA's spot rules were measured at **50% direction accuracy over 580 daily
predictions** — a coin flip — and the futures council reads the same indicators
through the same judge. What F5 adds is not an edge: it is a long series of
gates, most of which say no. On the first live run against real BTCUSDT data,
every one of them did.

That is the honest summary of this phase. The machinery for turning a direction
into a leveraged position is built, tested and stored; whether the direction is
worth anything is a question only F6 can answer, and it has no data yet.

---

## Next: F6

Scoring the stored plans against what the market actually did (SPEC 40–45), and
the daily report (SPEC 48). The learning module is written; what it needs is
resolved plans, and there are none.
