# PHASE 10 — Shadow model, drift, experiments, human approval

**Version 1.0.0 · 797 tests passing · ruff clean · migrations 0001–0014 applied**

*(§8a records two defects found in an audit pass after first delivery.)*

The last phase of the specification. All ten are now built.

---

## 1. Project structure

```
src/aruna/
  governance/
    research.py    SPEC 31 questions derived from measurements   (228)
    proposal.py    SPEC 44 proposals and the validation bar      (263)
    approval.py    the human gate - deliberately small           (112)
    shadow.py      variants run alongside, never acted on        (109)
    drift.py       has the world left the validation period?     (140)
    service.py     derives, stores, carries a human's decision   (156)
  db/repositories/
    governance.py  questions and proposals upsert; decisions do not (146)
migrations/
  0014_governance.sql  4 tables, 2 append-only triggers          (127)
tests/
  test_governance.py   37 tests, the gate first                  (431)
```

Changed: `app.py`, `cli.py`, `notify/telegram/bot.py`,
`notify/telegram/formatting.py`, `db/repositories/learning.py` (drift windows),
`db/repositories/backtest.py` (recent runs), `core/config.py`, `__init__.py`.

## 2. Files created

| File | What it holds |
|---|---|
| `governance/research.py` | Questions derived from measurements, never invented |
| `governance/proposal.py` | Proposals, and the verdict that refuses thin evidence |
| `governance/approval.py` | The gate. No path from a good number to an active change |
| `governance/shadow.py` | Variant comparison that admits when it learned nothing |
| `governance/drift.py` | Performance drift vs condition drift, kept apart |
| `governance/service.py` | Ties them to storage |
| `db/repositories/governance.py` | Decisions insert-only |
| `migrations/0014_governance.sql` | Named-human CHECK, append-only triggers |
| `tests/test_governance.py` | 37 tests |

## 3. Dependencies

**None added.** ARUNA finishes on the same dependency set it started PHASE 2
with, plus `yfinance`.

## 4. Windows setup

Unchanged from PHASE 1.

## 5. `.env.example`

Unchanged, and deliberately so. There is no configuration key that lowers the
validation bar, shortens the shadow period, or enables automatic approval. A
gate that a `.env` file can open is not a gate.

## 6. How to run

```bash
python -m aruna migrate         # applies 0014
python -m aruna research        # questions the record raises, and drift
python -m aruna proposals       # proposals and the decisions on record
```

Telegram: `/research`, `/proposals`, and the privileged `/approve <key> [note]`
and `/reject <key> [note]`. The Telegram username is recorded as the approver;
a chat with no username or display name is refused rather than falling back to
a numeric id, because "chat 12345 approved it" is not accountability.

## 7. How to test

```bash
python -m pytest tests/test_governance.py -v
python -m pytest
python -m ruff check src tests
```

## 8. Test results

**795 passed** (up from 758), 7m44s. ruff clean. No defects found while
building this phase — the first time that has been true, which most likely
means this phase reuses more measured machinery than it adds.

The gate was also walked end-to-end against live MySQL, using the PHASE 9
finding as the proposal:

```
2. approve before validating        -> refused (not validated)
4. approve on a NO_IMPROVEMENT      -> refused (evidence did not clear)
6. approve as 'system'/'aruna'/''   -> refused (not a person)
7. approve as 'rowan'               -> APPROVED, recorded
8. INSERT decision as 'system'      -> refused by CHECK constraint
9. UPDATE the recorded decision     -> refused by trigger
```

Four independent layers refuse the same thing: the application, the actor check,
the database constraint, and the append-only trigger.

## 8a. Corrections after delivery

**SPEC 26 objection patterns never reached a research run.**
`questions_from_objections` was written, exported and unit-tested, and the
service reached it only through `objection_questions` — a wrapper nothing
called. So the most specific signal the record produces (this agent, on this
ground, repeatedly overruled and repeatedly right) never became a question. The
service now derives it directly from `overruled_objections`, and the dead
wrapper is gone.

That is the same defect family as PHASE 7's unwired sampler and PHASE 9's
uncalled holdout guard: code that is reachable, tested, and never reached.

**A question the record stopped raising stayed open forever.** Questions are
re-derived from measurements on every run, but nothing ever closed one whose
condition had gone away — so a solved problem would be reported as outstanding
indefinitely and the list could only grow. `close_absent` now closes what was
not re-derived, and refuses to close anything when a run derives nothing at all,
since that is more likely a broken run than every problem being fixed at once.

## 9. Data sources

None new. Every question here is derived from ARUNA's own record.

## 10. Features implemented

