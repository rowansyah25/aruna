"""Command line entry point.

    python -m aruna <command>

``doctor`` is the one to reach for first: it checks every prerequisite without
requiring any of them to work, and prints what to fix.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from aruna import __version__
from aruna.app import ArunaApplication
from aruna.core.clock import isoformat, now_utc
from aruna.core.config import CURRENT_PHASE, ENV_FILE, PROJECT_ROOT, Settings, get_settings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import ArunaError, ConfigError
from aruna.core.logging import configure_logging
from aruna.db.migrator import Migrator, discover_migrations
from aruna.db.pool import Database, ensure_database_exists
from aruna.db.repositories.universe import UniverseRepository
from aruna.seed.universe import load_universe

if TYPE_CHECKING:
    from aruna.futures.binance import BinanceFuturesProvider

EXIT_OK = 0
EXIT_ERROR = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_settings() -> Settings:
    try:
        return get_settings()
    except ConfigError as exc:
        print(f"CONFIGURATION ERROR\n\n{exc}\n", file=sys.stderr)
        if ENV_FILE is None:
            print(
                "No .env file was found. Copy .env.example to .env and fill it in:\n"
                f"  copy {PROJECT_ROOT / '.env.example'} {PROJECT_ROOT / '.env'}\n",
                file=sys.stderr,
            )
        raise SystemExit(EXIT_ERROR) from exc


def _setup_cli_logging(settings: Settings, *, level: str | None = None) -> None:
    configure_logging(
        level=level or settings.log.level,
        fmt=settings.log.format,
        log_dir=settings.log.resolved_dir(),
        file_enabled=settings.log.file_enabled,
        secrets=settings.secrets(),
        instance=settings.app.instance_name,
        env=settings.app.env,
        phase=CURRENT_PHASE,
    )


def _rule(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 60 - len(title)))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"ARUNA AI {__version__} (PHASE {CURRENT_PHASE})")
    print(f"Python  {platform.python_version()} on {platform.system()} {platform.release()}")
    print(f"Root    {PROJECT_ROOT}")
    print(f"Env     {ENV_FILE or '(no .env - using process environment)'}")
    return EXIT_OK


def cmd_config(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    print(json.dumps(settings.describe(), indent=2))

    notices = settings.phase_notices()
    if notices:
        _rule(f"KNOWN GAPS AT PHASE {CURRENT_PHASE}")
        for notice in notices:
            print(f"  - {notice}")

    warnings = settings.startup_warnings()
    if warnings:
        _rule("WARNINGS")
        for warning in warnings:
            print(f"  ! {warning}")
    return EXIT_OK


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Preflight every prerequisite, reporting all failures rather than the first."""
    problems: list[str] = []
    print(f"ARUNA doctor - PHASE {CURRENT_PHASE}")

    _rule("PYTHON")
    version = sys.version_info
    ok = (version.major, version.minor) >= (3, 12)
    print(f"  {'OK  ' if ok else 'FAIL'} Python {platform.python_version()} (need >= 3.12)")
    if not ok:
        problems.append("Python 3.12 or newer is required")

    _rule("TIMEZONE DATA")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("Asia/Jakarta")
        print("  OK   Asia/Jakarta resolves")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL Asia/Jakarta unavailable: {exc}")
        problems.append("install tzdata:  pip install tzdata")

    _rule("CONFIGURATION")
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"  FAIL {exc}")
        problems.append("fix .env before continuing")
        _summarise(problems)
        return EXIT_ERROR

    print(f"  OK   env file: {ENV_FILE or '(process environment only)'}")
    print(f"  OK   markets: {', '.join(m.value for m in settings.app.enabled_markets)}")
    print("  OK   trading mode: PAPER (real trading disabled)")
    for notice in settings.phase_notices():
        print(f"  NOTE {notice}")
    for warning in settings.startup_warnings():
        print(f"  WARN {warning}")

    _rule("MIGRATION FILES")
    try:
        migrations = discover_migrations()
        print(f"  OK   {len(migrations)} migration file(s): "
              f"{', '.join(m.label for m in migrations)}")
    except ArunaError as exc:
        print(f"  FAIL {exc}")
        problems.append("migration files are unusable")

    _rule("MYSQL")
    db_ok = asyncio.run(_probe_database(settings))
    if not db_ok:
        problems.append(
            "MySQL is unreachable. Check ARUNA_DB_* in .env, then: aruna createdb"
        )

    _rule("REDIS")
    asyncio.run(_probe_redis(settings))

    _summarise(problems)
    return EXIT_OK if not problems else EXIT_ERROR


def _summarise(problems: list[str]) -> None:
    _rule("RESULT")
    if not problems:
        print("  All prerequisites satisfied.")
        print("  Next:  python -m aruna migrate  then  python -m aruna run")
        return
    print(f"  {len(problems)} problem(s) must be fixed:")
    for problem in problems:
        print(f"    - {problem}")


async def _probe_database(settings: Settings) -> bool:
    db = Database(settings.db)
    try:
        await db.connect()
    except ArunaError as exc:
        print(f"  FAIL {exc}")
        return False
    try:
        latency = await db.ping()
        version = await db.server_version()
        print(f"  OK   {settings.db.safe_dsn} ({latency:.1f} ms)")
        print(f"  OK   MySQL {version}")

        major, minor, patch = _parse_mysql_version(version)
        if (major, minor, patch) < (8, 0, 16):
            # Older servers parse CHECK constraints and then ignore them, so the
            # schema would look correct while enforcing nothing.
            print(
                f"  FAIL MySQL {version} does not enforce CHECK constraints "
                "(need 8.0.16+)"
            )
            return False

        timezone = await db.session_timezone()
        if timezone == "+00:00":
            print("  OK   session timezone pinned to +00:00 (UTC)")
        else:
            print(f"  FAIL session timezone is {timezone!r}, expected '+00:00'")
            return False

        status = await Migrator(db).status()
        if status.pending:
            print(
                f"  WARN {len(status.pending)} pending migration(s): "
                f"{', '.join(m.label for m in status.pending)}  -> run: aruna migrate"
            )
        else:
            print(f"  OK   schema at version {status.current_version}")
        return True
    except ArunaError as exc:
        print(f"  FAIL {exc}")
        return False
    finally:
        await db.close()


