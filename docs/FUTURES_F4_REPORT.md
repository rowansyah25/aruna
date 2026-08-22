# FUTURES F4 — Leverage

**983 tests passing (+70 since F3) · ruff clean**

Covers FUTURES SPEC 15 (one number), 16 (leverage is not a profit dial), 17
(council proposals), 18 (the ladder), 30 (slippage stress), 31 (margin mode),
32 (per horizon), 36 (safety score).

---

## What F4 delivers

| File | Lines | Covers |
|---|---|---|
| `futures/leverage.py` | 410 | The ladder, the single recommendation, margin mode, safety score |
| `futures/horizon.py` | 395 | SPEC 32: the position re-priced at the end of the hold |
| `futures/slippage.py` | 216 | SPEC 30: the trade walked through worsening fills |
| `tests/test_futures_horizon.py` | — | 24 tests |
| `tests/test_futures_leverage.py` | — | 28 tests |

The +70 is not all F4. Roughly half came from fixing defects that live Binance
data exposed in F1 and F3 once the venue became reachable, and from the Telegram
surface. Attributing all of it to this phase would overstate what was built here.

## The sentence F4 exists to make true

**With risk-based sizing, leverage changes neither the profit nor the loss.**

F3 sized the position from the risk budget and the stop distance. So the loss
when the stop fills is the risk budget at *every* leverage, and the profit at
target is the same figure at every leverage. What changes is the margin posted,
and therefore how far away the liquidation price sits.

Leverage is a safety dial with a capital cost. Anyone reaching for more of it to
make more money has misunderstood the arithmetic, and the module makes the
misunderstanding unrepresentable: **there is no profit-target parameter anywhere
in it.** A test reads the signature and asserts the absence.

`recommend_leverage()` returns the *highest* rung still clearing the safety bar,
because beyond that point extra leverage buys nothing at all — not more profit,
only a nearer liquidation.

## SPEC 17 — argument does not move a liquidation price

Council proposals can only ever pull the recommendation **down**. An agent may
object that the arithmetic is too aggressive; no agent may talk the engine past
a rung the liquidation maths refused. When a proposal is higher than the
arithmetic's choice it is recorded as overruled, with that sentence attached.

## SPEC 32 — derived, not tabulated

The tempting implementation is a table: `5m -> 10x, 1h -> 7x, 1d -> 3x`. Those
numbers come from nowhere. They would look exactly like a measurement while
being somebody's intuition, which is what SPEC 49 and FUTURES SPEC 51 forbid
most directly.

So F4 derives the one per-horizon effect that **is** arithmetic:

> In isolated margin, funding is paid out of the posted margin. Less margin means
> liquidation sits closer to entry. A position held across nine settlements has
> paid nine times, and its liquidation price at the end of that hold is not the
> one it started with.

Each rung is then scored on the **worse** of its entry state and its end-of-hold
state — the same rule `buffer_score` already applies to its own two measures,
because averaging a comfortable start against an uncomfortable finish would let
one paper over the other.

Measured on a real position — 0.1 BTC at 63,000, stop 5% away, funding 0.1% per
settlement:

```
entry only  -> 7x    2x:100 3x:100 4x:100 5x:100 7x: 89 10x: 46
4h          -> 7x    2x:100 3x:100 4x:100 5x:100 7x: 88 10x: 45
24h         -> 7x    2x:100 3x:100 4x:100 5x:100 7x: 86 10x: 43
7d          -> 5x    2x:100 3x:100 4x:100 5x:100 7x: 68 10x: 25

10x over 7d: 21 settlements, cost 132.30, margin 630 -> 497.70 (21% spent)
             liquidation 56,927 -> 58,256
             buffer HIGH_RISK(46) -> REJECT(25)
```

The gradient is the honest part. Four hours moves almost nothing, and the module
says so rather than manufacturing an effect. Seven days at elevated funding drops
7x below the safety bar and the recommendation falls to 5x.

### Three things SPEC 32 refuses to do

**It does not assume decay.** A short receives funding when the rate is positive,
and its margin *grows*. Reporting erosion in both directions would be a
fabricated conservatism, so the sign is carried through and the growth is
reported as growth.

**It does not treat missing funding as zero cost.** With no funding observation
the projection reports `decay_known=False` and says the decay is unknown — which
is not the same as zero, and nothing downstream may read it as zero.

**It does not project past 90 settlements.** Only the *next* rate is the venue's
forecast; every settlement after that assumes today's rate persists. Assuming it
ninety times over — thirty days at eight hours — is not a conservative estimate,
it is fiction with a decimal point, so it is refused rather than reported with a
caveat nobody reads.

### What it cannot measure, and says so

Longer holds are also exposed to gaps and to violent wicks. **A stop does not
execute through a wick; a liquidation does.** That exposure is real, ARUNA has no
measurement of it, and it is named in the findings rather than converted into a
penalty that would look derived. Every projection carries the sentence.

## SPEC 30 — the stress test that was flattering itself

Slippage is charged on both ends and against the position at each. The stop is
stressed too, and that is the part most often forgotten: a stop is not a
guaranteed price, it becomes a market order when touched, and the conditions that
trigger stops are the conditions that worsen fills.

