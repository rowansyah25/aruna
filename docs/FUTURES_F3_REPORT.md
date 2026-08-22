# FUTURES F3 — Risk mathematics

**913 tests passing (+44) · ruff clean**

Covers FUTURES SPEC 19 (position size), 20 (risk per trade), 21 (stop loss),
22 (take profit), 23 (R:R and expected net PnL), 24 (liquidation), 25 (buffer
score), 26 (cascade).

---

## Four guarantees that are structural, not promised

Each blocks a specific way a leveraged system talks itself into a bad position,
and each is enforced by what a function *cannot be given* rather than by
discipline.

**The stop cannot be tuned to make a ratio look good (SPEC 21).**
`stop_loss()` takes no target, no reward, no ratio, no equity. It cannot be
moved toward a number it has never been shown. A test reads its signature and
asserts those parameters are absent, and a second asserts `stops.py` never
imports the economics module — so a ratio cannot flow back into stop placement
from the other direction either.

**Position size cannot be inflated by leverage or a profit goal (SPEC 19, 16).**
`position_size()` takes no `leverage` and no `target_profit`. The chain runs one
way only:

```
risk budget -> stop distance -> quantity -> notional -> leverage
```

Leverage is the last thing computed and it is an *output*. The method that
reports it is called `implied_leverage` for that reason. A test asserts the only
quantity assignment in the module is `budget / stop_distance`.

**Liquidation is the venue's formula or nothing (SPEC 24).** Binance USD-M,
isolated one-way:

```
LP = (WB + cumB - side x Q x EP) / (Q x MMR - side x Q)
```

with the maintenance bracket the notional actually falls into. Verified against
worked figures: 0.1 BTC at 50,000 on 1,000 margin liquidates at ~40,160 long and
~59,761 short. Without a contract specification it returns `None` — an
approximate liquidation price is worse than none, because it is the one number a
leveraged trader plans around.

**A liquidation in front of the stop scores zero (SPEC 25).** If the exchange
closes the position before the stop is reached, the stop is decorative and every
risk figure derived from it is fiction. That case is `REJECT`, not a low score.

## The buffer score takes the worse of two measures

Liquidation distance is scored against the stop distance *and* against ATR, and
the **minimum** wins. Averaging them would let a generous ATR buffer paper over a
stop sitting past the liquidation price. A missing liquidation price scores 0 —
an unknown buffer is treated as unacceptable, not as acceptable.

## UNKNOWN is not NONE

`detect_cascade([])` returns `UNKNOWN`, not `NONE`. Binance withdrew the REST
liquidation endpoint, so an empty list comes from a feed that does not exist —
and that looks exactly like a calm market. Treating it as one is how a position
gets sized into a cascade. The question is recorded as unanswered.

## One defect, caught by a failing test

`MIN_ATR_DISTANCE` was 0.5 ATR and `ATR_PADDING` is 0.5 ATR. A structural stop
sits at `(entry - level) + padding`, so its distance is *always* greater than
0.5 ATR — the "stop is inside the noise" check could never fire. A dead branch
of exactly the family the earlier phases kept turning up.

Raised to 0.75 ATR, with the reason written next to the constant and a test
asserting `MIN_ATR_DISTANCE > ATR_PADDING` so it cannot silently die again.

## Smaller honesty details worth naming

- **Rounding always costs a tick.** Stops round away from entry, targets round
  toward it. Rounding the other way would tighten a stop and extend a target —
  both flatter the setup for free.
- **Targets beyond 6 ATR are dropped**, with a note. A level the market reaches
  once a quarter is not a plan (SPEC 22).
- **Only net R:R is offered as a decision input** (SPEC 23). Gross ignores costs
  that are certain while counting a reward that is not, which flatters every
  setup and the marginal ones most.
- **Funding in the economics is labelled a projection**, since the rate moves
  before it settles.
- **CROSS margin liquidation is labelled a best case**, because other open
  positions move it.
- **A size below the venue minimum is refused**, not rounded up: taking it would
  mean risking more than planned.

## What F3 does not do

- **It chooses no leverage.** That is F4, and it reads the buffer score.
- **No cascade data exists** on this venue over REST, so SPEC 26 is built and
  correct but permanently `UNKNOWN` until a websocket feed is added.
- **Slippage is an assumption** (0.05% default), not a measurement. SPEC 30's
  stress test is F4.
- **Nothing is stored, and no signal is produced.** F3 is arithmetic over
  inputs.

---

## Next: F4

The leverage engine (SPEC 15–18): a single recommended number, chosen by
council debate and stress-tested across 2x–10x, reading the buffer score this
phase produces. Plus slippage stress (SPEC 30), margin mode advice (SPEC 31),
per-horizon leverage (SPEC 32) and the safety score (SPEC 36).
