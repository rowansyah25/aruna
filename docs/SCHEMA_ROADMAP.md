# Schema roadmap

SPEC 42 lists the complete ARUNA table set.  PHASE 1 does **not** create all of
them.

**Why.** SPEC 45 requires each phase to be runnable and tested before the next
begins, and SPEC 49 forbids presenting an unbuilt feature as working.  Forty
empty tables that no code reads or writes would misrepresent what exists, and
would lock in column shapes before the code that uses them has been designed.
Each table is therefore created by the migration belonging to the phase that
first reads or writes it.

Every SPEC 42 table is accounted for below.

## PHASE 1 — created, in `migrations/0001_core.sql` and `0002_market_reference.sql`

| Table | Written by | Read by |
|---|---|---|
| `schema_migrations` | migration runner | `aruna migrate`, health check |
| `markets` | migration `0002` | universe repository, health |
| `assets` | `aruna seed` | universe repository |
| `app_state` | kill switch | startup state restore |
| `system_events` | health monitor, app lifecycle | `/status`, operator review |
| `audit_logs` | Telegram commands, lifecycle | operator review |
| `telegram_subscribers` | Telegram bot | operator review |

`telegram_subscribers` is not in SPEC 42; it exists because the bot needs a
record of which chats have contacted it, including refused ones.

## PHASE 2 — market data

`candles`, `market_snapshots`, `fundamentals`

`market_ticks` was built here and **dropped by migration 0020**. PASAL 26 keeps
SQL for long-term analysis memory rather than a tape of every observation, and
the table had made the case itself: 76,567 rows written, zero read: its two
reader methods had no callers anywhere in `src/` or `tests/`.

Still open, and not fixed by that drop: `market_snapshots` takes one INSERT per
poll per asset with no sampling. It has real readers (the Telegram market
commands and the health check), so thinning or relocating it is a decision
about those readers rather than a clean-up.

Shapes depend on what the chosen providers actually return, which is why they
are not guessed now.  Each must carry the SPEC 4 provenance columns:
`provider_timestamp`, `server_timestamp`, `latency_ms`, `source`.

These are the highest-volume tables in the system — crypto runs 24/7 — and
MySQL's partitioning is weaker than PostgreSQL's for this shape, with no BRIN
equivalent. Plan `RANGE` partitioning on the timestamp from the start rather
than retrofitting it.

## PHASE 3 — analysis ✅ created (`0004_analysis.sql`)

`technical_snapshots`, `volume_snapshots`, `regimes`

Keyed on `(asset_id, interval_code, as_of)` so recomputing a bar refreshes its
row. `as_of` is the newest **settled** bar behind the row — that column is what
lets a PHASE 9 replay prove no future data leaked in (SPEC 24), which is why it
is NOT NULL everywhere.

Readings are stored as JSON *with their sample sizes* rather than as bare float
columns: SPEC 6 treats indicators as evidence, and a column holding `47.3` with
no record of how many bars produced it can only be believed, not weighed.

## PHASE 4 — news and context

`news_events`

## PHASE 5–6 — agents and council

`council_sessions`, `agent_decisions`, `agent_objections`, `agent_rebuttals`,
`veto_events`, `veto_reviews`, `judge_decisions`

`veto_events` and `veto_reviews` stay separate tables: SPEC 19 makes a veto
reviewable, so the review is its own record with its own outcome, never a
column mutated on the veto row.

## PHASE 7 — signals and paper trading

`signals`, `signal_snapshots`, `setup_dna`, `historical_matches`,
`paper_trades`, `paper_results`, `outcome_snapshots`

`signal_snapshots` is the SPEC 20 prediction lock.  It must be enforced
append-only in the schema, the same way `audit_logs` already is in PHASE 1: a
pair of `BEFORE UPDATE` / `BEFORE DELETE` triggers that `SIGNAL SQLSTATE
'45000'`, so an application bug cannot edit a locked prediction.
`outcome_snapshots` records the SPEC 23 sampling points.

This phase is also where MySQL's lack of `jsonb` starts to matter: snapshots
and `setup_dna` will be queried *into*, and MySQL cannot index inside a JSON
document. Expect to add generated columns with functional indexes for whichever
JSON paths turn out to be hot.

## PHASE 8 — post-mortem and calibration

`loss_autopsies`, `counterfactuals`, `ghost_signals`,
`performance_daily`, `performance_weekly`, `performance_monthly`

`ghost_signals` holds WAIT decisions (SPEC 28) — they are evidence about the
filter's quality and are stored on the same footing as acted-on signals.

## PHASE 9 — validation

`backtests`, `walk_forward_runs`

## PHASE 10 — model governance

`learning_patterns`, `research_questions`, `experiments`, `model_versions`,
`model_validations`, `shadow_models`

`model_versions` is deliberately not created in PHASE 1.  There is no model in
this build, and seeding a row for one would be a fabricated record.

## Conventions for later migrations

MySQL 8.0.16+ (developed against 8.4.3).

- Filename `NNNN_lower_snake_case.sql`; the runner rejects anything else.
- Forward only.  There are no down-migrations: rolling back a schema whose
  premise is immutable prediction records (SPEC 20) would destroy evidence.
- Never edit an applied migration.  The runner stores a SHA-256 of each file
  and refuses to start when one changes.
- **Keep each migration small.** MySQL commits DDL implicitly, so a file that
  fails halfway leaves the earlier statements applied and cannot be rolled
  back. Small files bound the blast radius.
- **One statement per statement.** No `BEGIN ... END` bodies — the runner's
  splitter deliberately does not implement `DELIMITER`, which is a client
  convention rather than SQL. Trigger bodies stay single-statement.
- **`DATETIME(6)`, never `TIMESTAMP`.** `TIMESTAMP` is re-interpreted per
  session timezone and cannot represent a date past 2038. Always write UTC;
  every connection is pinned to `+00:00`.
- **`VARCHAR`, not `TEXT`, for anything indexed.** MySQL cannot index `TEXT`
  without a prefix length, and a prefix index weakens uniqueness guarantees.
- Tag naive `DATETIME` values as UTC at the repository boundary via
  `aruna.db.types.as_utc`. Nothing above `db/` should see a naive timestamp —
  that is how look-ahead bugs (SPEC 24) become invisible.
- Evidence tables (`signal_snapshots`, `audit_logs`, outcome records) get the
  append-only trigger pair.
- `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci` on every
  table.
