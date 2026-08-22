# ARUNA AI

Market Research & Paper Trading Intelligence System.

Two markets: **CRYPTO** and **IDX** (Indonesian equities). Forex is not part of
this system.

> **Measured result: ARUNA's rules have no directional edge.** Backtested over
> its own recorded history — 580 published daily predictions across a year —
> direction accuracy is **50%**, a coin flip, consistent across every
> walk-forward fold. Two execution fixes have been measured since: a cost floor
> that cut the 1h loss by 46%, and an exit-at-target variant that made things
> **worse** and was rejected. See [docs/FINDINGS.md](docs/FINDINGS.md).
>
> This is not a system to trade. It is a system that can tell you it does not
> work, and show you the numbers.

**Build stage: PHASE 10 — all ten phases of the specification are built.**
ARUNA ingests live crypto and IDX prices, computes indicators, structure and
regime, classifies news, values IDX companies, measures correlation, runs the
SPEC 12 agent roster through all four SPEC 14 council rounds with veto and an
evidence-weighted judge, **locks predictions and scores them** — and now
dissects its own losses, measures its calibration, tracks which agents have
earned their weight, replays itself over history to find out whether any of it
works — and **refuses to change itself without a named human's approval**.

**The system now refuses to answer, out loud.** Calibration and agent
reliability are the two places most tempted to report a number they have not
earned. Both report `INSUFFICIENT_SAMPLE` until they have enough observations —
a confidence bucket needs 20 predictions, an agent needs 25 scored opinions —
and until then the judge keeps both SPEC 16 factors neutral and records them as
unavailable on every decision.

A locked prediction cannot be changed. Not its direction, confidence, entry,
target, reasoning or timestamps. That is enforced three ways at once: a frozen
dataclass, an append-only database trigger, and a SHA-256 fingerprint that makes
any edit detectable afterwards. A changed view creates a *new* signal that
supersedes the old one; the original stays exactly as written (SPEC 20).

Locking can decline. A verdict below the confidence floor, or one whose evidence
is older than the horizon it predicts over, is recorded but not published — and
the run says so rather than reporting a quiet market.

See [docs/PHASE_8_REPORT.md](docs/PHASE_8_REPORT.md) for exactly what is and is
not built.

**The agents are deterministic rule-based reasoners, not language models.**
That is deliberate: SPEC 29 requires confidence to be measurable against
realised accuracy and SPEC 39 requires decisions to replay identically, and
both need reproducible output. An LLM-backed agent can implement the same
protocol later.

**Data sources.** Crypto comes from **Binance spot**, **USDT pairs only**
(`BTC/USDT`, `ETH/USDT`, …); IDX from **Yahoo Finance**, which is unofficial and
**delayed by roughly 15 minutes**. Nothing in ARUNA describes the IDX feed as
realtime — every provider declares `is_realtime` and `expected_delay_sec`, and
the label follows the data all the way to Telegram.

There is exactly one crypto adapter and no fallback (PASAL 5). If Binance
cannot be reached, ARUNA reports `DATA SOURCE UNAVAILABLE` and stops rather
than quoting another venue's prices under the same symbol.

**Two things this README used to say, and why they changed.** It said Binance
was unreachable from Indonesian networks because Kominfo TrustPositif blocks
it. That was one measurement on one network; on 2026-08-17 `api.binance.com`,
`api1`, `api2` and `fapi.binance.com` each answered from the development
machine, and `aruna providers` reports whichever answer it actually gets rather
than assuming one. What did **not** change: Binance is not registered with
Bappebti. That is a legal fact rather than a network one, it travels in the
adapter's `regulatory_note` on every stored row, and SPEC 47 leaves the
consequence to the operator.

Read-only throughout, and by two mechanisms rather than one. The adapter's
transport checks a hardcoded allowlist of public market-data paths before it
opens a connection. Underneath that, ARUNA holds no Binance credential and
signs nothing: there is no API key or secret field in the configuration to fill
in — they were removed rather than left empty, because a named slot is an
invitation — so an authenticated order, account or transfer endpoint could not
be answered even if a path ever reached one (PASAL 41).

**Paper trading only.** Real order execution does not exist in this codebase,
and `ARUNA_REAL_TRADING_ENABLED=true` is rejected at startup.

---

## Requirements