**It was not doing this.** The exit at the target was pinned to the unstressed
price, so the loss leg carried slippage twice while the reward leg carried it
once — and the report still announced *"the edge survives every scenario on the
ladder, including a 0.5% adverse fill on both ends."* On a long at 50,000 with a
51,400 target, the honest net R:R was 0.85 and the reported figure was 1.10.

The band of wrongly-robust targets sat just above break-even, so the error
flattered exactly the marginal setups — the ones where the answer matters.

Three tests covered this code and all three passed. One asserted the *note
string* rather than the behaviour, which is how the false claim survived.

## SPEC 31 — advice, never an action

ARUNA never changes an account setting. ISOLATED is recommended because it bounds
the damage of one bad idea to the margin posted against it; CROSS lets a single
position reach the whole wallet. When correlated positions are already open the
recommendation carries the cascade reasoning explicitly.

## Defects found and fixed in this phase

Four came from running against live Binance data for the first time, and they
are the same family every prior phase produced.

**`max_leverage` was fabricated as 1.** `/fapi/v1/leverageBracket` needs a signed
request this read-only adapter cannot make, and the fallback silently emptied the
entire ladder: every rung above 1x was skipped and the engine reported "no safe
leverage" for a reason that had nothing to do with safety. `None` now means
unknown, the ladder runs, and every rung states that the venue's cap was not
fetched.

**The maintenance-rate fallback claim was inverted.** The code said using the base
rate "overstates the requirement slightly rather than placing liquidation further
away than it is." **The opposite is true.** Brackets only ever raise the rate with
size, so the base rate understates the requirement and places liquidation
*further* from entry than reality. On BTCUSDT's real tiers a 1,000,000 notional
long liquidates 151 in price before the number ARUNA reported. The error runs
toward optimism and grows with position size.

The same inverted sentence was written in three places — the docstring, the
runtime finding, and the contract notes — all of them reassuring. That is the
direction a wrong claim tends to travel.

**Two clocks in one coherence check.** The order book was stamped from the local
clock while every other input came from the venue's, so the venue/local offset
landed directly in the skew figure. The sharp form: any offset between roughly
32s and the 60s `MAX_CLOCK_LEAD_SEC` tripped `SKEWED` before `CLOCK_SKEW` could
fire, so half the range that constant declares tolerable could never produce an
OK verdict. Now stamped from the venue's `T`/`E`, which also makes venue-side lag
visible instead of invisible.

**One `try` swallowed five calls.** `snapshot()` fetched six inputs under a single
guard with `contract` — required — last, after `long_short_ratio` — optional. A
failure of the optional feed suppressed the required one, and the snapshot
reported `missing: contract` and blocked the signal, naming an endpoint that had
never been called. Integrity's wording for that state, "the venue did not supply
them", was false: it was not asked. Each input now fails on its own.

**One threshold in two copies.** `MIN_NET_RR` and `MIN_NET_REWARD_RATIO` were both
`1.0` in different modules. Tuning either would have left the slippage report
saying "survives" for a setup the economics had already called not worth taking.

## Live behaviour, verified

```
probe            reachable, ~360 ms
integrity        OK, blocks_signal=False
                 mark -6.9s  funding -6.9s  OI -2.3s  book -8.1s  L/S 153.1s
brackets         refused: signed endpoint, reported unknown, not guessed
place an order   refused before any network call
```

All four continuous inputs now sit in one clock frame. `long_short` at 153s is
its five-minute publication bucket, judged on its own cadence — at the continuous
limit it blocked 100% of signals.

## What F4 does not do

- **It produces no signal.** No LONG/SHORT/WAIT exists yet; the futures council
  is F5. F4 is arithmetic over inputs.
- **Nothing is stored.** There is still no futures schema.
- **Nothing calls it in production.** `stress_test`, `recommend_leverage` and
  `stress_slippage` are reachable from the package but no live path invokes them.
  That is expected at F4 and stated so it is not mistaken for wiring.
- **Margin brackets are unavailable**, so every liquidation price rests on the
  base maintenance rate and is optimistic by an amount that grows with size.
  Fixing this needs either a static bracket file or a credentialed path kept
  strictly outside this adapter.
- **Cascade risk is permanently `UNKNOWN`** until a websocket `forceOrder` feed
  exists (SPEC 26).
- **Slippage scenarios are assumptions**, not measurements. The ladder is a
  what-if, and the 0.05% default fee is a configured figure.

## The thing to keep in view

Unchanged from F1, and F4 makes it sharper rather than softer.

ARUNA's spot rules were measured at **50% direction accuracy over 580 daily
predictions** — a coin flip — and every futures module built so far reads the same
indicators through the same council. Leverage multiplies whatever edge exists. On
a zero edge it multiplies the losses and adds a liquidation price that spot does
not have.

F4's contribution to that problem is to make the leverage number as small as the
arithmetic allows and to refuse to let anything argue it upward. That is
damage control, not an edge. The edge has to be measured, and F6 is where the
same machinery that produced the 50% figure gets pointed at futures.

---

## Next: F5

The futures council and the signal format (SPEC 8–14, 37–39, 47): LONG/SHORT/WAIT
with entry, stop, target, R:R, position size, leverage, margin mode and
liquidation distance — locked under the SPEC 20 prediction lock so that F6 can
score it. ARUNA remains the analyst; the human executes, ignores, or overrides.
