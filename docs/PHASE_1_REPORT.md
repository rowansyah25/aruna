# PHASE 1 delivery report

Per SPEC 49. Written after the build, from actual runs — not from intent.

- **Scope delivered:** core infrastructure, database, config, logging,
  Telegram, health monitor (SPEC 45, PHASE 1)
- **Markets:** CRYPTO and IDX. Forex is absent by construction.
- **Trading mode:** PAPER only. Real execution does not exist in the codebase.
- **Database:** MySQL 8.4 (Laragon's bundled server), chosen over the
  specification's PostgreSQL recommendation — see §12 for what that costs.

---

## 1. Project structure

```
aianalis/
├─ .env.example                  environment template (no secrets)
├─ .gitignore                    excludes .env, logs/, .venv/
├─ pyproject.toml                package, pytest, ruff config
├─ requirements.txt              runtime dependencies
├─ requirements-dev.txt          + pytest, ruff
├─ README.md                     setup and usage
├─ docs/
│  ├─ PHASE_1_REPORT.md          this file
│  └─ SCHEMA_ROADMAP.md          every SPEC 42 table mapped to its phase
├─ scripts/
│  ├─ setup.ps1                  venv + dependencies + .env
│  └─ start_redis.ps1            start/stop Laragon's bundled Redis
├─ migrations/
│  ├─ 0001_core.sql              PHASE 1 tables
│  └─ 0002_market_reference.sql  the two market rows
├─ src/aruna/
│  ├─ __init__.py, __main__.py, cli.py, app.py
│  ├─ core/     config, enums, clock, errors, logging, redaction, runtime_state
│  ├─ db/       pool, migrator, types, repositories/
│  ├─ cache/    redis_client
│  ├─ health/   models, checks, monitor
│  ├─ notify/   telegram/{bot, registry, formatting}
│  └─ seed/     universe
└─ tests/       12 test modules
```

## 2. Files created

47 Python files (6,672 lines) and 2 SQL migrations (185 lines).

**Core** — `core/config.py` (settings groups, market/trading guards, forced
MySQL session settings, secret inventory), `core/enums.py` (domain vocabulary),
`core/clock.py` (UTC/WIB, IDX and crypto sessions), `core/logging.py`
(structlog pipeline), `core/redaction.py` (secret scrubbing),
`core/runtime_state.py` (kill switch), `core/errors.py`.

**Database** — `db/pool.py` (asyncmy pool, query helpers, diagnostic hints),
`db/migrator.py` (forward-only runner, SQL statement splitter),
`db/types.py` (UTC tagging and JSON codecs at the driver boundary),
`db/repositories/` (`app_state`, `events`, `universe`, `telegram`).

**Cache** — `cache/redis_client.py`.

**Health** — `health/models.py`, `health/checks.py` (six probes),
`health/monitor.py` (debounce, edge-triggered alerts).

**Telegram** — `notify/telegram/registry.py` (all SPEC 40 commands with phase
metadata), `notify/telegram/bot.py`, `notify/telegram/formatting.py`.

**Application** — `app.py` (lifecycle), `cli.py` (8 commands), `seed/universe.py`.

## 3. Dependencies

Runtime: `pydantic>=2.9`, `pydantic-settings>=2.6`, `asyncmy>=0.2.9`,
`redis>=5.2`, `structlog>=24.4`, `python-telegram-bot>=21.9`, `tzdata>=2024.2`.

Dev: `pytest>=8.3`, `pytest-asyncio>=0.24`, `pytest-cov>=6.0`, `ruff>=0.8`.

Installed and tested against: pydantic 2.13.4, pydantic-settings 2.15.0,
asyncmy 0.2.12, redis 8.1.0, structlog 26.1.0, python-telegram-bot 22.8,
tzdata 2026.3, pytest 9.1.1, pytest-asyncio 1.4.0, ruff 0.16.3.

`tzdata` is not optional on Windows: without it `ZoneInfo("Asia/Jakarta")`
raises and the IDX session clock cannot be computed at all.

## 4. Windows setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev
```

Finds a Python 3.12+ interpreter (including one bundled under
`C:\laragon\bin\python`), creates `.venv`, installs dependencies, installs
`aruna` in editable mode, and copies `.env.example` to `.env` if absent.

Laragon's MySQL must be running. Its default `root` with an empty password is
what `.env.example` targets.

## 5. `.env.example`

Present at the repository root, with every key ARUNA reads, grouped by
subsystem, and no real values. Sections: application, logging, MySQL, Redis,
Telegram, health monitor, data providers (PHASE 2 placeholders).

`.env` is gitignored (SPEC 43). No secret appears anywhere in source.

## 6. How to run

```powershell
.\.venv\Scripts\python.exe -m aruna doctor      # check every prerequisite
.\.venv\Scripts\python.exe -m aruna createdb    # create the schema
.\.venv\Scripts\python.exe -m aruna migrate     # apply migrations
.\.venv\Scripts\python.exe -m aruna seed        # load the universe
.\.venv\Scripts\python.exe -m aruna run         # start (Ctrl+C to stop)
```

Also: `version`, `config`, `health`.

## 7. How to test

```powershell
.\.venv\Scripts\python.exe -m pytest                        # everything
.\.venv\Scripts\python.exe -m pytest -m "not integration"   # no database needed
.\.venv\Scripts\python.exe -m ruff check src tests          # lint
```

Integration tests create and drop `<ARUNA_DB_NAME>_test`, never the working
schema, and skip cleanly when MySQL is unreachable.

## 8. Test results

Run on 2026-08-15 against **live MySQL 8.4.3 and Redis 5.0.14.1**. Nothing was
skipped or mocked at the database boundary.

```
306 passed in 48.51s
ruff check src tests — All checks passed
```

| Module | Tests | Covers |
|---|---:|---|
| `test_db_integration.py` | 42 | live round trips: session UTC pin, strict mode, CHECK enforcement, upsert identity, append-only triggers, aware timestamps |
| `test_config.py` | 40 | forex rejection, real-trading rejection, forced session settings, secret inventory, DSN masking, fail-closed allowlist |
| `test_migrations.py` | 36 | statement splitter (quotes, escapes, comments), filename rules, checksum guard, schema invariants |
| `test_clock.py` | 30 | IDX sessions (Mon/Fri/weekend/lunch), crypto bands, aware-datetime enforcement |
| `test_telegram.py` | 29 | SPEC 40 registry completeness, honest not-implemented text, message rendering and length |
| `test_enums.py` | 27 | horizon durations, minute/month disambiguation, council roster, regime set |
| `test_health.py` | 27 | aggregation, debounce, edge-triggered events, probe-crash isolation, background loop |
| `test_redaction.py` | 21 | key- and value-based scrubbing, token/DSN/bearer shapes, rotation |
| `test_seed.py` | 21 | MVP universes, no invented tick sizes, JSON override validation |
| `test_cli.py` | 14 | command wiring |
| `test_runtime_state.py` | 11 | kill switch lifecycle, persistence, phase guard |
| `test_logging.py` | 8 | JSON output, context binding, redaction including third-party records |

### End-to-end run

`aruna createdb` → `migrate` → `seed` → `run` all executed successfully.
Verified by querying MySQL afterwards:

- `assets`: CRYPTO 5, IDX 11
- `system_events`: startup and per-component health transitions recorded
- `audit_logs`: matched `APPLICATION_START` / `APPLICATION_STOP` pairs
- session timezone `+00:00`; an event stored at `01:35:04 UTC` while local WIB
  was `08:35` — the UTC pin works

`aruna health` reports overall `UP` and exits 0.

### Five defects found and fixed during verification

Each was found by running the system or by a test, not by reading the code.

1. **`ARUNA_ENABLED_MARKETS=CRYPTO,IDX` failed to parse.** pydantic-settings
   JSON-decodes collection fields at the source layer, before any validator
   runs. Fixed with `NoDecode` on `enabled_markets` and `allowed_chat_ids`.
2. **Redis rejected the connection**: `unknown command 'HELLO'`. redis-py 8
   defaults to RESP3, whose handshake predates Redis 6.0 — including the 5.0.14
   Laragon bundles. Pinned to `protocol=2`; ARUNA uses no RESP3-only command.
3. **asyncmy's `sql_mode=` parameter is unusable.** It interpolates the value
   into `SET sql_mode = <value>` *unquoted*, so any comma-separated mode list
   is a syntax error. All session settings now go through a single
   `init_command`, which also makes them apply atomically.
4. **Health was permanently DEGRADED.** Unconfigured PHASE 2 providers were
   counted as configuration warnings, so `aruna health` always exited non-zero
   and was useless as a gate. Split into `phase_notices()` (expected at this
   build stage, reported but not counted) and `startup_warnings()` (wrong now,
   degrades health).
5. **The log redactor missed Redis passwords.** Its inline-credential pattern
   required a username before the colon, but ARUNA's own Redis URL is
   `redis://:password@host` — no username. A Redis password surfacing in a
   driver error message would have reached the log file. The configured-secrets
   list still covered it; the pattern is the backstop for values that are not in
   that set, and it had a hole.

## 9. Data sources

**None are connected. `STATUS: DATA SOURCE UNAVAILABLE` for all four.**

| Provider | Env keys | State |
|---|---|---|
| Crypto | `ARUNA_CRYPTO_PROVIDER*` | declared, empty, unused — PHASE 2 |
| IDX | `ARUNA_IDX_PROVIDER*` | declared, empty, unused — PHASE 2 |
| News | `ARUNA_NEWS_PROVIDER*` | declared, empty, unused — PHASE 4 |
| Fundamental | `ARUNA_FUNDAMENTAL_PROVIDER*` | declared, empty, unused — PHASE 4 |

`aruna doctor`, `aruna config`, and `/status` all report this explicitly. No
price, volume, news, or fundamental value exists anywhere in this build.

Provider selection is a PHASE 2 decision and, per SPEC 47, must use licensed or
officially authorised sources, with `source` and `provider_timestamp` stored on
every record for audit. Nothing here presumes a particular vendor.

## 10. Features implemented

Working, tested against live infrastructure, not placeholders:

**Configuration** — env-driven with strict validation; `ARUNA_ENABLED_MARKETS`
rejects `FOREX`/`FX`/`CURRENCY` by name; `ARUNA_REAL_TRADING_ENABLED=true` is
refused at startup (SPEC 46); `ARUNA_PAPER_TRADING_ENABLED=false` is refused;
credentials held as `SecretStr` and never rendered.

**MySQL session hardening** — every connection is opened with
`time_zone='+00:00'`, strict `sql_mode`, and a server-side
`max_execution_time`, in one statement. The health monitor re-checks the
timezone on every sweep and reports **DOWN** if it is ever not `+00:00`, because
a connection without the pin would write wrong timestamps that nothing else
would reveal.

**Logging** — structlog with console and JSON renderers, rotating JSON file
(20 MB × 10), context binding, third-party stdlib records routed through the
same pipeline. Two-layer secret scrubbing (by field name and by value shape),
verified to hold for asyncmy/telegram records too.

**Database** — asyncmy pool; typed error wrapping with actionable hints
(wrong password, missing schema, server not running); forward-only migration
runner with SHA-256 guarding and a quote/comment-aware statement splitter;
repositories for app state, system events, audit log, universe, and Telegram
subscribers. Naive `DATETIME` values are tagged UTC at the repository boundary,
so nothing above `db/` ever sees a naive timestamp.

**Schema guards** — `markets.code` is `CHECK (code IN ('CRYPTO','IDX'))`, so
forex cannot be inserted even by direct SQL. `audit_logs` is append-only,
enforced by triggers that `SIGNAL SQLSTATE '45000'` — `UPDATE` and `DELETE`
raise rather than silently doing nothing. `assets` enforces a coherent
listing/delisting pair, which SPEC 35 needs for survivorship bias. All verified
by live tests.

**Redis** — optional; connects, pings, namespaced JSON get/set, TTL counters for
rate limiting. Degrades quietly with one warning, never taking the app down.

**Health monitor** — six probes (database, redis, telegram, config, clock,
process). Worst-status aggregation with `DISABLED` excluded. Debounce so a
single blip reads DEGRADED rather than DOWN. Edge-triggered: events and alerts
fire on transitions, not every sweep. A probe that raises becomes one DOWN
component instead of aborting the sweep. Transitions land in `system_events`
and, when Telegram is live, as an alert.

**Telegram** — all SPEC 40 commands registered. Six implemented: `/start`,
`/help`, `/status`, `/health`, `/kill`, `/resume`. Allowlist fails closed.
Every attempt — accepted or refused — is written to `audit_logs`. Rate limiting
via Redis, failing open so a cache outage cannot block `/kill`. Plain text, no
`parse_mode`, so no escaping bug can drop an alert.

**Kill switch** — durable in `app_state`, restored at startup, surfaced in
`/status` and in the health report as DEGRADED.

**CLI** — 8 commands. `doctor` reports every problem at once rather than
stopping at the first, and gates on MySQL ≥ 8.0.16 because older servers parse
CHECK constraints and then ignore them.

**Lifecycle** — ordered startup with deliberate failure modes: MySQL and a
pending migration are fatal; Redis and Telegram are not. Reverse-order shutdown
where one failing component cannot prevent the others from closing. SIGINT and
SIGTERM handled, including the Windows path where `add_signal_handler` is
unavailable.

## 11. Dummy / not implemented

Named plainly, per SPEC 49.

**Registered but not built** — 19 Telegram commands: `/crypto`, `/btc`, `/eth`,
`/sol`, `/stocks`, `/bbca`, `/bbri`, `/bmri`, `/council`, `/signals`, `/today`,
`/performance`, `/weekly`, `/monthly`, `/autopsy`, `/research`, `/proposals`,
`/approve`, `/reject`. Each replies with `NOT IMPLEMENTED`, the phase it needs,
and what that phase delivers. None returns fabricated output.

**Declared but unused** — the four provider settings groups. Read at startup and
reported as unavailable; no code consumes them.

**Vocabulary without behaviour** — `core/enums.py` defines regimes, agent roles,
council rounds, veto reasons, loss causes, valuation verdicts, and no-trade
reasons. These are the storage contract for later phases. Nothing computes them
yet, and the enums alone do not constitute analysis.

**Entirely absent from this build** — market data ingestion, WebSocket
streaming, data-quality detection (SPEC 5), technical indicators, market
structure, regime detection, news, fundamentals, every AI agent, the council and
its protest rounds, veto and veto review, the judge, prediction lock, paper
trading, outcome sampling, loss autopsy, counterfactuals, ghost signals,
calibration, backtest, walk-forward, out-of-sample, decision replay, shadow
models, drift detection, daily/weekly/monthly reports.

## 12. Limitations

### Introduced by choosing MySQL over PostgreSQL

The specification (SPEC 44) recommends PostgreSQL. MySQL was chosen for
operational convenience — it ships with Laragon and needs no credential setup.
These are the consequences, listed so nobody rediscovers them later:

**Migrations are not atomic.** MySQL implicitly commits before and after every
DDL statement, so a migration that fails halfway leaves the earlier statements
applied. The runner does not pretend otherwise: it names the failing statement,
refuses to record the migration, and says which statements are already in place.
Keeping each migration small is the only real mitigation. PostgreSQL would give
transactional DDL for free.

**`DATETIME` has no timezone, so correctness depends on a session setting.**
Mitigated on every connection (`time_zone='+00:00'`), verified by a health probe
that reports DOWN if it drifts, and covered by live tests. It works — but it is
a convention held in place by three moving parts, where `timestamptz` would be
one column type. This is the risk most worth watching as PHASE 7 adds immutable
prediction records.

**No partial indexes.** `system_events` indexes all four severities where
PostgreSQL would index only `ERROR` and `CRITICAL`. Costs storage, not
correctness.

**JSON, not `jsonb`.** MySQL cannot GIN-index inside a JSON document. PHASE 7's
`signal_snapshots` and `setup_dna` will query into JSON heavily; expect to add
generated columns with functional indexes there, which PostgreSQL would not
need.

**Weaker time-series partitioning** for the PHASE 2 `market_ticks` and `candles`
tables. No BRIN equivalent, no TimescaleDB.

The repository layer returns dataclasses rather than driver rows, so the whole
database choice stays contained in `src/aruna/db/`. Switching later means
rewriting that package — not the health monitor, Telegram, or lifecycle code.

### Independent of the database choice

**No IDX holiday calendar.** `IdxCalendar` handles weekends and configured
session hours, not public holidays. `is_open()` can therefore return `True` on
an Indonesian holiday. It is a schedule hint; live data must still confirm the
market is trading. A holiday source arrives with the PHASE 2 IDX provider.

**IDX session hours are configuration, not verified fact.** Defaults follow the
regular market schedule with the shorter Friday session. Exchange hours change;
confirm them against the current IDX rulebook before relying on session labels.

**Crypto session bands are a simplification.** Real regional sessions overlap;
ARUNA needs one deterministic label per timestamp, so the bands are cut into
non-overlapping UTC ranges.

**Calendar horizons are approximations.** `1mo` = 30 days, `1y` = 365 days.
Adequate for scheduling; SPEC 23 outcome evaluation on IDX must additionally
respect the exchange calendar, which PHASE 7 has to implement.

**Most of the SPEC 42 schema does not exist yet.** Seven tables are created; the
rest belong to the phases that use them. See `docs/SCHEMA_ROADMAP.md`.

**Telegram rate limiting fails open.** With Redis down there is no counter. The
allowlist is the real access control; blocking an operator's `/kill` because a
cache is unavailable would be the worse failure.

**Timestamps serialise at millisecond precision.** Sub-millisecond detail is
dropped by the canonical ISO form. Sufficient for the kill switch and for the
SPEC 21 signal format; worth revisiting if PHASE 7 needs finer resolution for
sub-minute horizons.

**No authentication beyond the Telegram chat allowlist.** There is no HTTP API
and no web UI in this build, so there is nothing else to authenticate.

**Not verified on a VPS.** The architecture is modular and nothing is
Windows-specific, but this build has only been run on Windows 11 localhost.

**`root` with no password.** Correct for Laragon locally, and reported as a
phase notice. It becomes a `startup_warnings()` entry — degrading health — as
soon as `ARUNA_ENV` is anything but `development`.

## Next phase

PHASE 2: crypto and IDX market data providers. SPEC 45's precondition is met —
PHASE 1 is runnable and its full test suite passes against live infrastructure.