| | |
|---|---|
| Python | 3.12 or newer |
| MySQL | 8.0.16 or newer (developed against Laragon's 8.4.3) |
| Redis | optional — ARUNA runs without it |
| OS | Windows (developed on Windows 11); no Windows-only APIs are used |

MySQL 8.0.16 is a hard floor, not a preference: earlier versions parse `CHECK`
constraints and then ignore them, so the schema would look correct while
enforcing nothing. `aruna doctor` refuses to pass on an older server.

## Setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev
```

That creates `.venv`, installs dependencies, and copies `.env.example` to
`.env`. With Laragon running, the MySQL defaults in `.env.example`
(`root`, no password, port 3306) work as-is.

Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env
```

## First run

```powershell
.\.venv\Scripts\python.exe -m aruna doctor
```

`doctor` checks every prerequisite — Python version, timezone data,
configuration, migration files, MySQL version and session timezone, Redis — and
reports all problems at once instead of stopping at the first.

Then:

```powershell
.\.venv\Scripts\python.exe -m aruna createdb
```

```powershell
.\.venv\Scripts\python.exe -m aruna migrate
```

```powershell
.\.venv\Scripts\python.exe -m aruna seed
```

```powershell
.\.venv\Scripts\python.exe -m aruna run
```

`run` blocks until Ctrl+C, then shuts down cleanly.

## Running it for real: one click, and it stays up

Once setup is done, everything above collapses into one file at the repo root:

**`ARUNA.bat`** — double-click it. It starts `aruna supervise`, which keeps two
processes alive: `aruna run` (Telegram bot, ingest, WebSocket stream, and the
upkeep loop that refreshes candles, pulls news, scans, resolves and locks) and
`aruna futures-loop` (perpetual analysis over REST). Closing the window or
pressing Ctrl+C stops the supervisor **and** its children.

The supervisor exists because a loop cannot report its own death. ARUNA's
internal loops already survive a failed tick, but nothing inside a process can
survive the process being killed — OOM, a database that is down at startup,
Windows shutting Python down. So the watchdog sits outside (PASAL 37): it
restarts what dies, with a delay that doubles from 2s to 120s, resets that delay
only after five minutes of genuine uptime, and logs `CRITICAL` after five
consecutive deaths — because a supervisor that silently restarts a thousand
times makes permanent damage look like uptime.

**`PASANG-AUTOSTART.bat`** — double-click **once** to register a Windows
scheduled task so ARUNA starts at every login, survives reboots, keeps running
on battery, and has no execution time limit (Windows kills scheduled tasks after
three days by default). `COPOT-AUTOSTART.bat` removes it. To see what would be
registered without registering it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\autostart.ps1 -WhatIf
```

**Only one ARUNA may run at a time.** With autostart on, there are two paths to
the same process — the scheduled task and a manual double-click. Two live ARUNAs
mean two Telegram bots polling the same update queue, which Telegram answers
with `409` while messages vanish alternately, plus doubled ingest. So `supervise`
takes an exclusive OS lock on `logs/aruna.lock` and the second one refuses to
start and says so. The lock is an OS lock, not a PID file: the kernel releases it
the moment the process dies, so a crash never leaves a stale lock that blocks the
next start.

**ARUNA analyses only.** The watchdog restarts whatever it is given, so what it
is given matters: two read-and-analyse processes. No order is ever sent, no
leverage changed, no funds moved (PASAL 41).

## Commands

| Command | What it does |
|---|---|
| `aruna version` | version and environment |
| `aruna config` | effective configuration, secrets redacted |
| `aruna doctor` | check every prerequisite and report what to fix |
| `aruna createdb` | create the database if missing |
| `aruna migrate` | apply pending migrations (`--status`, `--dry-run`) |
| `aruna seed` | load the tradable universe (`--file`) |
| `aruna providers` | what each data source offers, and probe it |
| `aruna fetch` | backfill candles and record a snapshot |
| `aruna analyze` | indicators, structure and regime from stored candles |
| `aruna news` | fetch and classify news from RSS feeds |
| `aruna fundamental` | IDX fundamentals and a SPEC 7 valuation verdict |
| `aruna correlate` | correlation matrix from stored candles |
| `aruna deliberate` | run the agent roster over stored evidence (round one) |
| `aruna council` | convene the full council: protest, veto, judge |
| `aruna signal` | lock council verdicts as predictions, and score due ones |
| `aruna autopsy` | learn from resolved predictions: losses, ghosts, calibration |
| `aruna backtest` | replay the decision path over stored history |
| `aruna replay` | re-run stored decisions and check they reproduce |
| `aruna research` | questions ARUNA raises from its own record, and drift |
| `aruna proposals` | model change proposals and the decisions on record |
| `aruna health` | one health sweep, printed as JSON |
| `aruna run` | start ARUNA (ingests continuously) |
| `aruna supervise` | keep ARUNA alive: start everything, restart what dies |

`seed` takes `--prune` to disable assets that have left the universe — disabled,
never deleted, so their history survives. `fetch` takes `--market`, `--symbols`,
`--intervals`, `--limit`, `--no-snapshot`.

`signal` locks across several horizons at once and does not reconcile them —
SPEC 10 allows 15m to say BUY while 1d says SELL, and the scope line reports
which horizons carry the call. `--resolve` scores predictions whose horizon has
elapsed before locking new ones; `--resolve-only` scores without locking.
Locking and scoring are separate calls on purpose: SPEC 21 requires the
prediction to be published before its outcome is known, and one method doing
both would make that impossible to inspect.

## Telegram

Optional. Without `ARUNA_TELEGRAM_BOT_TOKEN` ARUNA runs headless and the health
monitor reports Telegram as `DISABLED` — not as a fault.

With a token set, `ARUNA_TELEGRAM_CHAT_ID` is also required: **the allowlist
fails closed**, so a bot with no configured chat refuses every command rather
than obeying whoever finds it. Every command attempt, accepted or refused,
lands in `audit_logs`.

Available now: `/start`, `/help`, `/status`, `/health`, `/kill`, `/resume`,
the market data commands `/crypto`, `/stocks`, `/btc`, `/eth`, `/sol`,
`/bbca`, `/bbri`, `/bmri`, `/council` for the latest verdicts, `/signals`
and `/today` for locked predictions and their outcomes so far, and
`/performance`, `/weekly`, `/monthly` and `/autopsy` for the track record, and
`/research`, `/proposals`, `/approve` and `/reject` for model governance.

A test asserts no command stays unbound past the phase that delivers it — and
that none claims to work before it.

Every other command from the specification is registered and answers with the
phase it is waiting for. `/help` shows the full map.

`/kill` engages a durable kill switch — it is written to `app_state`, so it
survives a restart. `/resume` releases it.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Integration tests need MySQL. They create and drop a separate
`<ARUNA_DB_NAME>_test` schema, so your working database is never touched, and
they skip cleanly when no database is reachable.

Unit tests only:

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not integration"
```

Lint:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

## Configuring the universe

The MVP universe (5 crypto pairs, 11 IDX stocks) lives in
`src/aruna/seed/universe.py`. To change it without touching code, create
`config/universe.json`:

```json
[
  {
    "market": "IDX",
    "symbol": "UNVR",
    "display_name": "Unilever Indonesia Tbk",
    "asset_class": "IDX_EQUITY",
    "quote_asset": "IDR",
    "sector": "Consumer Non-Cyclicals",
    "lot_size": 100
  }
]
```

Then `aruna seed`. Re-seeding never re-enables an asset you disabled.

## Layout

```
aruna/
  core/       config, enums, clock, logging, redaction, kill switch
  db/         asyncmy pool, migration runner, repositories
  cache/      Redis client (optional, degrades quietly)
  health/     probes, aggregation, periodic monitor
  notify/     Telegram bot and command registry
  seed/       tradable universe
  app.py      lifecycle orchestration
  cli.py      command line
migrations/   forward-only SQL, checksum-guarded
docs/         schema roadmap, phase report
```

## Design decisions worth knowing

**Raw SQL, no ORM.** Later phases store immutable evidence and run analytical
queries over it; hand-written SQL keeps those queries auditable.

**Every connection is pinned to UTC.** MySQL `DATETIME` carries no timezone, and
`CURRENT_TIMESTAMP` is evaluated in the *session* zone — which defaults to
`SYSTEM`. ARUNA forces `time_zone='+00:00'` and strict `sql_mode` on every
connection, and the health monitor reports **DOWN** if the session is ever not
UTC. See §12 of the phase report for why this is the risk worth watching.

**Migrations are forward-only and checksum-guarded.** Editing an applied
migration stops the system from starting. A schema that silently disagrees with
the code reading it is the failure this prevents. They are *not* atomic — MySQL
commits DDL implicitly, so a failure mid-file leaves earlier statements applied.
The runner says exactly which statement failed and refuses to record the
migration.

**MySQL is fatal, Redis is not.** MySQL is the store of record. Redis holds
cached health snapshots and rate-limit counters; when it is down, reads return
`None`, writes are dropped, and the health monitor says so.

**Secrets are scrubbed twice** — by field name and by value — across ARUNA's own
logs *and* stdlib records from asyncmy, redis, and python-telegram-bot.

**`audit_logs` and `signal_snapshots` are append-only at the database level.**
An `UPDATE` or `DELETE` raises via a trigger. For signals this is SPEC 20: a
locked prediction is immutable, so the lifecycle (status, resolution) lives in a
separate `signals` table that can advance without the frozen record being
touched.

**Health separates "wrong now" from "not built yet".** Unconfigured PHASE 2
providers are reported as known gaps, not as degraded health — otherwise the
system would sit permanently yellow and the colour would stop meaning anything.

**Horizons disambiguate minutes from months.** The specification writes `1M` for
both one minute (crypto) and one month (IDX investment); ARUNA stores `1m` and
`1mo` so outcome records are never ambiguous.

**Data is never invented.** A provider that cannot answer produces
`DATA SOURCE UNAVAILABLE`, not a guess. Gaps in a candle series are reported,
never interpolated. An interval a provider does not publish is refused rather
than manufactured: Binance spot has no 10m, so
`aruna fetch --market CRYPTO --intervals 10m` answers
`! BTC/USDT 10m: not offered by binance-spot` and stores nothing. Binance spot
does publish 3m, so crypto 3m bars come from the venue.

**Resampling exists as machinery, and nothing calls it.** `aruna/data/resample.py`
can aggregate a whole number of small bars into a larger one, drops any bucket
missing a constituent bar, and stamps the result `source:resampled(<base>)` so a
derived candle can never be read as one the venue published. It has no caller
anywhere in `src/` — zero rows in `candles` carry a resampled source, and zero
rows carry `10m`. Said here because the previous version of this paragraph
promised the
opposite, and a horizon nobody can score is a smaller failure than a horizon
scored from bars ARUNA said it had built and had not. Nothing needs it today:
a 10m prediction samples the stored 1m series (`sampling_intervals(M10)` is
`(1m, 10m)`), and SPEC 3's 3-day and 5-day IDX horizons are *trading-day*
prediction windows evaluated over daily candles, not bar sizes — calendar
buckets spanning a weekend would hold fewer sessions than they claim.

**Indicators are evidence, not truth.** Every computation returns its value
*with* the sample size behind it, and a reading without enough data does not
vote in regime classification. `UNCERTAIN` is a real regime answer, returned
whenever the evidence is thin or two regimes are equally supported — reporting
the leader of a tie would manufacture certainty.

**No unsettled bar reaches an indicator.** `CandleSeries` refuses candles that
are still forming, so look-ahead leakage (SPEC 24) is blocked structurally
rather than by each indicator remembering.

**Derived judgements state how little they know.** News sentiment is a keyword
lexicon, not language understanding — it returns `UNKNOWN` rather than
`NEUTRAL` when nothing fired, and its confidence is capped at 0.75. A valuation
carries `is_recommendation=False`: SPEC 7 is explicit that undervalued is never
an automatic BUY.

**No agent has a permanent stance.** SPEC 12 and 48 forbid it, and the tests
run every agent across five market shapes — any that only ever reaches BUY, or
only ever SELL, fails. The prosecutor attacks the proposal and then reaches its
own conclusion, which is allowed to agree.

**A confidence becomes a probability only once it has been measured.** SPEC 29
compares stated confidence against realised accuracy in buckets, and a bucket
reports nothing until it holds 20 predictions. Below that it says
`INSUFFICIENT_SAMPLE` and returns `None` — not a number that happens to look
plottable. No agent and no council verdict may exceed 0.95 either: a process
that reads one market through one set of indicators has no certainty to report.

**Learning cannot be silent.** When the two SPEC 16 factors are neutral because
the sample is too small, every stored decision says so, the startup log says so,
and `aruna autopsy` says so. The failure this prevents is the one where PHASE 8
is wired in and every decision quietly claims both factors were applied while
the provider answers `None` to every question.

**An autopsy explains; it does not adjust.** Nothing in a loss post-mortem
touches a weight. Weights move only through SPEC 30 reliability, from 25+ scored
opinions, bounded to ×0.7–×1.2 — an agent is never silenced, because one that
cannot be heard can never be proven right later.

**The backtest cannot see the future, structurally.** `Window` owns the bars and
hands out only views clipped to an instant; nothing receives the full series,
and the leakage guard raises rather than warns. A leaked backtest produces a
number people act on, and it looks entirely plausible.

**The backtest runs the production decision path, not a copy of it.** No
analysis, no agents and no scoring of its own — otherwise it would measure a
different system than the one that trades. What history cannot supply (order
book, point-in-time news) is listed on every run rather than quietly filled in.

**The out-of-sample holdout is reserved now, before any tuning exists.** The
most recent 25%, not a random slice — market regimes are serially correlated, so
a random holdout leaks through its neighbours. A holdout created after tuning
begins is worthless.

**ARUNA cannot approve a change to itself.** Four layers refuse it: the approval
function requires a named actor, the validation bar refuses evidence it has
already called noise, a database CHECK constraint rejects `system` as an
approver, and the decision table is append-only so who approved what cannot be
rewritten. There is no configuration key that opens any of them.

**The bar rises with the number of variants tested.** Try twenty and one will
look good — the most common way a backtest lies to the person running it. The
count comes from storage, so a proposal cannot understate how many attempts
preceded it.

**A verdict is not a signal, and locking can refuse.** Reaching a conclusion and
publishing a prediction are separate acts. The lock declines a call below the
confidence floor, and one whose newest settled bar is older than the horizon
being predicted — a "1h call" built on six-hour-old evidence is not a one-hour
forecast. Declined calls are still stored, with the reason in their frozen
reasoning, because a record of only the published ones would flatter the system.

**Costs are modelled, and stated as assumptions.** Paper PnL is net of exchange
fees, the spread actually crossed, and slippage (SPEC 34). Those figures come
from published retail schedules, not from observed fills, and the summary reports
`cost_ratio` — how much of the gross a strategy hands back — because a backtest
that quietly omits costs is how a losing strategy looks profitable.

**The judge weighs evidence, never headcount.** SPEC 16 requires that a
minority can beat a majority, and a test proves it: three agents sharing one
under-sampled reading lose to one agent with strong independent evidence. Its
two history-dependent factors now have a source (PHASE 8) and are applied the
moment they can be measured — and declared `unavailable` on every stored
decision until then.

**A protest is not a veto.** Disagreement about direction, however strong, goes
to the judge. A veto is reserved for critical conditions, differs by market, and
is always reviewed — one whose stated condition is not actually present is
rejected, so a faulty probe cannot freeze the system.

## Documentation

- [docs/FINDINGS.md](docs/FINDINGS.md) — what has actually been measured, and
  what was done about it. Start here

- [docs/PHASE_10_REPORT.md](docs/PHASE_10_REPORT.md) — research questions,
  proposals, shadow models, drift, and the gate that stops ARUNA changing itself
- [docs/PHASE_9_REPORT.md](docs/PHASE_9_REPORT.md) — the backtest harness, the
  leakage guard, decision replay, and the measured finding that the rules lose
  money
- [docs/PHASE_8_REPORT.md](docs/PHASE_8_REPORT.md) — loss autopsy,
  counterfactuals, ghost signals, calibration, agent reliability, and why most
  of it currently refuses to report a number
- [docs/PHASE_7_REPORT.md](docs/PHASE_7_REPORT.md) — the prediction lock, paper
  trading costs, multi-horizon scope, the outcome engine, and what a locked
  prediction still cannot tell you
- [docs/PHASE_6_REPORT.md](docs/PHASE_6_REPORT.md) — council rounds, veto and
  review, the judge, and the two SPEC 16 factors that do not exist yet
- [docs/PHASE_5_REPORT.md](docs/PHASE_5_REPORT.md) — agents, prosecutor,
  self-critic, risk and no-trade, and what "AI agent" means here
- [docs/PHASE_4_REPORT.md](docs/PHASE_4_REPORT.md) — news, fundamentals,
  correlation, and the licensing of each source
- [docs/PHASE_3_REPORT.md](docs/PHASE_3_REPORT.md) — analysis: indicators,
  structure, regime, and what is deliberately not produced
- [docs/PHASE_2_REPORT.md](docs/PHASE_2_REPORT.md) — market data: sources,
  defects found, limitations
- [docs/PHASE_1_REPORT.md](docs/PHASE_1_REPORT.md) — infrastructure
- [docs/SCHEMA_ROADMAP.md](docs/SCHEMA_ROADMAP.md) — every SPEC 42 table mapped
  to the phase that creates it
