# FUTURES F6 — Learning and the daily report

**1081 tests passing · ruff clean**

Covers FUTURES SPEC 40–45 (outcome scoring and learning), 48 (the daily
account).

---

> **The most important sentence in this report:** nothing here has measured
> anything. Zero futures plans have resolved. Every figure this phase produces
> reports insufficient sample, and it says so in its first line. The machinery
> is built and wired; it has no data.
>
> Phase 8 opened with the same sentence for the spot system, and four months
> later the data arrived and said the rules had no edge. That is the point of
> writing the machinery before the results exist.

## What F6 delivers

| File | Covers |
|---|---|
| `futures/learning.py` | Five outcomes, the liquidation asymmetry, the daily report |
| `futures_plan_results` (migration 0015) | Storage, with a constraint on the one outcome that matters most |

## A perpetual needs a question spot never has to ask

The spot learning layer asks whether a direction was right. A leveraged position
needs a harder one: **did the exchange close it before the trader could?**

```
TARGET_HIT     the plan worked
STOPPED_OUT    the idea was wrong, which is the outcome it was planned for
LIQUIDATED     the stop was decorative and every figure derived from it was fiction
EXPIRED        neither level reached; says nothing about direction
OPEN           not yet resolved
```

A liquidation is not a bad outcome on a spectrum with a bad fill. It is the plan
having been wrong about the one thing it promised to be right about.

## Sample thresholds do not apply to liquidations

This is the design decision the phase turns on.

Everywhere else in ARUNA, a figure below its threshold reports
`INSUFFICIENT_SAMPLE`, because reading a trend from four observations is how a
system fools itself. **A liquidation is not a trend.** One liquidation on a plan
ARUNA recommended is a defect in the leverage engine, reportable at n=1, and
averaging it into a win rate is precisely the arithmetic that would hide it.

So the verdict checks liquidations *before* it checks sample size, and a test
asserts both conditions holding at once — three wins and one liquidation
produces the liquidation message, not the insufficient-sample one. Waiting for
fifty liquidations before mentioning one would be an absurd reading of a rule
that exists to prevent over-reading noise.

The database agrees: an outcome of `LIQUIDATED` that does not record having
touched the liquidation level is refused by a CHECK constraint.

## A win that touched liquidation is not a clean win

Reported separately as `near_liquidations`, with no threshold. A plan whose
price reached the liquidation level and survived on a wick was not a safe plan
that worked; it was an unsafe plan that got away with it, and counting it as a
win launders the distinction that matters most for judging the leverage engine.

## The exchange acts first, and does not wait its turn

Within one bar the order is **liquidation → stop → target**, because that is the
order the exchange applies them. A bar that traded through both the stop and the
target is a loss; a bar that traded through both the stop and the liquidation
price is a liquidation.

Any other ordering flatters the record on exactly the bars where the outcome was
genuinely in doubt — which are the bars a win rate is most sensitive to.

## Plans ARUNA declined to make are not scored here

`score_plan` returns `None` for a WAIT, a refusal or a NO_SIGNAL. Folding them
in would let the plans ARUNA declined to make count toward the win rate of the
ones it did. They are counted separately, in the daily tally, where they belong.

## The daily report leads with the refusals

```
CONSIDERED:  48
  plans:     2
  refused:   40
  waited:    5
  no signal: 1
```

A day of two plans and forty refusals is a day ARUNA mostly said no, and a
report opening with the two would describe a different system from the one that
ran. A day with no plan at all reads: *"No plan was issued. That is an output,
not a failure to produce one."*

Every report ends by stating that ARUNA placed no order, changed no leverage or
margin setting, and moved no funds — today or on any day (SPEC 3, 50).

## What F6 does not do

- **It has measured nothing.** Repeated here because it is the only honest
  headline: two plans are stored, both refusals, and no plan has resolved.
- **No autopsy.** SPEC 25's loss autopsy and SPEC 26's successful-objection
  analysis are not ported to futures. They need resolved plans and there are
  none.
- **No calibration.** The council's confidence has never been compared against
  a futures outcome.
- **Funding projection accuracy is unmeasured.** Comparing a projected rate
  against what settled needs history the system has not accumulated.
- **Slippage remains an assumption**, and cannot become a measurement without
  fills — which ARUNA will never have, because it does not trade. This one is
  permanent, not pending.
- **No scheduler resolves plans.** `score_plan` runs when something calls it;
  nothing calls it on a timer.

## What would make this phase mean something

Plans, resolved, over months. The spot system needed 580 daily predictions
before its 50% accuracy was a fact rather than a worry, and the futures system
will need something comparable before any figure here is worth acting on.

Until then the correct reading of every number F6 produces is: **not enough
data, and ARUNA says so rather than filling the gap.**

---

## Where the futures track stands

F1–F6 are built. The perpetual adapter is read-only by construction, the data
integrity gate blocks incoherent input, the risk mathematics refuse to be tuned,
the leverage engine will not be argued upward, the plan refuses more often than
it accepts, the storage is append-only and fingerprinted, and the learning layer
is waiting for data.

None of that is an edge. It is a system that will report honestly when the
measurement finally happens — which is what the spot track's 50% result proved
was worth building first.