**SPEC 31 — research questions.** Derived from measurements, each carrying the
numbers that provoked it. Run against the live record, ARUNA independently
raised the same four findings that were diagnosed by hand from the PHASE 9
backtest — including, at severity 1.00, *"direction accuracy is 50% over 580
predictions — indistinguishable from chance. Does the council have any
directional skill to execute on?"*

That is the phase working as intended: the system reading its own record and
asking the question that most threatens it.

**SPEC 44 — proposals and validation.** A proposal is a written hypothesis with
a comparison. The verdict refuses three specific ways of being fooled:

- `INSUFFICIENT_SAMPLE` below 100 resolved predictions per arm;
- `NO_IMPROVEMENT` below a 2-point effect — not everything measurable is worth
  changing a system in use for;
- `WITHIN_NOISE` when the effect is inside its own standard error, **with the
  bar rising as more variants are tested**. Test twenty variants and one looks
  good; that is the most common way a backtest lies to the person running it,
  and the count comes from storage so a proposal cannot understate it.

An in-sample result never clears, however good it looks.

**The human gate.** `approve()` requires a named actor and refuses `system`,
`aruna`, `auto`, `automatic`, `none` and blank. There is no `auto_approve`, no
confidence threshold that implies approval, and no configuration that could add
one. The database agrees independently: `proposal_decisions` has a CHECK
constraint rejecting those same names and an append-only trigger, so who
approved a change cannot be rewritten afterwards.

Approval and rejection are deliberately asymmetric: approval needs validated
evidence *and* a person, rejection needs only a person. Stopping a change must
never be harder than making one.

**Shadow models.** Recorded alongside, never acted on. The comparison reports
the disagreement rate *first*: two models differing on 3 of 500 decisions have
produced a comparison resting on 3 observations, however impressive the other
497 look. Below a 5% disagreement rate the verdict is `INDISTINGUISHABLE`.

**Drift.** Performance drift (the rules do worse in the same conditions) is kept
separate from condition drift (the market has moved), because fixing the wrong
one is worse than waiting. Both stay silent below 60 resolved predictions per
window — a detector that cries wolf trains its operator to ignore it.

## 11. Dummy features

None in the code.

**One artefact in the database needs declaring.** Walking the gate against live
MySQL left a real `APPROVED` row for the proposal `exit-at-target`. Its
comparison numbers were **illustrative, not measured** — the variant arm was
supplied by hand to exercise the gate — and **the change it describes is not
implemented**. The decision table is append-only by design, so the row stays.
Reversing it requires a new proposal, which is the mechanism working correctly.

It is named here because a reader querying `proposal_decisions` would otherwise
find an approved change that does not exist in the code.

## 12. Limitations

**No proposal has been validated on real measured evidence.** The machinery
exists; nothing has yet run a variant long enough to compare honestly.

**Shadow models are not wired into the live loop.** `ShadowComparison` scores
decisions once something records them; nothing in `aruna signal` currently runs
a second council alongside the first. Executing a variant in shadow needs a
second parameterised rule set, which does not exist because ARUNA has no
parameterised rules — every constant is a written literal.

**Drift reports nothing yet**, correctly: 0 resolved predictions in each window
against a floor of 60.

**Research questions are derived from a fixed set of rules.** Four question
shapes (cost ratio, accuracy at or below chance, calibration gap, dominant
failure class) plus repeated vindicated objections. A pattern outside those
shapes will not be noticed, and no language model reads the record looking for
one.

**The multiple-comparisons correction is a heuristic**, not a formal family-wise
error rate: `2.0 + log₄(attempts)` standard errors. It is defensible and stated;
it is not a substitute for a pre-registered test.

**Approval is per-proposal, not per-deployment.** Nothing checks that the code
running matches the set of approved proposals. A change could be made in source
without a proposal, and this phase would not notice — that would need a
model-version fingerprint compared against the approval record.

---

## The state of the system, ten phases in

ARUNA does what the specification asked: it ingests two markets, reasons over
them with an adversarial council, locks predictions it cannot later edit, scores
itself honestly, replays its own decisions deterministically, and refuses to
change itself without a person.

What it does not do is make money. The PHASE 9 backtest measured 50% direction
accuracy over 580 daily predictions — a coin flip — and costs several times any
gross edge. PHASE 10's own research module now raises that as its most severe
open question, unprompted.

That is the correct end state for this build. A system that reported a plausible
edge on this evidence would be lying, and every mechanism in the ten phases
exists to make that particular lie hard to tell.

The next work is not another phase. It is answering the question the system is
now asking: **is there any directional skill here to execute on?** The two
proposals worth testing first are already written down in the PHASE 9
conclusion — exit logic, and a cost floor that refuses signals whose target
cannot cover the round trip.