def _parse_mysql_version(version: str) -> tuple[int, int, int]:
    """Leading ``major.minor.patch`` of a MySQL version banner.

    Distribution builds append suffixes (``8.4.3-log``, ``8.0.35-0ubuntu``), so
    only the numeric prefix is parsed. Unparseable versions read as 0.0.0 and
    fail the minimum-version gate rather than passing it by accident.
    """
    match = re.match(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


async def _probe_redis(settings: Settings) -> None:
    from aruna.cache.redis_client import Cache

    if not settings.redis.enabled:
        print("  SKIP Redis disabled via ARUNA_REDIS_ENABLED=false")
        return
    cache = Cache(settings.redis)
    if not await cache.connect():
        print(f"  WARN {settings.redis.safe_url} unreachable - ARUNA runs without cache")
        return
    try:
        latency = await cache.ping()
        info = await cache.info()
        print(f"  OK   {settings.redis.safe_url} ({latency:.1f} ms), redis {info['version']}")
    finally:
        await cache.close()


def cmd_createdb(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    _setup_cli_logging(settings)
    try:
        created = asyncio.run(ensure_database_exists(settings.db))
    except ArunaError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if created:
        print(f"Created database {settings.db.name!r}.")
    else:
        print(f"Database {settings.db.name!r} already exists.")
    print("Next: python -m aruna migrate")
    return EXIT_OK


def cmd_migrate(args: argparse.Namespace) -> int:
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_migrate(settings, status_only=args.status, dry_run=args.dry_run))


async def _migrate(settings: Settings, *, status_only: bool, dry_run: bool) -> int:
    db = Database(settings.db)
    try:
        await db.connect()
    except ArunaError as exc:
        print(f"FAILED: {exc}\n\nTry: python -m aruna createdb", file=sys.stderr)
        return EXIT_ERROR

    migrator = Migrator(db)
    try:
        status = await migrator.status()
        print(f"Schema version: {status.current_version or '(none)'}")
        print(f"Applied: {len(status.applied)}   Pending: {len(status.pending)}")
        for record in status.applied:
            print(f"  [applied] {record.version}_{record.name}  ({record.execution_ms} ms)")
        for migration in status.pending:
            print(f"  [pending] {migration.label}")

        if status_only:
            return EXIT_OK
        if not status.pending:
            print("\nSchema is up to date.")
            return EXIT_OK

        applied = await migrator.upgrade(dry_run=dry_run)
        verb = "Would apply" if dry_run else "Applied"
        print(f"\n{verb} {len(applied)} migration(s).")
        if not dry_run:
            print("Next: python -m aruna seed")
        return EXIT_OK
    except ArunaError as exc:
        print(f"MIGRATION FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await db.close()


def cmd_seed(args: argparse.Namespace) -> int:
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(
        _seed(settings, Path(args.file) if args.file else None, prune=args.prune)
    )


async def _seed(settings: Settings, path: Path | None, *, prune: bool = False) -> int:
    specs = load_universe(path)
    enabled = set(settings.app.enabled_markets)
    # Seeding an asset for a market this instance does not run would create
    # rows nothing ever reads.
    selected = [spec for spec in specs if spec.market in enabled]
    skipped = len(specs) - len(selected)

    db = Database(settings.db)
    retired: dict[str, list[str]] = {}
    try:
        await db.connect()
        repo = UniverseRepository(db)
        count = await repo.upsert_many(selected)
        if prune:
            for market in enabled:
                keep = {s.symbol for s in selected if s.market is market}
                gone = await repo.disable_absent(market, keep)
                if gone:
                    retired[market.value] = gone
        counts = await repo.counts_by_market()
    except ArunaError as exc:
        print(f"SEED FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await db.close()

    print(f"Upserted {count} asset(s).")
    if skipped:
        print(f"Skipped {skipped} asset(s) for markets not enabled on this instance.")
    for market, symbols in retired.items():
        print(f"Disabled {len(symbols)} retired {market} asset(s): {', '.join(symbols)}")
        print("  (disabled, not deleted - their history stays intact)")
    for market, total in sorted(counts.items()):
        print(f"  {market}: {total} enabled")
    print("\nNote: tick size and price precision stay NULL until a PHASE 2 data")
    print("provider supplies them (SPEC 4 - no invented values).")
    return EXIT_OK


def cmd_providers(_args: argparse.Namespace) -> int:
    """What each configured data source can and cannot do."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_providers(settings))


async def _providers(settings: Settings) -> int:
    from aruna.data.registry import build_providers

    try:
        providers = build_providers(
            settings.providers, settings.data, settings.app.enabled_markets
        )
    except ArunaError as exc:
        print(f"PROVIDER CONFIG ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if not providers:
        print("No data providers configured.")
        print("STATUS: DATA SOURCE UNAVAILABLE for every enabled market.")
        return EXIT_ERROR

    problems = 0
    for market, provider in providers.items():
        caps = provider.capabilities
        _rule(f"{market.value} -> {caps.name}")
        print(f"  transport:   {caps.transport.value}")
        freshness = "REALTIME" if caps.is_realtime else f"DELAYED ~{caps.expected_delay_sec}s"
        print(f"  freshness:   {freshness}")
        print(f"  order book:  {'yes' if caps.supports_order_book else 'no'}")
        print(f"  intervals:   {', '.join(i.value for i in caps.supported_intervals)}")
        print(f"  regulatory:  {caps.regulatory_note}")
        if caps.limitations:
            print("  limitations:")
            for item in caps.limitations:
                print(f"    - {item}")

        await provider.open()
        try:
            status = await provider.status()
        finally:
            await provider.close()

        if status.reachable:
            print(f"  REACHABLE    {status.latency_ms:.0f} ms  {status.detail}")
        else:
            problems += 1
            print(f"  UNREACHABLE  {status.detail}")
            print("  STATUS: DATA SOURCE UNAVAILABLE")

    return EXIT_OK if problems == 0 else EXIT_ERROR


def cmd_fetch(args: argparse.Namespace) -> int:
    """Backfill candles and record one snapshot per asset."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_fetch(settings, args))


async def _fetch(settings: Settings, args: argparse.Namespace) -> int:

    app = ArunaApplication(settings)
    try:
        # One-shot: no background pollers competing with the explicit work.
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if app.ingest is None:
        print("No data providers are configured.", file=sys.stderr)
        print("STATUS: DATA SOURCE UNAVAILABLE", file=sys.stderr)
        await app.shutdown()
        return EXIT_ERROR

    try:
        markets = (
            (Market(args.market.upper()),) if args.market else app.ingest.markets
        )
        intervals = _parse_intervals(args.intervals) if args.intervals else None
        symbols = tuple(s.strip() for s in args.symbols.split(",")) if args.symbols else None
        failures = 0

        for market in markets:
            ingestor = app.ingest.ingestor(market)
            if ingestor is None:
                print(f"\n{market.value}: DATA SOURCE UNAVAILABLE (no provider)")
                failures += 1
                continue

            supported = ingestor.provider.capabilities.supported_intervals
            wanted = intervals or supported
            _rule(f"{market.value} via {ingestor.provider.name}")

            result = await ingestor.backfill(wanted, limit=args.limit, symbols=symbols)
            print(f"  candles stored: {result.candles}")
            if result.rejected:
                print(f"  quality-rejected: {result.rejected}")
            for problem in result.failures:
                print(f"  ! {problem}")
            failures += len(result.failures)

            if not args.no_snapshot:
                poll = await ingestor.poll_once()
                print(f"  snapshots: {poll.snapshots}")
                for problem in poll.failures:
                    print(f"  ! {problem}")
                if poll.quality_counts:
                    print(f"  quality: {poll.quality_counts}")

        _rule("COVERAGE")
        assert app.market_data is not None
        for row in await app.market_data.coverage():
            newest = row["newest"].strftime("%Y-%m-%d %H:%M") if row["newest"] else "-"
            print(
                f"  {row['market_code']:6} {row['symbol']:10} {row['interval_code']:4} "
                f"{row['bars']:6} bars  newest {newest}Z"
            )
        return EXIT_OK if failures == 0 else EXIT_ERROR
    except ArunaError as exc:
        print(f"FETCH FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def _parse_intervals(raw: str) -> tuple[Horizon, ...]:
    out: list[Horizon] = []
    for token in raw.split(","):
        value = token.strip().lower()
        if not value:
            continue
        try:
            out.append(Horizon(value))
        except ValueError:
            raise SystemExit(
                f"unknown interval {token!r}; valid: "
                + ", ".join(h.value for h in Horizon)
            ) from None
    return tuple(out)


def cmd_analyze(args: argparse.Namespace) -> int:
    """Compute technical, structure and regime evidence from stored candles."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_analyze(settings, args))


async def _analyze(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.analysis is not None and app.analysis_store is not None
    try:
        markets = (
            (Market(args.market.upper()),) if args.market else settings.app.enabled_markets
        )
        intervals = (
            _parse_intervals(args.intervals) if args.intervals else (Horizon.H1, Horizon.D1)
        )
        symbols = tuple(s.strip() for s in args.symbols.split(",")) if args.symbols else None
        failures = 0

        for market in markets:
            _rule(f"{market.value}  intervals: {', '.join(i.value for i in intervals)}")
            result = await app.analysis.analyse_market(
                market, intervals, symbols=symbols, persist=not args.dry_run
            )

            for snapshot in result.snapshots:
                print(f"  {snapshot.summary_line()}")
                if args.verbose:
                    for reason in snapshot.regime.reasons:
                        print(f"      - {reason}")
                    for note in snapshot.notes:
                        print(f"      note: {note}")

            print(f"\n  {result.summary()}")
            if result.regimes:
                spread = ", ".join(
                    f"{name}={count}" for name, count in sorted(result.regimes.items())
                )
                print(f"  regimes: {spread}")
            for problem in result.failures:
                print(f"  ! {problem}")
            failures += len(result.failures)

        if not args.dry_run:
            _rule("STORED")
            for row in await app.analysis_store.coverage():
                newest = row["newest"].strftime("%Y-%m-%d %H:%M") if row["newest"] else "-"
                print(
                    f"  {row['market_code']:6} {row['symbol']:10} "
                    f"{row['interval_code']:4} {row['snapshots']:5} snapshots  "
                    f"newest {newest}Z"
                )
        return EXIT_OK if failures == 0 else EXIT_ERROR
    except ArunaError as exc:
        print(f"ANALYSIS FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_news(args: argparse.Namespace) -> int:
    """Fetch and classify news from the configured RSS feeds."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_news(settings, args))


async def _news(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.news.rss import UNAVAILABLE_FEEDS

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.news is not None and app.news_store is not None
    try:
        await app.news.open()
        result = await app.news.ingest()

        _rule("FETCHED")
        for source, count in sorted(result.by_source.items()):
            print(f"  {source:18} {count:4} items")
        for name, reason in UNAVAILABLE_FEEDS:
            print(f"  {name:18}    - DATA SOURCE UNAVAILABLE: {reason}")

        print(f"\n  {result.summary()}")
        for problem in result.failures:
            print(f"  ! {problem}")

        _rule("LATEST")
        for row in await app.news_store.recent(limit=args.limit):
            when = row["published_at"].strftime("%m-%d %H:%M") if row["published_at"] else "  ?  "
            print(
                f"  {when}  {row['importance']:8} {row['sentiment']:8} "
                f"{row['category']:18} {row['title'][:64]}"
            )
            print(f"           {row['source']}  {row['url'][:88]}")

        _rule("SENTIMENT (7 days)")
        from datetime import timedelta

        since = now_utc() - timedelta(days=7)
        breakdown = await app.news_store.sentiment_breakdown(since=since)
        print(f"  {breakdown or 'no items in window'}")
        print(
            "\n  Sentiment is keyword-derived, not language understanding.\n"
            "  UNKNOWN means the lexicon found nothing to go on - not 'neutral'."
        )
        return EXIT_OK if not result.failures else EXIT_ERROR
    except ArunaError as exc:
        print(f"NEWS FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_fundamental(args: argparse.Namespace) -> int:
    """Fetch IDX fundamentals and produce a SPEC 7 valuation verdict."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_fundamental(settings, args))


async def _fundamental(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.fundamental is not None and app.fundamental_store is not None
    try:
        symbols = tuple(s.strip() for s in args.symbols.split(",")) if args.symbols else None
        result = await app.fundamental.ingest(symbols=symbols)

        _rule("VALUATION")
        for row in await app.fundamental_store.coverage():
            verdict = row["verdict"] or "-"
            confidence = row["verdict_confidence"]
            confidence_text = f"{float(confidence):.2f}" if confidence is not None else "-"
            print(
                f"  {row['symbol']:8} {verdict:12} conf={confidence_text:5} "
                f"coverage={float(row['coverage']) * 100:5.1f}%"
            )

        print(f"\n  {result.summary()}")
        if result.verdicts:
            print(f"  verdicts: {result.verdicts}")
        for problem in result.failures:
            print(f"  ! {problem}")

        print(
            "\n  SPEC 7: a valuation is not a recommendation.\n"
            "  UNDERVALUED is never an automatic BUY - there are good reasons\n"
            "  a company is cheap, and this engine cannot tell which applies."
        )
        return EXIT_OK if not result.failures else EXIT_ERROR
    except ArunaError as exc:
        print(f"FUNDAMENTAL FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_correlate(args: argparse.Namespace) -> int:
    """Correlation matrix across an enabled market, from stored candles."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_correlate(settings, args))


async def _correlate(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.analysis.correlation import build_matrix, concentration_warning
    from aruna.analysis.series import CandleSeries, InsufficientData

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.universe is not None and app.market_data is not None
    assert app.correlation_store is not None
    try:
        interval = _parse_intervals(args.interval)[0] if args.interval else Horizon.H1
        markets = (
            (Market(args.market.upper()),) if args.market else settings.app.enabled_markets
        )

        for market in markets:
            _rule(f"{market.value} correlation ({interval.value})")
            series_by_symbol = {}
            for asset in await app.universe.assets(market=market, enabled_only=True):
                rows = await app.market_data.candles(asset.id, interval, limit=args.limit)
                if len(rows) < 25:
                    continue
                try:
                    series_by_symbol[asset.symbol] = CandleSeries.from_rows(
                        rows, market=market, symbol=asset.symbol, interval=interval
                    )
                except InsufficientData:
                    continue

            if len(series_by_symbol) < 2:
                print("  need at least two assets with stored candles")
                print("  Run: python -m aruna fetch")
                continue

            matrix = build_matrix(
                series_by_symbol, interval=interval.value, computed_at=now_utc()
            )
            for pair in sorted(matrix.pairs, key=lambda p: -abs(p.coefficient)):
                flag = "" if pair.reliable else "  (thin sample)"
                print(
                    f"  {pair.left:9} {pair.right:9} r={pair.coefficient:+.3f}  "
                    f"{pair.strength:8} n={pair.overlap}{flag}"
                )

            average = matrix.average_absolute()
            if average is not None:
                print(f"\n  average |r|: {average:.3f}")
            warning = concentration_warning(matrix)
            if warning:
                print(f"  ! {warning}")
            for note in matrix.skipped:
                print(f"  - {note}")

            if not args.dry_run:
                stored = await app.correlation_store.save(matrix, market=market)
                print(f"  stored {stored} pair(s)")

        print(
            "\n  Computed on returns, not raw prices: two assets that both drift\n"
            "  upward correlate on price while saying nothing about co-movement."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"CORRELATION FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_deliberate(args: argparse.Namespace) -> int:
    """Run the SPEC 12 agent roster over stored evidence (PHASE 5, round one)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_deliberate(settings, args))


async def _deliberate(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.deliberation is not None
    try:
        interval = _parse_intervals(args.interval)[0] if args.interval else Horizon.H1
        markets = (
            (Market(args.market.upper()),) if args.market else settings.app.enabled_markets
        )
        symbols = tuple(s.strip() for s in args.symbols.split(",")) if args.symbols else None
        failures = 0

        for market in markets:
            _rule(f"{market.value} deliberation ({interval.value})")
            result = await app.deliberation.run(
                market,
                interval,
                symbols=symbols,
                trading_allowed=app.state.trading_allowed,
                persist=not args.dry_run,
            )

            for record in result.results:
                print(f"  {record.summary()}")
                if args.verbose:
                    for opinion in record.opinions:
                        marker = "  (abstained)" if opinion.abstained else ""
                        print(f"      {opinion.summary()}{marker}")
                        if opinion.reasoning and not opinion.abstained:
                            print(f"          {opinion.reasoning[0]}")
                    print(f"      PROPOSAL   {record.proposal.summary()}")
                    print(f"      PROSECUTOR {record.prosecutor.summary()}")
                    for reason in record.prosecutor.reasoning[:3]:
                        print(f"          {reason}")
                    if record.critique.reassessment_required:
                        print("      SELF-CRITIC forced a reassessment:")
                        for reason in record.critique.opinion.reasoning[:3]:
                            print(f"          {reason}")
                    for detail in record.no_trade.details[:4]:
                        print(f"      BLOCKED: {detail}")
                    for note in record.notes:
                        print(f"      note: {note}")

            print(f"\n  {result.summary()}")
            if result.outcomes:
                print(f"  outcomes: {result.outcomes}")
            for problem in result.failures:
                print(f"  ! {problem}")
            failures += len(result.failures)

        print(
            "\n  This is round one only: independent opinions, prosecutor,\n"
            "  self-critic, risk and no-trade gates. Cross-protest, rebuttal,\n"
            "  veto and the evidence-weighted judge do not run here, so none of\n"
            "  this is a council decision or a signal. Use `aruna council` for\n"
            "  the full sequence, `aruna signal` to lock a prediction."
        )
        return EXIT_OK if failures == 0 else EXIT_ERROR
    except ArunaError as exc:
        print(f"DELIBERATION FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_council(args: argparse.Namespace) -> int:
    """Convene the full SPEC 14 council (PHASE 6)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_council(settings, args))


async def _council(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.council is not None
    try:
        interval = _parse_intervals(args.interval)[0] if args.interval else Horizon.H1
        markets = (
            (Market(args.market.upper()),) if args.market else settings.app.enabled_markets
        )
        symbols = tuple(s.strip() for s in args.symbols.split(",")) if args.symbols else None
        failures = 0

        for market in markets:
            _rule(f"{market.value} council ({interval.value})")
            result = await app.council.run(
                market,
                interval,
                symbols=symbols,
                trading_allowed=app.state.trading_allowed,
                persist=not args.dry_run,
            )

            for verdict in result.verdicts:
                print(f"  {verdict.summary()}")
                if args.verbose:
                    for opinion in verdict.opinions:
                        if not opinion.abstained:
                            print(f"      {opinion.summary()}")
                    print(
                        f"      ROUNDS: "
                        f"{', '.join(r.value for r in verdict.rounds_run)}"
                    )
                    for objection in verdict.protest.objections[:5]:
                        print(
                            f"      OBJECT {objection.accuser.value} -> "
                            f"{objection.target.value} [{objection.ground}]"
                        )
                    for rebuttal in verdict.protest.rebuttals:
                        if rebuttal.conceded:
                            print(
                                f"      CONCEDED {rebuttal.target.value}: "
                                f"{rebuttal.detail}"
                            )
                    for note in verdict.protest.adversarial_review[:3]:
                        print(f"      REVIEW: {note}")
                    for review in verdict.veto.reviews:
                        print(
                            f"      VETO {review.veto.reason.value} -> "
                            f"{review.outcome.value}: {review.rationale}"
                        )
                    print(
                        f"      JUDGE {verdict.judgement.decision.value} "
                        f"buy={verdict.judgement.buy_weight:.3f} "
                        f"sell={verdict.judgement.sell_weight:.3f}"
                    )
                    for reason in verdict.judgement.reasoning[:4]:
                        print(f"          {reason}")
                    for note in verdict.notes:
                        print(f"      note: {note}")

            print(f"\n  {result.summary()}")
            if result.decisions:
                print(f"  decisions: {result.decisions}")
            for problem in result.failures:
                print(f"  ! {problem}")
            failures += len(result.failures)

        print(
            "\n  A council verdict is not a signal: it carries no entry, no\n"
            "  target and no immutable snapshot. Locking is a separate act and\n"
            "  it can decline - run `aruna signal` to see what it publishes.\n"
            "  The judge weighs evidence, never headcount. Whether historical\n"
            "  reliability and calibration were applied is recorded on each\n"
            "  stored decision; `aruna autopsy` reports what has been measured."
        )
        return EXIT_OK if failures == 0 else EXIT_ERROR
    except ArunaError as exc:
        print(f"COUNCIL FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def cmd_history(args: argparse.Namespace) -> int:
    """Compare performance across time windows (PASAL 11.20)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_history(settings, args))


async def _history(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.db.repositories.learning import LearningRepository
    from aruna.learning.windows import (
        MIN_SHIFT,
        WINDOWS,
        build_window,
        shifts,
    )

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    repo = LearningRepository(app.db)
    dimensions = (
        [args.dimension] if args.dimension
        else ["asset", "timeframe", "regime", "direction", "quality"]
    )

    try:
        ada_terukur = False
        for dimension in dimensions:
            _rule(f"{dimension} across windows")
            laporan = {}
            for window, days in WINDOWS:
                rows = await repo.window_rows(dimension, days=days)
                laporan[window] = build_window(
                    rows, dimension=dimension, window=window
                )

            semua = laporan["all"]
            if not semua.cells:
                print("  Nothing has resolved yet.")
                continue

            # Satu baris per nilai, satu kolom per jendela. Jendela yang
            # bersarang paling mudah dibaca berdampingan, karena di situlah
            # terlihat bahwa "hari ini" adalah bagian kecil dari "semua" dan
            # bukan pengukuran yang menyaingi.
            print(f"      {'key':<16} {'today':>10} {'7d':>10} "
                  f"{'30d':>10} {'all':>10}")
            for cell in sorted(semua.cells, key=lambda c: -c.decided)[:10]:
                kolom = []
                for window, _ in WINDOWS:
                    sel = laporan[window].get(cell.key)
                    if sel is None or sel.win_rate is None:
                        n = 0 if sel is None else sel.decided
                        kolom.append(f"n={n}")
                    else:
                        kolom.append(f"{sel.win_rate * 100:.0f}% ({sel.decided})")
                        ada_terukur = True
                print(f"      {cell.key:<16} " + " ".join(f"{c:>10}" for c in kolom))

            terbaik, terburuk = semua.best(), semua.worst()
            if terbaik is not None:
                print(f"      best:  {terbaik.key} "
                      f"({terbaik.win_rate * 100:.0f}% of {terbaik.decided})")
            if terburuk is not None and terburuk.key != (
                terbaik.key if terbaik else None
            ):
                print(f"      worst: {terburuk.key} "
                      f"({terburuk.win_rate * 100:.0f}% of {terburuk.decided})")

            bergeser = shifts(laporan["30d"], semua)
            for shift in bergeser[:3]:
                print(f"      shift: {shift.summary()}")

        if not ada_terukur:
            print(
                f"\n  No cell has reached the sample floor in any window, so no\n"
                f"  win rate is shown anywhere above. The counts are the whole\n"
                f"  of what is known. Shifts need {MIN_SHIFT:.0%} between two\n"
                "  windows that BOTH have enough - noise compared with noise\n"
                "  is noisier still."
            )
        return EXIT_OK
    finally:
        await app.shutdown()


async def _print_agent_breakdown(app: ArunaApplication) -> None:
    """Keandalan agen per rezim, timeframe, dan aset (PASAL 11.2).

    Dicetak di sini karena di sinilah pembacanya sudah berada: ``aruna
    autopsy`` adalah perintah yang dijalankan orang ketika bertanya "apa yang
    sebenarnya terjadi". Sebuah rincian yang dibangun dan tidak dipanggil dari
    mana pun adalah kode yang tidak pernah salah karena tidak pernah jalan.

    Sel yang belum cukup sampel tetap ditampilkan - dengan berapa lagi yang
    dibutuhkan, bukan dengan angka akurasinya. Yang dihilangkan dari layar
    terbaca sebagai "tidak ada masalah di sana".
    """
    from aruna.db.repositories.learning import LearningRepository
    from aruna.learning.breakdown import MIN_CELL_SAMPLE, build_breakdown

    repo = LearningRepository(app.db)
    ada_yang_terukur = False

    for dimension in ("regime", "timeframe", "asset"):
        try:
            rows = await repo.agent_breakdown(dimension)
        except ArunaError as exc:
            print(f"\n  {dimension.upper()}: tidak bisa dibaca ({exc})")
            continue

        report = build_breakdown(rows, dimension=dimension)
        if not report.cells:
            continue

        print(f"\n  AGENT PER {dimension.upper()} (PASAL 11.2)")
        for cell in sorted(report.cells, key=lambda c: -c.votes)[:10]:
            if cell.accuracy is None:
                shown = f"needs {cell.needs} more"
            else:
                shown = f"{cell.accuracy * 100:.0f}%"
                ada_yang_terukur = True
            print(
                f"      {cell.agent:<12} {cell.key:<14} "
                f"n={cell.votes:<4} {shown}"
            )
        best = report.best()
        if best is not None:
            print(f"      best: {best.agent} @ {best.key}")

    if not ada_yang_terukur:
        print(
            f"\n  No cell has reached {MIN_CELL_SAMPLE} observations yet, so no\n"
            "  per-regime, per-timeframe or per-asset accuracy is shown. The\n"
            "  raw counts above are the whole of what is known."
        )


def cmd_autopsy(args: argparse.Namespace) -> int:
    """Learn from resolved predictions (PHASE 8)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_autopsy(settings, args))


async def _autopsy(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.learning is not None
    try:
        _rule("reviewing resolved predictions")
        result = await app.learning.review(
            limit=args.limit, persist=not args.dry_run
        )

        if not result.reviewed:
            print(
                "  Nothing has resolved yet. Every figure below would be\n"
                "  computed from zero observations, so none is shown.\n"
                "  Run:  python -m aruna signal --resolve"
            )
            return EXIT_OK

        for autopsy in result.autopsies:
            print(f"\n  LOSS: {autopsy.summary()}")
            for finding in autopsy.findings:
                print(f"      - {finding}")
            if args.verbose:
                for agent, weight in autopsy.backers:
                    print(f"      backed by {agent} at weight {weight:.3f}")
                for objection in autopsy.unanswered_objections[:3]:
                    print(f"      unanswered: {objection}")

        for ghost in result.ghosts:
            print(f"\n  GHOST: {ghost.summary()}")
            if args.verbose:
                for reason in ghost.reasoning[:3]:
                    print(f"      we waited because: {reason}")

        if args.verbose:
            for item in result.counterfactuals:
                print(f"  {item.summary()}")

        vindicated = [o for o in result.objections if o.vindicated]
        if vindicated:
            print("\n  OBJECTIONS OVERRULED THAT TURNED OUT RIGHT (SPEC 26)")
            for record in vindicated[:8]:
                print(
                    f"      {record.accuser} [{record.ground}]: "
                    f"{record.vindicated}/{record.raised}"
                )

        calibration = result.calibration
        if calibration is not None:
            print("\n  CALIBRATION (SPEC 29)")
            print(f"      {calibration.verdict}")
            for bucket in calibration.buckets:
                accuracy = bucket.accuracy
                shown = (
                    f"{accuracy * 100:.0f}%"
                    if accuracy is not None
                    else f"needs {bucket.to_dict()['needs']} more"
                )
                print(
                    f"      {bucket.label:<10} n={bucket.predictions:<4} {shown}"
                )

        reliability = result.reliability
        if reliability is not None and reliability.records:
            print("\n  AGENT RELIABILITY (SPEC 30)")
            for record in reliability.records:
                accuracy = record.accuracy
                shown = (
                    f"{accuracy * 100:.0f}%  x{record.multiplier}"
                    if accuracy is not None
                    else "INSUFFICIENT SAMPLE"
                )
                print(f"      {record.role.value:<12} n={record.scored:<4} {shown}")

        await _print_agent_breakdown(app)

        print(f"\n  {result.summary()}")
        for problem in result.failures:
            print(f"  ! {problem}")

        print(
            "\n  An autopsy explains one loss; it changes no weight. Weights\n"
            "  move only through SPEC 30 reliability, and only once an agent\n"
            "  has enough scored opinions. Until then every SPEC 16 factor\n"
            "  stays neutral and is recorded as unavailable."
        )
        return EXIT_OK if not result.failures else EXIT_ERROR
    except ArunaError as exc:
        print(f"REVIEW FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_backtest(args: argparse.Namespace) -> int:
    """Replay the decision path over stored history (PHASE 9)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_backtest(settings, args))


async def _backtest(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.backtest is not None
    try:
        interval = _parse_intervals(args.interval)[0] if args.interval else Horizon.H1
        markets = (
            (Market(args.market.upper()),) if args.market else settings.app.enabled_markets
        )
        symbols = tuple(s.strip() for s in args.symbols.split(",")) if args.symbols else None
        failures = 0

        for market in markets:
            _rule(f"{market.value} backtest ({interval.value})")
            run = await app.backtest.run(
                market,
                interval,
                symbols=symbols,
                every=args.every,
                folds=args.folds,
                include_holdout=args.include_holdout,
                exit_at_target=args.exit_at_target,
                stop_loss=args.stop_loss,
            )

            for result in run.results:
                print(f"  {result.symbol:<10} {result.summary()}")
                if args.verbose and result.withheld:
                    for reason, count in sorted(result.withheld.items()):
                        print(f"      withheld {reason}: {count}")

            combined = run.to_dict()["combined"]
            print(f"\n  decisions simulated: {combined['decisions_simulated']}")
            print(f"  published:           {combined['published']}")
            print(f"  resolved:            {combined['resolved']}")
            accuracy = combined["direction_accuracy"]
            print(
                f"  direction accuracy:  "
                f"{f'{accuracy * 100:.0f}%' if accuracy is not None else 'n/a'}"
            )
            trades = combined["paper_trades"]
            if trades.get("trades"):
                print(f"  net PnL:             {trades['net_pnl']}")
                print(
                    f"  cost ratio:          {trades.get('cost_ratio')} "
                    "of gross eaten by costs"
                )

            if run.walk_forward is not None:
                print(f"\n  WALK-FORWARD: {run.walk_forward.verdict}")
                for fold in run.walk_forward.results:
                    shown = (
                        f"{fold.accuracy * 100:.0f}%"
                        if fold.accuracy is not None
                        else "INSUFFICIENT SAMPLE"
                    )
                    print(
                        f"      {fold.fold.label}  published={fold.published:<4} "
                        f"resolved={fold.resolved:<4} {shown}"
                    )
                if run.walk_forward.holdout is None and run.split is not None:
                    print(
                        f"      holdout {run.split.holdout_start:%Y-%m-%d} onward "
                        "reserved and not evaluated (SPEC 38)"
                    )

            # **Disimpan, bukan cuma dicetak.** Sebelum baris ini,
            # `BacktestService` menghitung fold walk-forward dan holdout dengan
            # lengkap, mencetaknya, lalu membuangnya - dan `backtest_runs`
            # berisi nol baris sepanjang umur sistem. Akibatnya WALK_FORWARD
            # dan OUT_OF_SAMPLE (PASAL 14.40) dilaporkan hilang pada tiap
            # keputusan, bukan karena validasinya tidak bisa dijalankan
            # melainkan karena hasilnya tidak pernah bertahan.
            if app.backtest_store is not None:
                await app.backtest_store.record_backtest(run)

            for problem in run.failures:
                print(f"  ! {problem}")
            failures += len(run.failures)

            print("\n  WHAT THIS BACKTEST CANNOT REPRODUCE")
            for caveat in combined["known_optimism"]:
                print(f"      - {caveat}")

        print(
            "\n  Every caveat above pushes results in the flattering direction.\n"
            "  A backtest measures rules against recorded history; it is not a\n"
            "  forecast, and ARUNA fits no parameters, so walk-forward here\n"
            "  measures consistency across periods, not resistance to\n"
            "  curve-fitting."
        )
        return EXIT_OK if failures == 0 else EXIT_ERROR
    except ArunaError as exc:
        print(f"BACKTEST FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_replay(args: argparse.Namespace) -> int:
    """Re-run stored decisions from their recorded inputs (SPEC 39)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_replay(settings, args))


async def _replay(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.backtest is not None and app.backtest_store is not None
    try:
        _rule("replaying stored decisions")
        results = await app.backtest.replay(limit=args.limit)
        if not results:
            print(
                "  No stored decision carries the council session it came from,\n"
                "  so none can be replayed. Run:  python -m aruna signal"
            )
            return EXIT_OK

        for result in results:
            print(f"  {result.summary()}")
            if not args.dry_run:
                await app.backtest_store.record_replay(result)

        summary = app.backtest.summarise_replays(results)
        print(f"\n  {summary['verdict']}")
        print(
            f"  reproduced {summary['reproduced']}/{summary['replayable']}"
            f"  not replayable: {summary['not_replayable']}"
        )
        if summary["diverging_fields"]:
            print("  diverging fields:")
            for field, count in summary["diverging_fields"].items():
                print(f"      {field}: {count}")

        print(
            "\n  A decision that cannot be reproduced from its stored inputs\n"
            "  cannot be audited, and every explanation attached to it is\n"
            "  unverifiable."
        )
        return EXIT_OK if summary["diverged"] == 0 else EXIT_ERROR
    except ArunaError as exc:
        print(f"REPLAY FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_research(args: argparse.Namespace) -> int:
    """Questions ARUNA raises about itself, and drift (PHASE 10)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_research(settings, args))


async def _research(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.governance is not None
    try:
        _rule("questions from the record")
        result = await app.governance.research(persist=not args.dry_run)

        if not result.questions:
            print(
                "  Nothing in the record raises a question yet. That is usually\n"
                "  a shortage of resolved predictions, not a clean bill of health."
            )
        for question in result.questions:
            print(f"\n  [{question.severity:.2f}] {question.question}")
            for item in question.evidence:
                print(f"      - {item}")

        drift = await app.governance.check_drift(persist=not args.dry_run)
        print(f"\n  DRIFT: {drift.verdict}")

        print(
            "\n  These are questions, not findings. Nothing here changes how\n"
            "  ARUNA decides: a change needs a written proposal, a validated\n"
            "  comparison, and a named human's approval (SPEC 44)."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"RESEARCH FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_proposals(args: argparse.Namespace) -> int:
    """List model change proposals, or decide one (PHASE 10)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_proposals(settings, args))


async def _proposals(settings: Settings, args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    assert app.governance_store is not None
    try:
        rows = await app.governance_store.proposals(limit=args.limit)
        if not rows:
            print(
                "  No model change has been proposed.\n\n"
                "  Proposals are written by a person against a research\n"
                "  question; ARUNA does not author changes to itself."
            )
            return EXIT_OK

        _rule("model change proposals")
        for row in rows:
            print(f"\n  {row['proposal_key']}  [{row['status']}]")
            print(f"      {row['title']}")
            print(f"      hypothesis: {row['hypothesis']}")
            validation = row.get("validation")
            if validation:
                print(f"      verdict:    {validation['verdict']}")
                for reason in validation.get("reasons", []):
                    print(f"          {reason}")
            else:
                print("      verdict:    UNVALIDATED - no comparison recorded")

        decisions = await app.governance_store.decisions(limit=5)
        if decisions:
            print("\n  DECISIONS ON RECORD")
            for row in decisions:
                print(
                    f"      {row['decided_at']:%Y-%m-%d} {row['decision']:<9} "
                    f"{row['proposal_key']} by {row['decided_by']}"
                )

        print(
            "\n  A proposal becomes active only when a named person approves\n"
            "  it. There is no threshold, score or configuration that can\n"
            "  approve one on ARUNA's behalf (SPEC 44)."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"PROPOSALS FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_futures(args: argparse.Namespace) -> int:
    """Pull one live perpetual snapshot and read it (FUTURES SPEC F1-F4).

    Needs no database and no credentials: it reads public market data and
    prints what it found. It does **not** produce a signal - the futures
    council is F5 and does not exist yet.
    """
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_futures(args))


async def _futures(args: argparse.Namespace) -> int:
    from aruna.futures import (
        analyse_funding,
        analyse_open_interest,
        assess_liquidity,
        check_integrity,
    )
    from aruna.futures.binance import BinanceFuturesProvider

    symbol = args.symbol.upper()
    notional = Decimal(str(args.notional))
    provider = BinanceFuturesProvider()
    await provider.open()
    try:
        status = await provider.probe()
        _rule(f"{provider.name} -> {symbol}")
        if not status.reachable:
            print(f"  UNREACHABLE  {status.detail}")
            print("\nSTATUS: DATA SOURCE UNAVAILABLE")
            print(
                "  Nothing is substituted and no cached figure is presented as\n"
                "  live. Reachability is network-dependent (FUTURES SPEC 5)."
            )
            return EXIT_ERROR
        print(f"  REACHABLE    {status.latency_ms:.0f} ms  {status.detail}")

        snapshot = await provider.snapshot(symbol)

        # SPEC 46 first: an incoherent set of inputs may not be read at all.
        report = check_integrity(snapshot)
        _rule("data integrity (FUTURES SPEC 46)")
        print(f"  verdict:     {report.verdict.value}")
        for name, age in sorted(report.ages.items()):
            print(f"    {name:<16} {age:8.1f}s")
        for finding in report.findings:
            print(f"    - {finding}")
        if report.blocks_signal:
            print("\nNO SIGNAL: inputs do not describe one coherent moment.")
            return EXIT_ERROR

        if snapshot.mark:
            _rule("mark")
            print(f"  mark price:  {snapshot.mark.mark_price}")
            print(f"  index price: {snapshot.mark.index_price}")

        if snapshot.funding:
            history = await provider.funding_history(symbol, limit=100)
            funding = analyse_funding(snapshot.funding, history)
            _rule("funding (FUTURES SPEC 27)")
            print(f"  rate:        {snapshot.funding.rate}")
            print(f"  bias:        {funding.bias.value}")
            print(f"  trend:       {funding.trend.value}")
            for finding in funding.findings:
                print(f"    - {finding}")

        if snapshot.open_interest:
            oi_history = await provider.open_interest_history(symbol, limit=30)
            earlier = oi_history[0] if oi_history else None
            oi = analyse_open_interest(
                snapshot.open_interest, earlier, await _price_change(provider, symbol)
            )
            _rule("open interest (FUTURES SPEC 28)")
            print(f"  open interest: {snapshot.open_interest.open_interest}")
            print(f"  flow read:     {oi.flow.value}")
            print(f"  oi change:     {_pct(oi.oi_change_pct)}")
            print(f"  price change:  {_pct(oi.price_change_pct)}")
            for finding in oi.findings:
                print(f"    - {finding}")

        if snapshot.order_book is not None:
            liquidity = assess_liquidity(snapshot.order_book, notional, buying=True)
            _rule(f"liquidity for {notional} USDT (FUTURES SPEC 29)")
            print(f"  tradeable:   {'yes' if liquidity.tradeable else 'NO'}")
            print(f"  sweep cost:  {_pct(liquidity.sweep_cost_pct)}")
            print(f"  spread:      {liquidity.spread_bps} bps")
            for finding in liquidity.findings:
                print(f"    - {finding}")

        if snapshot.contract and not snapshot.contract.leverage_cap_known:
            _rule("what the venue would not supply")
            for note in snapshot.contract.notes:
                print(f"    - {note}")

        print(
            "\n  This is market data and its reading, not a recommendation.\n"
            "  No LONG/SHORT/WAIT is produced here: the futures council is F5\n"
            "  and is not built. ARUNA never places an order (FUTURES SPEC 50)."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"FUTURES SNAPSHOT FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await provider.close()


def _pct(value: Decimal | None) -> str:
    """A missing measurement says so; it never prints as zero."""
    return "unavailable" if value is None else f"{value:+.3f}%"


async def _price_change(
    provider: BinanceFuturesProvider, symbol: str
) -> Decimal | None:
    """Percent change over the last 30 minutes, or ``None``.

    Open interest may not be read without it (FUTURES SPEC 28): rising OI means
    opposite things depending on which way price went.
    """
    try:
        rows = await provider._get(  # noqa: SLF001 - the allowlist is the API
            "/fapi/v1/klines", symbol=symbol, interval="5m", limit=7
        )
    except ArunaError:
        return None
    if len(rows) < 2:
        return None
    first = Decimal(str(rows[0][1]))
    last = Decimal(str(rows[-1][4]))
    if first == 0:
        return None
    return (last - first) / first * Decimal(100)


def cmd_plan(args: argparse.Namespace) -> int:
    """Run the council on a perpetual and build a plan from its verdict."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_plan(settings, args))


async def _plan(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.futures.plan import render
    from aruna.futures.service import FuturesPlanService

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        horizon = Horizon(args.horizon)
    except ValueError:
        print(f"unknown horizon '{args.horizon}'", file=sys.stderr)
        await app.shutdown()
        return EXIT_ERROR

    service = FuturesPlanService(
        deliberation=app.deliberation,
        # The council as currently weighted, not the service wrapping it -
        # `use_history` swaps the instance, and the measured SPEC 16 factors
        # must reach a futures verdict exactly as they reach a spot one.
        council=app.council.council,
        store=app.futures_store,
        universe=app.universe,
    )

    try:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        run = await service.plan(
            symbols,
            horizon=horizon,
            equity=Decimal(str(args.equity)),
            risk_pct=Decimal(str(args.risk)) if args.risk is not None else None,
        )

        for plan in run.plans:
            _rule(f"{plan.symbol} {horizon.value}")
            print(render(plan))
            print()

        _rule("run")
        counts = run.to_dict()
        print(f"  considered:  {counts['considered']}")
        print(f"  plans:       {counts['plans']}")
        print(f"  refused:     {counts['refused']}")
        print(f"  waited:      {counts['waited']}")
        print(f"  no signal:   {counts['no_signal']}")
        print(f"  stored:      {counts['stored']}")
        for problem in run.errors:
            print(f"  ! {problem}")

        if not run.actionable:
            print(
                "\n  No plan was issued. That is an output, not a failure to\n"
                "  produce one (FUTURES SPEC 52)."
            )
        print(
            "\n  ARUNA placed no order, changed no leverage or margin setting,\n"
            "  and moved no funds (FUTURES SPEC 3, 50)."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"PLAN FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        # Service memiliki adapter bursanya sendiri sekarang - satu untuk
        # seumur hidupnya, bukan satu per tick - jadi ia yang harus menutupnya.
        await service.aclose()
        await app.shutdown()


def cmd_xau_loop(args: argparse.Namespace) -> int:
    """Analisa XAUUSD M5 pada timer, lalu berhenti.  ANALISA SAJA."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_xau_loop(settings, args))


async def _xau_loop(settings: Settings, args: argparse.Namespace) -> int:
    """Satu keputusan XAU per bar M5, disimpan apa adanya.

    Berdiri sendiri dari ``aruna run`` dengan alasan yang sama seperti
    ``futures-loop``: cadence dan universe-nya sendiri, dan matinya salah satu
    tidak boleh menyeret yang lain.

    **``ARUNA_ENABLED_MARKETS`` sengaja TIDAK disentuh.**  Menambahkan ``FOREX``
    di sana akan menyeret XAU ke loop upkeep crypto - persis kebalikan dari
    "modul terpisah" yang spec tuntut.  Providernya dibangun langsung di sini.
    """
    import asyncio as _asyncio
    from datetime import UTC, timedelta

    from aruna.core.clock import now_utc
    from aruna.data.quality import QualityGate
    from aruna.data.registry import build_provider
    from aruna.db.repositories.xau import XauRepository
    from aruna.xau.cooldown import Cooldown
    from aruna.xau.dolar import hitung_bukti_dolar, tarik_proksi
    from aruna.xau.loop import BAR_DIBUTUHKAN, SIMBOL, satu_tick

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        provider = build_provider(
            Market.FOREX,
            settings.providers.forex_provider,
            settings.data,
            api_key=settings.providers.forex_provider_api_key.get_secret_value(),
        )
    except ArunaError as exc:
        print(f"XAU PROVIDER UNAVAILABLE: {exc}", file=sys.stderr)
        await app.shutdown()
        return EXIT_ERROR

    gate = QualityGate(settings.data, source=provider.name)
    repo = XauRepository(app.db)
    cooldown = Cooldown()
    berhenti = now_utc() + timedelta(hours=args.hours)

    print(f"--- xau loop {'-' * 48}")
    print(f"  simbol:    {SIMBOL}")
    print(f"  interval:  {args.interval}s")
    print(f"  sampai:    {berhenti.isoformat()}  ({args.hours}h)")
    print("  ARUNA menganalisa saja. Tidak ada order, tidak ada dana bergerak.")

    await provider.open()
    dinilai = tersimpan = dilewati = 0
    # Proksi dolar ditarik per JAM, bukan tiap bar. Korelasi 250-bar adalah
    # statistik dua puluh jam; menariknya tiap lima menit menghabiskan 288
    # kredit sehari untuk angka yang hampir tidak berubah. Per jam: 24.
    tick_per_tarikan_proksi = max(1, 3600 // max(args.interval, 1))
    tick_ke = 0
    dolar = None
    # Bar terakhir yang sudah dinilai. Tanpa ini, dua tick dalam satu jendela
    # 300 detik menulis dua baris untuk bar yang sama dan melanggar kunci
    # uniknya - dan galat itu mematikan loop yang lalu dinyalakan ulang.
    #
    # **Dibaca dari basis data, bukan dimulai dari None.** Penjaga di memori
    # hanya menutup drift jadwal di dalam satu proses; restart menghapusnya,
    # dan proses baru akan menilai ulang bar yang sudah disimpan proses lama.
    # Diukur di produksi 2026-08-27: crash loop tiga kali beruntun tiap
    # delapan detik, karena penjaga supervisor menyalakan ulang apa yang baru
    # saja mati karena tabrakan kunci unik.
    as_of_terakhir = await repo.as_of_terakhir()
    if as_of_terakhir is not None:
        if as_of_terakhir.tzinfo is None:
            as_of_terakhir = as_of_terakhir.replace(tzinfo=UTC)
        print(f"  bar terakhir yang sudah dinilai: {as_of_terakhir.isoformat()}")
    try:
        while now_utc() < berhenti:
            if tick_ke % tick_per_tarikan_proksi == 0:
                proksi = await tarik_proksi(provider, limit=BAR_DIBUTUHKAN)
                if proksi:
                    xau_bar = await provider.fetch_candles(
                        SIMBOL, Horizon.M5, limit=BAR_DIBUTUHKAN
                    )
                    dolar = hitung_bukti_dolar(xau_bar, proksi)
                    print(
                        f"  proksi dolar {dolar.simbol}: r={dolar.korelasi} "
                        f"atas {dolar.sampel} return"
                    )
            tick_ke += 1

            hasil = await satu_tick(
                provider,
                gate,
                sekarang=now_utc(),
                repo=repo,
                cooldown=cooldown,
                as_of_terakhir=as_of_terakhir,
                dolar=dolar,
            )
            if hasil.menilai:
                dinilai += 1
                tersimpan += 1 if hasil.prediction_id else 0
                as_of_terakhir = hasil.as_of
            else:
                dilewati += 1
            await _asyncio.sleep(args.interval)
    except (KeyboardInterrupt, _asyncio.CancelledError):
        pass
    finally:
        await provider.close()
        await app.shutdown()

    print(f"  dinilai {dinilai}, tersimpan {tersimpan}, dilewati {dilewati}")
    return EXIT_OK


def cmd_futures_loop(args: argparse.Namespace) -> int:
    """Plan on a timer for a fixed stretch, then stop (FUTURES SPEC 48)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_futures_loop(settings, args))


async def _futures_loop(settings: Settings, args: argparse.Namespace) -> int:
    from datetime import datetime, timedelta

    from aruna.db.repositories.futures_metrics import FuturesMetricsRepository
    from aruna.futures.binance import BinanceFuturesProvider
    from aruna.futures.learning import FuturesLearningReport, daily_report
    from aruna.futures.notify import PlanNotifier
    from aruna.futures.resolve import FuturesResolver
    from aruna.futures.scheduler import FuturesScheduler, next_run_window
    from aruna.futures.service import FUTURES_MODEL_VERSION, FuturesPlanService
    from aruna.notify.telegram.sender import sender_from

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        horizon = Horizon(args.horizon)
    except ValueError:
        print(f"unknown horizon '{args.horizon}'", file=sys.stderr)
        await app.shutdown()
        return EXIT_ERROR

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # Send-only, never polling: the bot in `aruna run` owns getUpdates, and a
    # second consumer would break both. Sending has no such exclusivity.
    notifier = None
    if not args.quiet:
        sender = sender_from(settings.telegram)
        if sender.configured:
            notifier = PlanNotifier(
                sender=sender,
                horizon_hours=horizon.duration.total_seconds() / 3600,
                # Konstanta yang sama yang dipakai saat menyimpan barisnya.
                model_version=FUTURES_MODEL_VERSION,
                # Supaya "laporan hari ini sudah dikirim" bertahan melewati
                # restart. Penjaga proses menjalankan ulang loop ini tiap dua
                # puluh empat jam; penanda yang hanya di memori berarti setiap
                # kelahiran ulang mengirim laporan kemarin sekali lagi.
                state=app.app_state,
                # PASAL 14.31. Tanpa ini tidak ada jejak pengiriman rencana
                # yang ditulis, dan hasilnya nanti tidak akan pernah didorong -
                # yang persis dilaporkan operator: "saat signal dikirim ke tele
                # gaada resultnya, hilang semua".
                store=app.futures_store,
            )
        else:
            print("  (tidak ada chat Telegram terkonfigurasi - jalan tanpa notifikasi)")

    async def _daily(awal: datetime, akhir: datetime) -> str | None:
        """Laporan untuk satu hari WIB penuh, bukan untuk 24 jam terakhir.

        Jendelanya datang dari pemanggil, dan itu yang membuat angkanya
        benar-benar direset di batas hari: sebuah laporan yang dibangun dari
        "sejak sehari lalu" terhitung saat kirim akan memuat sebagian hari
        sebelumnya setiap kali pengirimannya tidak tepat waktu.
        """
        counts = await app.futures_store.counts_since(awal, akhir)
        if not sum(counts.values()):
            return None
        # Loaded, not left empty. Constructed with no arguments this reported
        # "INSUFFICIENT SAMPLE: 0 resolved plan(s)" however many outcomes the
        # resolver had written - the loop closed and the report never looked.
        results = await app.futures_store.results_since(awal, akhir)
        return daily_report(
            FuturesLearningReport(results=tuple(results)),
            plans_made=counts.get("PLAN", 0),
            refusals=counts.get("REFUSED", 0),
            waits=counts.get("WAIT", 0),
            no_signals=counts.get("NO_SIGNAL", 0),
            as_of=akhir,
        )

    service = FuturesPlanService(
        deliberation=app.deliberation,
        council=app.council.council,
        store=app.futures_store,
        universe=app.universe,
        # So each tick refreshes the candles the council reads. Without
        # it the loop re-derives one answer from frozen bars.
        ingest=app.ingest,
        # So the argument behind each plan survives the tick that made
        # it, and so a real disagreement reaches the operator.
        council_store=app.council_store,
        # PASAL 14.40/14.41: pattern discovery, spesialisasi agent, champion,
        # challenger, drift, dan korelasi. Semuanya sudah tersimpan dan tidak
        # satu pun pernah dibaca oleh lapisan yang memutuskan sampai baris ini.
        pembelajaran=app.pembelajaran,
        # PASAL 15.32. Proyektornya hidup di proses `aruna run`; PEMBACANYA
        # harus hidup di sini, karena keputusan futures dibuat di proses ini.
        #
        # Terukur 2026-08-20T21:49Z, dan hanya pengukuran produksi yang
        # menemukannya: tanpa baris ini `memory_pengaruh` UNKNOWN pada keempat
        # puluh amatan dan `memory_kasus` nol - seluruh Phase 15 hijau di test,
        # tersambung di loop upkeep, dan diam sepenuhnya di jalur keputusan.
        memory=app.memory_store,
        # PASAL 15.16: katalog pola Phase 12, DIBACA - tidak dihitung ulang.
        pola_store=app.adaptive_store,
        # PASAL 15.44, dan alasannya sama persis dengan `memory=` di atas:
        # putusannya DIHITUNG di proses `aruna run` dan DIPAKAI di sini. Tanpa
        # baris ini gerbangnya tertulis, teruji, dan tidak pernah menutup di
        # satu pun keputusan hidup.
        app_state=app.app_state,
        # Bagian 16.2. Funding rate dan open interest diambil tiap siklus di
        # proses INI, dipakai untuk rencananya, lalu dibuang - dan akibatnya
        # dua dari tiga belas pemicu simulasi tidak pernah bisa menyala, karena
        # yang membacanya hidup di proses `aruna run` yang tidak punya
        # angkanya. Baris ini yang menjembatani keduanya.
        metrik=FuturesMetricsRepository(app.db),
    )

    # Dinaikkan keluar dari `FuturesScheduler(...)` supaya namanya bisa
    # dipegang: service ini memiliki satu adapter bursa untuk seluruh loop -
    # itu yang membuat cache spesifikasi kontraknya berarti - dan adapter itu
    # harus ditutup di tiap jalan keluar, tidak hanya yang berhasil.
    async def _tutup() -> None:
        await service.aclose()
        await app.shutdown()

    try:
        scheduler = FuturesScheduler(
            service=service,
            symbols=symbols,
            horizon=horizon,
            equity=Decimal(str(args.equity)),
            interval_sec=int(args.interval),
            risk_pct=Decimal(str(args.risk)) if args.risk is not None else None,
            notifier=notifier,
            daily_report=_daily,
            # Closes the loop. Without it the plans pile up unscored and F6
            # measures nothing, however complete its machinery is.
            resolver=FuturesResolver(
                store=app.futures_store, provider=BinanceFuturesProvider()
            ),
        )
    except ArunaError as exc:
        print(f"LOOP REFUSED: {exc}", file=sys.stderr)
        await _tutup()
        return EXIT_ERROR

    until = next_run_window(args.hours)
    _rule("futures loop")
    print(f"  symbols:   {', '.join(symbols)}")
    print(f"  horizon:   {horizon.value}")
    print(f"  interval:  {args.interval}s")
    print(f"  until:     {isoformat(until)}  ({args.hours}h)")
    print("\n  ARUNA analyses only. No order is placed, no leverage or margin")
    print("  setting is changed, and no funds move (FUTURES SPEC 3, 50).")
    print("  Ctrl+C stops it; there is no position left behind.\n")

    try:
        stats = await scheduler.run_until(until)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n  stopped early by the operator")
        await _tutup()
        return EXIT_OK
    except ArunaError as exc:
        print(f"LOOP FAILED: {exc}", file=sys.stderr)
        await _tutup()
        return EXIT_ERROR

    _rule("run")
    print(f"  {stats.summary()}")
    for problem in stats.errors[:10]:
        print(f"  ! {problem}")

    counts = await app.futures_store.counts_since(stats.started_at)
    # The same empty-report defect as `_daily`, in its twin - and this is the
    # worse one, because the in-loop report only runs when a notifier exists
    # (scheduler.py). Under --quiet, or with no Telegram chat configured, this
    # closing block is the ONLY futures learning report the operator ever sees,
    # and it said "0 plan yang sudah selesai" on a run that may have liquidated
    # a recommended position.
    #
    # The window reaches back a day rather than to `stats.started_at`, because
    # `save_result` stamps `resolved_at` with the plan's horizon end, not the
    # wall clock - so a backlog plan scored during this run carries an earlier
    # timestamp and would fall outside a start-of-run cutoff.
    resolved = await app.futures_store.results_since(
        min(stats.started_at, now_utc() - timedelta(days=1))
    )
    print()
    print(
        daily_report(
            FuturesLearningReport(results=tuple(resolved)),
            plans_made=counts.get("PLAN", 0),
            refusals=counts.get("REFUSED", 0),
            waits=counts.get("WAIT", 0),
            no_signals=counts.get("NO_SIGNAL", 0),
            as_of=now_utc(),
        )
    )
    await _tutup()
    return EXIT_OK


def cmd_supervise(args: argparse.Namespace) -> int:
    """Jaga ARUNA tetap hidup: nyalakan semuanya, hidupkan lagi yang mati.

    Loop di dalam ARUNA sudah tahan terhadap tick yang gagal. Yang tidak bisa
    dijaga dari dalam adalah proses yang benar-benar mati - dan dari dalam
    proses, kematian itu tidak bisa dilaporkan (PASAL 37).

    Tetap analis: yang dijaga hidup adalah pembacaan dan analisis. Tidak ada
    order, tidak ada perubahan leverage, tidak ada dana yang berpindah.
    """
    from aruna.supervisor import (
        LOCK_PATH,
        AlreadyRunning,
        Supervisor,
        default_children,
        single_instance,
    )

    settings = _load_settings()
    _setup_cli_logging(settings)
    print("--- ARUNA - penjaga proses ----------------------------------")
    print("  Ctrl+C menghentikan penjaga DAN semua anaknya.")
    print("  Analisis saja: tidak ada order, tidak ada dana yang berpindah.")
    print()
    # `--symbols` menang kalau operator menyebutnya; kalau tidak, daftarnya
    # datang dari `.env`. Bawaan argumen sengaja `None` dan bukan sederet
    # simbol: sebuah bawaan CLI tidak bisa dibedakan dari pilihan operator, dan
    # `ARUNA.bat` memanggil perintah ini tanpa argumen sama sekali - jadi
    # bawaan CLI akan selalu menang atas konfigurasi, diam-diam.
    simbol = args.symbols or settings.upkeep.futures_symbols
    supervisor = Supervisor(children=default_children(simbol, hours=args.hours))
    print(f"  simbol futures: {len(settings.upkeep.futures_symbol_list)} perpetual")
    try:
        # Dua jalan menuju proses yang sama - tugas terjadwal saat login dan
        # klik manual - dan dua ARUNA hidup berarti dua bot Telegram menarik
        # antrean update yang sama. Menolak menyala lebih baik daripada jalan
        # dobel diam-diam.
        with single_instance(LOCK_PATH):
            asyncio.run(supervisor.run())
    except AlreadyRunning as exc:
        print(f"  {exc}")
        return 1
    except KeyboardInterrupt:
        print("\n  dihentikan operator")
    return 0


def cmd_upkeep(args: argparse.Namespace) -> int:
    """Run one maintenance cycle by hand: refresh candles, then score what is due."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_upkeep(settings, args))


async def _upkeep(settings: Settings, _args: argparse.Namespace) -> int:
    app = ArunaApplication(settings)
    try:
        # One shot, never a loop: `startup(background=False)` promises no
        # periodic task, and this command exists precisely so an operator can
        # force a catch-up without having to run `aruna run`.
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        if app.upkeep is None:
            print(
                "Upkeep tidak aktif: tidak ada provider yang terkonfigurasi, atau\n"
                "ARUNA_UPKEEP_ENABLED=false.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        _rule("UPKEEP - satu siklus")
        stats = await app.upkeep.cycle()
        print(f"  {stats.summary()}")
        for problem in stats.errors:
            print(f"  ! {problem}")
        print(
            "\n  Candle yang hilang dilaporkan sebagai gap, tidak pernah diisi\n"
            "  interpolasi (SPEC 4). Sinyal yang candle-nya belum menyusul tetap\n"
            "  LOCKED dan akan dinilai pada pass berikutnya."
        )
        return EXIT_OK if not stats.errors else EXIT_ERROR
    except ArunaError as exc:
        print(f"UPKEEP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_notify_test(args: argparse.Namespace) -> int:
    """Kirim contoh pesan ke Telegram, ditandai jelas sebagai uji coba.

    Gunanya melihat tata letaknya di layar ponsel - lebar baris, di mana
    terpotong, apakah emoji terbaca - tanpa menunggu signal sungguhan datang.

    Angkanya karangan, dan itu justru bahayanya: sebuah pesan tes yang terbaca
    sebagai signal asli membuat operator bertindak atas angka yang sengaja
    dibuat-buat untuk memeriksa tata letak. Karena itu penandanya huruf besar
    di baris pertama DAN baris terakhir - notifikasi ponsel sering memotong
    bagian tengah pesan, dan yang tersisa di layar kunci adalah kedua ujungnya.
    """
    from aruna.core.enums import Decision
    from aruna.notify.verdict import VoteSplit, render_analysis

    settings = _load_settings()
    _setup_cli_logging(settings)

    split = VoteSplit(
        setuju=("TECHNICAL", "STRUCTURE", "VOLUME"),
        kontra=("RISK", "NEWS"),
    )
    # Simbol karangan, bukan ticker sungguhan.
    #
    # Angkanya memang sudah ditulis tangan sejak awal - tidak ada satu pun
    # panggilan ke pasar di jalur ini - tapi angka karangan pada nama aset yang
    # nyata tetap terbaca sebagai kabar tentang aset itu. "BTC/USDT 63000"
    # adalah harga yang masuk akal, dan penanda uji coba di kedua ujung pesan
    # bersaing dengan pengenalan yang lebih cepat daripada membaca: mata
    # menangkap tickernya lebih dulu.
    #
    # Operator: "jangan pakai data real". Yang paling mudah disangka nyata
    # adalah namanya, bukan angkanya.
    contoh = [
        render_analysis(
            symbol="CONTOH-A/USDT PERPETUAL", decision=Decision.BUY, split=split,
            confidence=0.87, entry="100.00", stop="98.00", target="105.00",
            timeframe="15M", reward_risk="1:2.3",
            leverage=10, liquidation="91.00", test_mode=True,
        ),
        render_analysis(
            symbol="CONTOH-B/USDT PERPETUAL", decision=Decision.SELL, split=split,
            confidence=0.84, entry="50.00", stop="51.00", target="47.50",
            timeframe="15M", reward_risk="1:2.1", test_mode=True,
        ),
        # Dua contoh ARUNA RESULT hilang bersama jalur spot (2026-08-25):
        # `render_result` melaporkan hasil prediksi spot, dan tidak ada lagi
        # prediksi spot untuk dilaporkan.
        _contoh_futures(split),
    ]

    if args.print_only:
        # NO SIGNAL ikut dicetak di sini dan TIDAK ikut dikirim. Operator
        # meminta Telegram tidak menerimanya; melihat tata letaknya di
        # terminal bukan hal yang sama, dan tetap berguna saat blok itu
        # diubah.
        contoh.insert(2, render_analysis(
            symbol="CONTOH-C/USDT PERPETUAL", decision=Decision.WAIT, split=split,
            reason="Evidence tidak cukup kuat untuk menghasilkan LONG atau SHORT.",
            test_mode=True,
        ))
        for teks in contoh:
            print(teks)
            print("\n" + "-" * 40 + "\n")
        return EXIT_OK

    return asyncio.run(_notify_test(settings, contoh))


def _contoh_futures(split: object) -> str:
    """Satu pesan ARUNA FUTURES bertanda uji coba.

    Jalur inilah yang paling berbahaya untuk diuji tanpa penanda - pesannya
    membawa entry, stop, leverage dan harga likuidasi - dan sampai baris ini ada
    ia adalah satu-satunya blok pesan yang tidak pernah ikut ``notify-test``.
    Sebuah mode uji coba yang tidak pernah dipanggil dari mana pun tidak
    melindungi apa pun.

    Angkanya dikarang di sini dan tidak diambil dari pasar, sesuai permintaan
    operator: sebuah percobaan yang memakai harga sungguhan masih bisa dibaca
    sebagai kabar tentang pasar, penanda atau bukan.
    """
    from decimal import Decimal
    from types import SimpleNamespace

    from aruna.futures.notify import _alert
    from aruna.futures.plan import PlanVerdict

    plan = SimpleNamespace(
        symbol="CONTOH/USDT PERPETUAL",
        verdict=PlanVerdict.PLAN,
        side=SimpleNamespace(value="LONG"),
        entry=Decimal("100.0000"),
        stop=Decimal("98.0000"),
        target=Decimal("105.0000"),
        quantity=Decimal("10"),
        leverage=5,
        margin_mode=SimpleNamespace(value="ISOLATED"),
        liquidation=SimpleNamespace(price=Decimal("82.0000")),
        buffer=SimpleNamespace(band="CONTOH", score=99),
        net_rr=Decimal("2.10"),
        tick_size=Decimal("0.0001"),
        caveats=("angka di pesan ini dikarang untuk memeriksa tata letak",),
    )
    note = SimpleNamespace(
        symbol=plan.symbol, confidence=0.5, disagreement=0.0,
        split=split, reasons=(), debated=False, high_disagreement=False,
    )
    return _alert(plan, now_utc(), note=note, test_mode=True)


async def _notify_test(settings: Settings, contoh: list[str]) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
        if app.bot is None:
            print(
                "TELEGRAM TIDAK AKTIF: tidak ada ARUNA_TELEGRAM_BOT_TOKEN.\n"
                "Pakai --print-only untuk melihat pesannya di terminal.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        terkirim = 0
        for teks in contoh:
            if await app.bot.send(teks):
                terkirim += 1
        print(f"terkirim: {terkirim}/{len(contoh)}")
        return EXIT_OK if terkirim == len(contoh) else EXIT_ERROR
    except ArunaError as exc:
        print(f"GAGAL: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_learn(args: argparse.Namespace) -> int:
    """Jalankan satu putaran pembelajaran adaptif (PASAL 12.27).

    Membaca sejarah, mencari pola, mengukur strategi dan spesialisasi agent,
    lalu menyimpan hasilnya. **Tidak mengubah satu pun bobot atau ambang** -
    PASAL 11.16 dan 12.26 melarangnya, dan perintah ini tidak punya jalur untuk
    melakukannya seandainya pun diminta.
    """
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_learn(settings, args))


async def _learn(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.db.repositories.learning12 import LearningRepository
    from aruna.learning.adaptive import AdaptiveLearningService
    from aruna.notify.learning import render_learning

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        store = LearningRepository(app.db)
        run = await AdaptiveLearningService(store).run()

        _rule("pembelajaran")
        print(f"  {run.summary()}")
        print(f"  pola tersimpan: {run.stored_patterns}")

        _rule("pola yang berbeda dari rata-rata")
        if not run.discovery.notable:
            print("  belum ada yang bedanya melampaui ketidakpastian sample")
        for p in run.discovery.notable[: (args.top or 10)]:
            print(f"  {p.line()}")

        _rule("strategi")
        for s in run.strategies[: (args.top or 10)]:
            print(
                f"  {s.strategy_code} {s.dimensions.get('regime', ''):<16}"
                f" {s.evidence.label()}"
            )
            print(f"      bersih {s.net_pnl:.2f}  drawdown {s.max_drawdown:.2f}")

        _rule("agent")
        for prof in run.profiles:
            print(f"  {prof.line()}")

        _rule("ringkasan seperti yang akan dibaca operator")
        for baris in render_learning(
            observations=run.observations,
            baseline_label=run.discovery.baseline.label(),
            patterns=[p.line() for p in run.discovery.notable],
            specialists=run.specialists,
            strategies=[
                f"{s.strategy_code}: {s.evidence.label()}"
                for s in run.strategies
                if s.dimensions.get("regime") == "ALL"
            ],
        ):
            print(f"  {baris}")

        print(
            "\n  ARUNA tidak mengubah modelnya sendiri. Tidak ada bobot,\n"
            "  ambang, atau parameter yang berubah karena perintah ini\n"
            "  (PASAL 11.16, 12.26)."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"LEARNING FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_korpus(args: argparse.Namespace) -> int:
    """Ukur keunggulan tiap agen atas korpus keputusan lintas regime.

    **Perintah ini menjawab satu pertanyaan: agen mana yang benar-benar
    menyumbang.** Bukan "agen mana yang sering benar" - itu pertanyaan yang
    jawabannya menyesatkan, karena di pasar yang naik 58% waktu, agen yang
    selalu bilang BUY benar 58% dan menyumbang nol.

    Council diputar ulang di candle yang sudah tersimpan, jadi tidak ada
    panggilan pasar dan tidak ada yang ditulis. Jalankan sesudah mengubah agen
    mana pun; angkanya sebanding karena korpusnya sama.
    """
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_korpus(settings, args))


async def _korpus(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.backtest.korpus import bangun
    from aruna.core.enums import Horizon

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        interval = Horizon(args.interval)
        simbol = await app.db.fetch(
            "SELECT DISTINCT symbol FROM candles WHERE interval_code = %s "
            "AND symbol LIKE '%%/%%' ORDER BY symbol",
            interval.value,
        )
        candles: dict[str, list] = {}
        for row in simbol:
            candles[row["symbol"]] = await app.db.fetch(
                "SELECT open_time, close_time, open, high, low, close, volume "
                "FROM candles WHERE symbol = %s AND interval_code = %s "
                "ORDER BY open_time",
                row["symbol"],
                interval.value,
            )
        if not candles:
            print(f"tidak ada candle {interval.value} tersimpan", file=sys.stderr)
            return EXIT_ERROR

        korpus = bangun(candles, interval=interval)
        dasar = korpus.garis_dasar
        if dasar is None:
            print("korpus kosong - tidak ada keputusan yang bisa dinilai")
            return EXIT_ERROR

        rentang = sorted(o.pada for o in korpus.opini)
        _rule(f"KORPUS {interval.value}")
        print(f"  simbol      : {len(candles)}")
        print(f"  keputusan   : {len(korpus.keputusan):,}")
        print(f"  opini agen  : {len(korpus.opini):,}")
        print(f"  rentang     : {rentang[0].date()} .. {rentang[-1].date()}")
        print(f"  gagal       : {korpus.gagal}")
        print(f"  garis dasar : {dasar:.1%} naik")
        print()
        print("  Keunggulan diukur dalam POIN di atas garis dasar, bukan")
        print("  akurasi. Nol berarti agen itu tidak menyumbang apa pun.")
        print()
        def _poin(nilai: float | None, n: int) -> str:
            # Tidak diukur bukan diukur nol. "+0.0" di sebelah n=0 terbaca
            # sebagai "agen ini netral", padahal artinya ia tidak pernah
            # bersuara ke arah itu sama sekali.
            if nilai is None or n < args.min_sample:
                return f"{'-':>8}"
            return f"{nilai:>+8.1f}"

        print(f"  {'agen':12} {'BUY':>8} {'n':>7} {'SELL':>8} {'n':>7}")
        for agen in sorted({o.agen for o in korpus.opini}):
            eb, nb = korpus.edge(agen, "BUY")
            es, ns = korpus.edge(agen, "SELL")
            if max(nb, ns) < args.min_sample:
                continue
            print(f"  {agen:12} {_poin(eb, nb)} {nb:>7} {_poin(es, ns)} {ns:>7}")
        print()
        print("  ARUNA MENGANALISIS SAJA. Tidak ada order yang dikirim.")
        return EXIT_OK
    finally:
        await app.shutdown()


def cmd_strategies(args: argparse.Namespace) -> int:
    """Katalog strategi, statusnya, dan hasilnya (PASAL 12.7, 12.15)."""
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_strategies(settings, args))


async def _strategies(settings: Settings, args: argparse.Namespace) -> int:
    from aruna.db.repositories.learning12 import LearningRepository
    from aruna.learning.evidence import Evidence
    from aruna.learning.lifecycle import evaluate
    from aruna.learning.strategies import by_code

    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
    except ArunaError as exc:
        print(f"STARTUP FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        store = LearningRepository(app.db)
        katalog = await store.catalog_with_performance()
        baseline = await store.overall_win_rate()

        if not katalog:
            print("\n  Katalog kosong. Jalankan `aruna learn` dulu.")
            return EXIT_OK

        _rule("katalog strategi")
        if baseline is not None:
            print(f"  rata-rata ARUNA: {baseline:.0%}\n")
        for s in katalog:
            bukti = Evidence(
                wins=int(s["wins"] or 0), losses=int(s["losses"] or 0)
            )
            entri = by_code(str(s["code"]))
            print(f"  {s['code']}  {s['name']}   [{s['status']}]")
            print(f"      {bukti.label()}")
            print(
                f"      bersih {Decimal(str(s['net_pnl'] or 0)):.2f}"
                f"   drawdown {Decimal(str(s['max_drawdown'] or 0)):.2f}"
            )
            rezim = s.get("preferred_regimes") or []
            if rezim:
                print(f"      rezim: {', '.join(rezim)}")
            if entri is not None and args.detail:
                print(f"      {entri.description}")
                for k in entri.conditions:
                    print(f"        - {k}")
            if s.get("status_reason"):
                print(f"      sebab: {s['status_reason']}")
            print()

        laporan = evaluate(katalog, baseline=baseline)
        _rule("penilaian daur hidup")
        print(f"  {laporan.summary()}\n")
        for a in laporan.to_propose:
            print(f"  {a.line()}\n")
        if not laporan.to_propose:
            print("  Tidak ada yang menunggu keputusan Anda.\n")

        print(
            "  ARUNA memasang label pengamatan sendiri (ACTIVE, DEGRADED,\n"
            "  UNDER_REVIEW). Menghentikan sebuah strategi - SUSPENDED atau\n"
            "  RETIRED - mengubah apa yang ARUNA pertimbangkan, jadi itu\n"
            "  keputusan Anda (PASAL 12.20). Tidak ada strategi yang pernah\n"
            "  dihapus (PASAL 12.15)."
        )
        return EXIT_OK
    except ArunaError as exc:
        print(f"CATALOG FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_health(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    _setup_cli_logging(settings)
    return asyncio.run(_health(settings))


async def _health(settings: Settings) -> int:
    app = ArunaApplication(settings)
    try:
        await app.startup(background=False)
        report = await app.health_now()
        print(json.dumps(report.to_dict(), indent=2))
        return EXIT_OK if report.healthy else EXIT_ERROR
    except ArunaError as exc:
        print(f"HEALTH CHECK FAILED: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await app.shutdown()


def cmd_run(_args: argparse.Namespace) -> int:
    settings = _load_settings()
    app = ArunaApplication(settings)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except ArunaError as exc:
        print(f"\nSTARTUP FAILED\n\n{exc}\n", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aruna",
        description=(
            "ARUNA AI - market research and paper trading intelligence "
            f"(crypto + IDX). This build is PHASE {CURRENT_PHASE}."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "typical first run:\n"
            "  python -m aruna doctor\n"
            "  python -m aruna createdb\n"
            "  python -m aruna migrate\n"
            "  python -m aruna seed\n"
            "  python -m aruna providers\n"
            "  python -m aruna fetch\n"
            "  python -m aruna run\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="print version and environment").set_defaults(
        func=cmd_version
    )
    sub.add_parser("config", help="print the redacted effective configuration").set_defaults(
        func=cmd_config
    )
    sub.add_parser("doctor", help="check every prerequisite and report what to fix").set_defaults(
        func=cmd_doctor
    )
    sub.add_parser("createdb", help="create the ARUNA database if missing").set_defaults(
        func=cmd_createdb
    )

    migrate = sub.add_parser("migrate", help="apply pending schema migrations")
    migrate.add_argument("--status", action="store_true", help="show status, apply nothing")
    migrate.add_argument("--dry-run", action="store_true", help="list what would be applied")
    migrate.set_defaults(func=cmd_migrate)

    seed = sub.add_parser("seed", help="load the tradable universe")
    seed.add_argument("--file", help="path to a universe JSON file")
    seed.add_argument(
        "--prune",
        action="store_true",
        help="disable enabled assets that are no longer in the universe "
        "(disabled, never deleted)",
    )
    seed.set_defaults(func=cmd_seed)

    sub.add_parser(
        "providers", help="show what each data source offers, and probe it"
    ).set_defaults(func=cmd_providers)

    fetch = sub.add_parser("fetch", help="backfill candles and record a snapshot")
    fetch.add_argument("--market", help="CRYPTO or IDX (default: all enabled)")
    fetch.add_argument("--symbols", help="comma separated, e.g. BTC/USDT,ETH/USDT")
    fetch.add_argument("--intervals", help="comma separated, e.g. 1m,15m,1d")
    fetch.add_argument("--limit", type=int, help="candles per interval")
    fetch.add_argument(
        "--no-snapshot", action="store_true", help="backfill candles only"
    )
    fetch.set_defaults(func=cmd_fetch)

    analyze = sub.add_parser(
        "analyze", help="compute indicators, structure and regime from stored candles"
    )
    analyze.add_argument("--market", help="CRYPTO or IDX (default: all enabled)")
    analyze.add_argument("--symbols", help="comma separated")
    analyze.add_argument("--intervals", help="comma separated (default: 1h,1d)")
    analyze.add_argument("--dry-run", action="store_true", help="compute, store nothing")
    analyze.add_argument("--verbose", action="store_true", help="show reasons and notes")
    analyze.set_defaults(func=cmd_analyze)

    news = sub.add_parser("news", help="fetch and classify news from RSS feeds")
    news.add_argument("--limit", type=int, default=15, help="items to display")
    news.set_defaults(func=cmd_news)

    fundamental = sub.add_parser(
        "fundamental", help="fetch IDX fundamentals and value them (SPEC 7)"
    )
    fundamental.add_argument("--symbols", help="comma separated, default: all IDX")
    fundamental.set_defaults(func=cmd_fundamental)

    correlate = sub.add_parser(
        "correlate", help="correlation matrix from stored candles"
    )
    correlate.add_argument("--market", help="CRYPTO or IDX (default: all enabled)")
    correlate.add_argument("--interval", help="single interval (default: 1h)")
    correlate.add_argument("--limit", type=int, default=200, help="bars per asset")
    correlate.add_argument("--dry-run", action="store_true", help="compute, store nothing")
    correlate.set_defaults(func=cmd_correlate)

    deliberate = sub.add_parser(
        "deliberate", help="run the agent roster over stored evidence (round one)"
    )
    deliberate.add_argument("--market", help="CRYPTO or IDX (default: all enabled)")
    deliberate.add_argument("--symbols", help="comma separated")
    deliberate.add_argument("--interval", help="single interval (default: 1h)")
    deliberate.add_argument("--dry-run", action="store_true", help="store nothing")
    deliberate.add_argument("--verbose", action="store_true", help="show every opinion")
    deliberate.set_defaults(func=cmd_deliberate)

    council = sub.add_parser(
        "council", help="convene the full council: protest, veto, judge"
    )
    council.add_argument("--market", help="CRYPTO or IDX (default: all enabled)")
    council.add_argument("--symbols", help="comma separated")
    council.add_argument("--interval", help="single interval (default: 1h)")
    council.add_argument("--dry-run", action="store_true", help="store nothing")
    council.add_argument("--verbose", action="store_true", help="show every round")
    council.set_defaults(func=cmd_council)

    autopsy = sub.add_parser(
        "autopsy",
        help="learn from resolved predictions: losses, ghosts, calibration",
    )
    autopsy.add_argument(
        "--limit", type=int, default=500, help="resolved predictions to review"
    )
    autopsy.add_argument("--dry-run", action="store_true", help="store nothing")
    autopsy.add_argument(
        "--verbose", action="store_true", help="show weights and counterfactuals"
    )
    autopsy.set_defaults(func=cmd_autopsy)

    backtest = sub.add_parser(
        "backtest", help="replay the decision path over stored history"
    )
    backtest.add_argument("--market", help="CRYPTO or IDX (default: all enabled)")
    backtest.add_argument("--symbols", help="comma separated")
    backtest.add_argument("--interval", help="single interval (default: 1h)")
    backtest.add_argument(
        "--every", type=int, default=1, help="decide every Nth bar (default: 1)"
    )
    backtest.add_argument(
        "--folds", type=int, default=4, help="walk-forward folds (default: 4)"
    )
    backtest.add_argument(
        "--include-holdout",
        action="store_true",
        help="also evaluate the reserved out-of-sample tail (SPEC 38) - do not "
        "use this while choosing between model variants",
    )
    backtest.add_argument(
        "--exit-at-target",
        action="store_true",
        help="variant: close when the target is touched instead of holding to "
        "horizon expiry. Not the live rule - a candidate for SPEC 44 approval",
    )
    backtest.add_argument(
        "--stop-loss",
        action="store_true",
        help="variant: close at the mirror of the target. Meant to be combined "
        "with --exit-at-target; neither is the live rule",
    )
    backtest.add_argument("--verbose", action="store_true", help="per-asset detail")
    backtest.set_defaults(func=cmd_backtest)

    replay = sub.add_parser(
        "replay", help="re-run stored decisions and check they reproduce"
    )
    replay.add_argument("--limit", type=int, default=20, help="decisions to replay")
    replay.add_argument("--dry-run", action="store_true", help="store nothing")
    replay.set_defaults(func=cmd_replay)

    research = sub.add_parser(
        "research", help="questions ARUNA raises from its own record, and drift"
    )
    research.add_argument("--dry-run", action="store_true", help="store nothing")
    research.set_defaults(func=cmd_research)

    proposals = sub.add_parser(
        "proposals", help="model change proposals and the decisions on record"
    )
    proposals.add_argument("--limit", type=int, default=20, help="proposals to show")
    proposals.set_defaults(func=cmd_proposals)

    futures = sub.add_parser(
        "futures",
        help="pull one live perpetual snapshot and read it (no DB, no credentials)",
    )
    futures.add_argument("symbol", help="perpetual symbol, e.g. BTCUSDT")
    futures.add_argument(
        "--notional",
        type=float,
        default=1000.0,
        help="position size in USDT to test the order book against (default 1000)",
    )
    futures.set_defaults(func=cmd_futures)

    plan = sub.add_parser(
        "plan",
        help="run the council on a perpetual and build a futures plan from it",
    )
    plan.add_argument("symbols", help="comma separated, e.g. BTCUSDT,ETHUSDT")
    plan.add_argument(
        "--horizon", default="4h", help="council interval (default 4h)"
    )
    plan.add_argument(
        "--equity",
        type=float,
        default=10_000.0,
        help="account equity the position is sized from (default 10000)",
    )
    plan.add_argument(
        "--risk",
        type=float,
        default=None,
        help="risk per trade in percent (default from the risk engine, max 2)",
    )
    plan.set_defaults(func=cmd_plan)

    loop = sub.add_parser(
        "futures-loop",
        help="plan on a timer for a fixed stretch, then stop (analysis only)",
    )
    loop.add_argument("symbols", help="comma separated, e.g. BTCUSDT,ETHUSDT")
    loop.add_argument("--horizon", default="4h", help="council interval (default 4h)")
    loop.add_argument("--hours", type=float, default=24.0, help="how long to run")
    loop.add_argument(
        "--interval", type=int, default=900, help="seconds between ticks (default 900)"
    )
    loop.add_argument("--equity", type=float, default=10_000.0)
    loop.add_argument("--risk", type=float, default=None)
    loop.add_argument(
        "--quiet",
        action="store_true",
        help="store plans without sending any Telegram notification",
    )
    loop.set_defaults(func=cmd_futures_loop)

    xau = sub.add_parser(
        "xau-loop",
        help="analisa XAUUSD M5 pada timer, lalu berhenti (analisa saja)",
    )
    xau.add_argument("--hours", type=float, default=24.0, help="berapa lama berjalan")
    xau.add_argument(
        "--interval",
        type=int,
        default=300,
        help="detik antar tick; bawaan 300 = satu bar M5",
    )
    xau.set_defaults(func=cmd_xau_loop)

    upkeep = sub.add_parser(
        "upkeep",
        help="run exactly one maintenance cycle: refresh due candles, then score "
        "due signals. The periodic loop belongs to `aruna run`",
    )
    # No --once flag: this command has exactly one mode. It used to carry one,
    # parsed into a parameter the handler named `_args` and never read, so
    # passing it and omitting it did the same thing. A knob wired to nothing is
    # the defect this repo keeps finding; the help text below says the same
    # thing without pretending to be a choice.
    upkeep.set_defaults(func=cmd_upkeep)

    learn = sub.add_parser(
        "learn",
        help="jalankan satu putaran pembelajaran adaptif (PASAL 12); "
        "menganalisis saja, tidak mengubah model",
    )
    learn.add_argument(
        "--top", type=int, default=10, help="berapa baris per bagian"
    )
    learn.set_defaults(func=cmd_learn)

    strategies = sub.add_parser(
        "strategies",
        help="katalog strategi, statusnya, dan hasilnya (PASAL 12.7)",
    )
    strategies.add_argument(
        "--detail", action="store_true", help="ikut cetak deskripsi dan kondisi"
    )
    strategies.set_defaults(func=cmd_strategies)

    korpus = sub.add_parser(
        "korpus",
        help="ukur keunggulan tiap agen atas korpus keputusan lintas regime",
    )
    korpus.add_argument(
        "--interval",
        default="1d",
        help="interval candle yang diputar ulang (default 1d - satu-satunya "
        "yang riwayatnya memuat lebih dari satu regime)",
    )
    korpus.add_argument(
        "--min-sample",
        type=int,
        default=150,
        help="agen dengan suara lebih sedikit dari ini tidak dicetak; "
        "keunggulan dari sampel tipis adalah derau yang punya angka",
    )
    korpus.set_defaults(func=cmd_korpus)

    sub.add_parser("health", help="run one health sweep and print it as JSON").set_defaults(
        func=cmd_health
    )
    sub.add_parser("run", help="start ARUNA (Ctrl+C to stop)").set_defaults(func=cmd_run)

    history = sub.add_parser(
        "history",
        help="compare performance across today / 7d / 30d / all time",
    )
    history.add_argument(
        "--dimension",
        choices=["asset", "timeframe", "regime", "direction", "quality"],
        help="single dimension (default: all five)",
    )
    history.set_defaults(func=cmd_history)

    notify_test = sub.add_parser(
        "notify-test",
        help="kirim contoh pesan ke Telegram, ditandai jelas sebagai uji coba",
    )
    notify_test.add_argument(
        "--print-only",
        action="store_true",
        help="cetak ke terminal saja, jangan kirim ke Telegram",
    )
    notify_test.set_defaults(func=cmd_notify_test)

    supervise = sub.add_parser(
        "supervise",
        help="keep ARUNA running: start every process and restart what dies "
        "(Ctrl+C to stop everything)",
    )
    supervise.add_argument(
        "--symbols",
        default=None,
        help=(
            "perpetuals for the futures loop, comma separated "
            "(default: ARUNA_UPKEEP_FUTURES_SYMBOLS in .env)"
        ),
    )
    supervise.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="how long each futures-loop process runs before exiting cleanly; "
        "the supervisor starts a fresh one, so this is a recycle interval, "
        "not a stop time",
    )
    supervise.set_defaults(func=cmd_supervise)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SystemExit as exc:
        return _exit_code(exc)
    except KeyboardInterrupt:
        return EXIT_OK


def _exit_code(exc: SystemExit) -> int:
    """Turn any SystemExit into a shell exit code.

    ``SystemExit`` carries a message rather than a number when raised for a bad
    argument - ``_parse_intervals`` does exactly that. Calling ``int()`` on it
    replaced a clear one-line complaint with a ValueError traceback, which told
    the operator nothing about the flag they had actually mistyped.
    """
    code = exc.code
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
