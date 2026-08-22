"""The Telegram bot.

This is ARUNA's control surface, so two properties matter more than features:

* **Fail closed.** An unconfigured allowlist authorizes nobody.  A bot token
  with no ``ARUNA_TELEGRAM_CHAT_ID`` refuses every command rather than
  accepting commands from whoever finds it.
* **Every attempt is recorded.** Authorized or refused, each command lands in
  ``audit_logs`` with the chat id.  ``/kill`` is an operational action on a
  trading system; it needs a reviewable trail (SPEC 42, 43).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from telegram import BotCommand, Update
from telegram.error import InvalidToken as PTBInvalidToken
from telegram.error import TelegramError as PTBTelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from aruna.cache.redis_client import Cache
from aruna.core.clock import now_utc
from aruna.core.config import Settings
from aruna.core.enums import Horizon, Market
from aruna.core.errors import TelegramError
from aruna.core.logging import get_logger
from aruna.core.runtime_state import RuntimeState
from aruna.db.repositories.events import AuditRepository
from aruna.db.repositories.market_data import MarketDataRepository
from aruna.db.repositories.telegram import TelegramSubscriberRepository
from aruna.db.repositories.universe import UniverseRepository
from aruna.health.models import HealthReport
from aruna.notify.telegram import formatting as fmt
from aruna.notify.telegram.registry import Command, build_registry

log = get_logger("aruna.telegram")

#: Per-chat command budget.  Generous - this guards against a stuck client
#: looping, not against an attacker, who is already stopped by the allowlist.
RATE_LIMIT_COMMANDS = 30
RATE_LIMIT_WINDOW_SEC = 60


@dataclass(slots=True)
class BotDeps:
    """Everything the bot needs, passed in rather than imported.

    Keeps the bot testable and stops the notification layer from reaching back
    into the application object.
    """

    settings: Settings
    state: RuntimeState
    phase: int
    latest_health: Callable[[], HealthReport | None]
    refresh_health: Callable[[], Awaitable[HealthReport]] | None = None
    audit: AuditRepository | None = None
    subscribers: TelegramSubscriberRepository | None = None
    cache: Cache | None = None
    market_data: MarketDataRepository | None = None
    universe: UniverseRepository | None = None
    council: Any = None
    signals: Any = None
    learning: Any = None
    governance: Any = None
    futures: Any = None
    #: Penyimpanan Phase 12. Dipakai dua perintah baca-saja; tidak ada jalur
    #: dari bot yang menulis ke sana.
    adaptive: Any = None


class TelegramBot:
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps
        self._settings = deps.settings
        self._registry = build_registry()
        self._app: Application | None = None
        self._started = False
        self._bind_phase1_handlers()

    # ---- registry ------------------------------------------------------

    @property
    def registry(self) -> dict[str, Command]:
        return self._registry

    @property
    def active(self) -> bool:
        return self._settings.telegram.active

    @property
    def started(self) -> bool:
        return self._started

    def _bind_phase1_handlers(self) -> None:
        """Attach implementations to the commands PHASE 1 actually delivers.

        Everything else keeps ``handler=None`` and answers with its phase.
        """
        bindings: dict[str, Any] = {
            "start": self._cmd_start,
            "help": self._cmd_help,
            "status": self._cmd_status,
            "health": self._cmd_health,
            "kill": self._cmd_kill,
            "resume": self._cmd_resume,
        }
        # PHASE 2 market data. Bound only when storage is wired in - without it
        # these would answer with silence, which SPEC 49 forbids.
        if self._deps.market_data is not None:
            bindings |= {
                "crypto": self._market_overview(Market.CRYPTO),
                "stocks": self._market_overview(Market.IDX),
                "btc": self._asset_detail(Market.CRYPTO, "BTC/USDT"),
                "eth": self._asset_detail(Market.CRYPTO, "ETH/USDT"),
                "sol": self._asset_detail(Market.CRYPTO, "SOL/USDT"),
                "bbca": self._asset_detail(Market.IDX, "BBCA"),
                "bbri": self._asset_detail(Market.IDX, "BBRI"),
                "bmri": self._asset_detail(Market.IDX, "BMRI"),
            }
        # PHASE 6 council. Bound only when the store is wired in - otherwise it
        # would answer with silence, which SPEC 49 forbids.
        if self._deps.council is not None and self._deps.universe is not None:
            bindings["council"] = self._cmd_council
        # PHASE 7 signals. Same rule: bound only when the store exists.
        if self._deps.signals is not None:
            bindings |= {
                "signals": self._cmd_signals,
                "today": self._cmd_today,
            }
        # PHASE 12 pembelajaran adaptif. Keduanya baca-saja - lihat registry.
        if self._deps.adaptive is not None:
            bindings |= {
                "strategies": self._cmd_strategies,
                "learning": self._cmd_learning,
            }
        # PHASE 8 performance and post-mortem.
        if self._deps.learning is not None and self._deps.signals is not None:
            bindings |= {
                "performance": self._performance(days=None, period="all time"),
                "weekly": self._performance(days=7, period="last 7 days"),
                "monthly": self._performance(days=30, period="last 30 days"),
                "autopsy": self._cmd_autopsy,
            }
        # FUTURES F5. Bound only when the store is wired in - otherwise it
        # would answer with silence, which SPEC 49 forbids.
        if self._deps.futures is not None:
            bindings["plans"] = self._cmd_plans
        # PHASE 10 governance. /approve and /reject are privileged, and the
        # approval gate refuses anything that is not a named person regardless.
        if self._deps.governance is not None:
            bindings |= {
                "research": self._cmd_research,
                "proposals": self._cmd_proposals,
                "approve": self._decide("approve"),
                "reject": self._decide("reject"),
            }
        for name, handler in bindings.items():
            self._registry[name].handler = handler

    # ---- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        if not self.active:
            log.info(
                "telegram.disabled",
                reason="no bot token configured",
                impact="ARUNA runs headless; no signals or alerts are delivered",
            )
            return
        if self._started:
            return

        token = self._settings.telegram.bot_token.get_secret_value()
        self._app = ApplicationBuilder().token(token).build()
        self._register_handlers(self._app)

        try:
            await self._app.initialize()
            await self._app.start()
            if self._app.updater is not None:
                # Drop the backlog: replaying commands queued while ARUNA was
                # down could fire /kill or /resume against a stale intent.
                await self._app.updater.start_polling(drop_pending_updates=True)
            await self._publish_command_menu()
        except PTBTelegramError as exc:
            self._app = None
            # Token yang tidak sah tidak akan pulih dengan menunggu; jaringan
            # yang tersumbat hampir selalu pulih. Ditandai di sini - di
            # satu-satunya tempat yang masih memegang tipe pengecualian
            # aslinya - karena di atas sana yang tersisa hanyalah kalimatnya,
            # dan menyimpulkan sebab dari kalimat berarti menebak.
            raise TelegramError(
                f"could not start Telegram bot: {exc}",
                permanent=isinstance(exc, PTBInvalidToken),
            ) from exc

        self._started = True

        # Inside its own guard, and deliberately not fatal. `get_me` is a
        # network call made purely to name the bot in the log line below, and it
        # sat outside the try above: a transient failure there propagated out of
        # start(), so app.py recorded a DOWN event and a START_FAILED for a bot
        # that had initialised, started, and was already polling normally.
        # Failing to learn the bot's username is not failing to start.
        try:
            me = await self.get_me()
        except (PTBTelegramError, TelegramError) as exc:
            log.warning(
                "telegram.started",
                username=None,
                detail=f"polling, but getMe failed: {exc}",
                authorized_chats=len(self._settings.telegram.authorized_chat_ids),
            )
            return

        log.info(
            "telegram.started",
            username=me.get("username"),
            authorized_chats=len(self._settings.telegram.authorized_chat_ids),
        )

    async def stop(self) -> None:
        if self._app is None:
            return
        try:
            if self._app.updater is not None and self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except PTBTelegramError as exc:
            log.warning("telegram.stop_failed", error=str(exc))
        finally:
            self._app = None
            self._started = False
            log.info("telegram.stopped")

    def _register_handlers(self, app: Application) -> None:
        # One dispatcher per command object, bound to its name and any aliases,
        # so an alias and its canonical name share the same auth and audit path.
        for command in self._unique_commands():
            app.add_handler(
                CommandHandler([command.name, *command.aliases], self._dispatch(command))
            )
        app.add_error_handler(self._on_error)

    def _unique_commands(self) -> list[Command]:
        seen: dict[int, Command] = {}
        for command in self._registry.values():
            seen.setdefault(id(command), command)
        return list(seen.values())

    async def _publish_command_menu(self) -> None:
        if self._app is None:
            return
        entries = [
            BotCommand(
                command.name,
                (
                    command.summary
                    + ("" if command.implemented else f" [PHASE {command.phase}]")
                )[:256],
            )
            for command in self._unique_commands()
        ]
        try:
            await self._app.bot.set_my_commands(entries)
        except PTBTelegramError as exc:
            # Cosmetic only - the handlers work regardless of the menu.
            log.warning("telegram.set_commands_failed", error=str(exc))

    # ---- outbound ------------------------------------------------------

    async def get_me(self) -> dict[str, Any]:
        if self._app is None:
            raise TelegramError("bot is not started")
        try:
            me = await self._app.bot.get_me()
        except PTBTelegramError as exc:
            raise TelegramError(f"getMe failed: {exc}") from exc
        return {"id": me.id, "username": me.username, "name": me.full_name}

    async def send(self, text: str, *, chat_id: str | None = None) -> bool:
        """Send a message.  Returns False instead of raising on failure.

        A failed notification must not take down the caller - the health
        monitor sends alerts from inside its own loop.
        """
        return await self.send_id(text, chat_id=chat_id) is not None

    async def send_id(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        reply_to: int | None = None,
    ) -> int | None:
        """Kirim, dan kembalikan id pesannya - atau ``None`` kalau gagal.

        **Kenapa id-nya dibutuhkan.** Operator: *"masak tiba-tiba result aja
        tanpa sinyal"*. Sebuah pesan hasil hanya berguna kalau bisa
        dipasangkan dengan signal yang dinilainya, dan Telegram memasangkan
        pesan lewat id - bukan lewat kemiripan isi.

        ``send`` tetap ada dan tetap mengembalikan ``bool``: puluhan pemanggil
        tidak peduli id-nya, dan mengubah semuanya sekaligus akan menyentuh
        jauh lebih banyak jalur daripada yang diperbaiki.

        ``reply_to`` yang menunjuk pesan yang sudah dihapus membuat Telegram
        menolak seluruh kirimannya. Karena itu kegagalannya dicoba ulang tanpa
        balasan: kabar bahwa ARUNA salah tidak boleh hilang gara-gara pesan
        lama yang dihapus operator (PASAL 11.21).
        """
        target = chat_id or self._settings.telegram.chat_id
        if self._app is None or not target:
            return None
        body = fmt.truncate(text)
        try:
            pesan = await self._app.bot.send_message(
                chat_id=target,
                text=body,
                disable_web_page_preview=True,
                reply_to_message_id=reply_to,
            )
        except PTBTelegramError as exc:
            if reply_to is not None:
                log.info(
                    "telegram.reply_target_gone", chat_id=target, error=str(exc)
                )
                return await self.send_id(body, chat_id=target)
            log.warning("telegram.send_failed", chat_id=target, error=str(exc))
            return None
        return int(getattr(pesan, "message_id", 0)) or None

    async def broadcast(self, text: str) -> int:
        sent = 0
        for chat_id in sorted(self._settings.telegram.authorized_chat_ids):
            if await self.send(text, chat_id=chat_id):
                sent += 1
        return sent

    # ---- dispatch ------------------------------------------------------

    def _dispatch(self, command: Command):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            chat = update.effective_chat
            if chat is None:
                return
            chat_id = str(chat.id)
            authorized = self._settings.telegram.is_authorized(chat_id)

            await self._record_contact(update, authorized=authorized)

            if not authorized:
                await self._audit(
                    actor=f"telegram:{chat_id}",
                    action=f"COMMAND_{command.name.upper()}",
                    result="DENIED",
                    detail="chat is not on the allowlist",
                )
                log.warning(
                    "telegram.unauthorized", chat_id=chat_id, command=command.name
                )
                await self._reply(update, fmt.unauthorized(chat_id))
                return

            if not await self._within_rate_limit(chat_id):
                # Audited like every other refusal. This branch wrote no row,
                # which meant a burst of commands from an authorized chat left
                # no trace at all - and a rate limit with no record is unusable
                # for the one thing it exists to reveal, namely who is hammering
                # the bot and when.
                await self._audit(
                    actor=f"telegram:{chat_id}",
                    action=f"COMMAND_{command.name.upper()}",
                    result="DENIED",
                    detail=f"rate limited: more than the window of {RATE_LIMIT_WINDOW_SEC}s allows",
                )
                await self._reply(update, fmt.rate_limited(RATE_LIMIT_WINDOW_SEC))
                return

            if not command.implemented:
                await self._audit(
                    actor=f"telegram:{chat_id}",
                    action=f"COMMAND_{command.name.upper()}",
                    result="SUCCESS",
                    detail=f"not implemented; scheduled for phase {command.phase}",
                )
                await self._reply(update, command.unavailable_text(self._deps.phase))
                return

            try:
                await command.handler(update, context)  # type: ignore[misc]
            except Exception as exc:
                log.exception("telegram.command_failed", command=command.name)
                await self._audit(
                    actor=f"telegram:{chat_id}",
                    action=f"COMMAND_{command.name.upper()}",
                    result="FAILURE",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                await self._reply(
                    update,
                    f"ARUNA - COMMAND FAILED\n\n/{command.name} raised "
                    f"{type(exc).__name__}.\nThe error is in the log and the audit trail.",
                )

        return handler

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.error("telegram.update_error", error=str(context.error))

    # ---- PHASE 1 commands ----------------------------------------------

    async def _cmd_start(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)  # type: ignore[union-attr]
        authorized = self._settings.telegram.is_authorized(chat_id)
        await self._reply(
            update, fmt.welcome(self._settings, self._deps.phase, authorized=authorized)
        )

    async def _cmd_help(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, fmt.help_text(self._registry, self._deps.phase))

    async def _cmd_status(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        report = self._deps.latest_health()
        await self._reply(
            update,
            fmt.status_summary(report, self._settings, self._deps.state, self._deps.phase),
        )

    async def _cmd_health(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        # /health forces a fresh sweep; /status shows the cached one.
        report = self._deps.latest_health()
        if self._deps.refresh_health is not None:
            try:
                report = await asyncio.wait_for(self._deps.refresh_health(), timeout=30)
            except Exception as exc:  # noqa: BLE001 - report the cached sweep instead
                log.warning("telegram.health_refresh_failed", error=str(exc))
        await self._reply(update, fmt.health_detail(report))

    async def _cmd_kill(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)  # type: ignore[union-attr]
        actor = f"telegram:{chat_id}"
        reason = " ".join(context.args or []).strip() or "manual kill via Telegram"
        before = self._deps.state.kill_switch.to_dict()
        after = await self._deps.state.activate_kill_switch(reason=reason, actor=actor)
        await self._audit(
            actor=actor,
            action="KILL_SWITCH_ACTIVATE",
            entity_type="runtime_state",
            entity_id="kill_switch",
            before_state=before,
            after_state=after.to_dict(),
            detail=reason,
        )
        log.warning("runtime.kill_switch_engaged", actor=actor, reason=reason)
        await self._reply(update, fmt.kill_switch_engaged(reason, actor))

    async def _cmd_resume(self, update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)  # type: ignore[union-attr]
        actor = f"telegram:{chat_id}"
        before = self._deps.state.kill_switch.to_dict()
        after = await self._deps.state.release_kill_switch(actor=actor)
        await self._audit(
            actor=actor,
            action="KILL_SWITCH_RELEASE",
            entity_type="runtime_state",
            entity_id="kill_switch",
            before_state=before,
            after_state=after.to_dict(),
        )
        log.warning("runtime.kill_switch_released", actor=actor)
        await self._reply(update, fmt.kill_switch_released(actor))

    # ---- PHASE 2 market data --------------------------------------------

    def _market_overview(self, market: Market):
        async def handler(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
            assert self._deps.market_data is not None
            rows = await self._deps.market_data.latest_snapshots(market)
            title = "CRYPTO" if market is Market.CRYPTO else "IDX"
            await self._reply(
                update, fmt.market_overview(title, rows, phase=self._deps.phase)
            )

        return handler

    def _asset_detail(self, market: Market, symbol: str):
        async def handler(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
            store = self._deps.market_data
            assert store is not None

            snapshot = await store.latest_snapshot(market=market, symbol=symbol)
            candles: list[dict[str, Any]] = []
            if self._deps.universe is not None:
                asset = await self._deps.universe.find(market, symbol)
                if asset is not None:
                    interval = Horizon.M15 if market is Market.CRYPTO else Horizon.D1
                    candles = await store.candles(asset.id, interval, limit=8)
            await self._reply(
                update,
                fmt.asset_detail(symbol, snapshot, candles, phase=self._deps.phase),
            )

        return handler

    # ---- PHASE 6 council -------------------------------------------------

    async def _cmd_council(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        store = self._deps.council
        universe = self._deps.universe
        assert store is not None and universe is not None

        rows: list[dict[str, Any]] = []
        for market in self._settings.app.enabled_markets:
            for asset in await universe.assets(market=market, enabled_only=True):
                latest = await store.latest(asset.id, Horizon.H1.value)
                if latest is None:
                    continue
                latest |= {"symbol": asset.symbol, "interval_code": Horizon.H1.value}
                rows.append(latest)

        rows.sort(key=lambda r: r["as_of"], reverse=True)
        await self._reply(
            update, fmt.council_report(rows[:8], phase=self._deps.phase)
        )

    # ---- PHASE 7 signals -------------------------------------------------

    async def _cmd_signals(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        store = self._deps.signals
        assert store is not None
        rows = await store.open_signals(limit=10)
        await self._reply(update, fmt.signals_report(rows, phase=self._deps.phase))

    async def _cmd_strategies(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Katalog strategi dan statusnya (PASAL 12.7, 12.15). Baca-saja."""
        from decimal import Decimal

        from aruna.learning.evidence import Evidence
        from aruna.learning.lifecycle import evaluate

        store = self._deps.adaptive
        assert store is not None
        katalog = await store.catalog_with_performance()
        if not katalog:
            await self._reply(
                update,
                "Katalog strategi masih kosong. ARUNA belajar sekali sehari; "
                "hasil pertamanya belum ada.",
            )
            return

        baseline = await store.overall_win_rate()
        baris = ["KATALOG STRATEGI", ""]
        if baseline is not None:
            baris += [f"Rata-rata ARUNA: {baseline:.0%}", ""]
        for s in katalog:
            bukti = Evidence(
                wins=int(s["wins"] or 0), losses=int(s["losses"] or 0)
            )
            baris.append(f"{s['code']} {s['name']} [{s['status']}]")
            baris.append(f"  {bukti.label()}")
            baris.append(f"  bersih {Decimal(str(s['net_pnl'] or 0)):.2f}")
            baris.append("")

        laporan = evaluate(katalog, baseline=baseline)
        if laporan.to_propose:
            baris.append("MENUNGGU KEPUTUSAN ANDA:")
            for a in laporan.to_propose:
                baris.append(f"  {a.code}: {a.current.value} -> {a.proposed.value}")
                baris.append(f"    {a.reason}")
        else:
            baris.append("Tidak ada yang menunggu keputusan Anda.")

        baris += [
            "",
            "ARUNA memasang label pengamatan sendiri. Menghentikan sebuah",
            "strategi adalah keputusan Anda (PASAL 12.20). Tidak ada yang",
            "pernah dihapus (PASAL 12.15).",
        ]
        await self._reply(update, "\n".join(baris))

    async def _cmd_learning(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Hasil putaran pembelajaran TERAKHIR (PASAL 12.24). Baca-saja.

        Membaca yang tersimpan; tidak menghitung ulang apa pun. Sebuah perintah
        chat yang memicu putaran pembelajaran akan menulis ratusan baris hasil
        setiap kali diketik, dan dua pesan beruntun menjalankan dua putaran
        yang saling menyalip.
        """
        from aruna.learning.adaptive import LEARNING_VERSION

        store = self._deps.adaptive
        assert store is not None
        pola = await store.notable_patterns(
            model_version=LEARNING_VERSION, limit=6
        )
        peristiwa = await store.recent_events(
            since=now_utc() - timedelta(days=7), limit=6
        )

        if not pola and not peristiwa:
            await self._reply(
                update,
                "Belum ada hasil pembelajaran tersimpan. ARUNA belajar sekali "
                "sehari; kalau ARUNA baru dinyalakan, tunggu putaran pertama.",
            )
            return

        baris = ["HASIL PEMBELAJARAN TERAKHIR", ""]
        if pola:
            baris.append("Pola bersample cukup:")
            for p in pola:
                rate = p.get("win_rate")
                persen = f"{float(rate):.0%}" if rate is not None else "-"
                baris.append(
                    f"  {p['pattern_key'][:44]}"
                )
                baris.append(
                    f"    {persen} menang, {p['sample_size']} sample "
                    f"({p['evidence']})"
                )
            baris.append("")
        if peristiwa:
            baris.append("Peristiwa pembelajaran:")
            for e in peristiwa:
                baris.append(f"  {e['event_type']}: {str(e['summary'])[:70]}")
            baris.append("")
        baris += [
            "ARUNA tidak mengubah modelnya sendiri. Setiap perubahan penting",
            "menunggu persetujuan Anda (PASAL 12.26).",
        ]
        await self._reply(update, "\n".join(baris))

    async def _cmd_today(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        store = self._deps.signals
        assert store is not None
        since = now_utc() - timedelta(hours=24)
        rows = await store.since(reference=since, limit=20)
        performance = await store.performance()
        await self._reply(
            update, fmt.today_report(rows, performance, phase=self._deps.phase)
        )

    async def _cmd_plans(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Recent futures plans, including the refusals (FUTURES SPEC 48).

        Reads, like every other command here. It does not run a council or call
        a venue: analysis is the scheduler's job and the CLI's, and a Telegram
        command that quietly issued new plans would make the record depend on
        who happened to send a message.
        """
        store = self._deps.futures
        assert store is not None
        rows = await store.recent(limit=10)
        counts = await store.counts_since(now_utc() - timedelta(hours=24))
        # What the plans came to, not only that they were made. Reporting the
        # tally without the outcomes describes a system that decides and never
        # finds out.
        outcomes = await store.outcome_counts()
        ghosts = await store.ghost_counts()
        await self._reply(
            update, fmt.futures_plans_report(rows, counts, outcomes, ghosts)
        )

    # ---- PHASE 8 performance and post-mortem -----------------------------

    def _performance(self, *, days: int | None, period: str):
        async def handler(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
            learning = self._deps.learning
            signals = self._deps.signals
            assert learning is not None and signals is not None

            if days is None:
                performance = await signals.performance()
                accuracy = await signals.accuracy()
            else:
                window = await learning.performance_window(days=days)
                performance = window
                accuracy = window

            await self._reply(
                update,
                fmt.performance_report(
                    performance,
                    accuracy,
                    await learning.latest_calibration(),
                    phase=self._deps.phase,
                    period=period,
                    reliability=await learning.latest_reliability(),
                ),
            )

        return handler

    async def _cmd_autopsy(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        learning = self._deps.learning
        assert learning is not None
        await self._reply(
            update,
            fmt.autopsy_report(
                await learning.autopsies(limit=5),
                await learning.ghosts(limit=5),
                phase=self._deps.phase,
            ),
        )

    # ---- PHASE 10 governance ---------------------------------------------

    async def _cmd_research(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        store = self._deps.governance
        assert store is not None
        await self._reply(
            update,
            fmt.research_report(
                await store.questions(limit=6), None, phase=self._deps.phase
            ),
        )

    async def _cmd_proposals(
        self, update: Update, _context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        store = self._deps.governance
        assert store is not None
        await self._reply(
            update,
            fmt.proposals_report(
                await store.proposals(limit=6),
                await store.decisions(limit=4),
                phase=self._deps.phase,
            ),
        )

    def _decide(self, action: str):
        """Approve or reject a proposal on the sender's authority.

        The Telegram identity *is* the named person: an approval recorded as
        "chat 12345" would defeat the point, so the username is required and a
        chat without one is refused rather than falling back to the id.
        """

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            store = self._deps.governance
            assert store is not None

            args = context.args or []
            if not args:
                await self._reply(
                    update, fmt.usage_error(action, "<proposal-key> [note]")
                )
                return

            user = update.effective_user
            actor = (user.username or user.full_name) if user else ""
            if not actor:
                await self._reply(
                    update,
                    "ARUNA - NO NAME\n\nThis chat has no username or display "
                    "name, so the decision could not be attributed to a person. "
                    "A model change must be approved by someone who can be "
                    "asked afterwards why (SPEC 44).",
                )
                return

            key, note = args[0], " ".join(args[1:])
            stored = await store.proposal(key)
            if stored is None:
                await self._reply(update, f"ARUNA\n\nNo proposal named {key}.")
                return

            from aruna.governance.proposal import ApprovalError

            try:
                decided = await self._apply_decision(action, stored, actor, note)
            except ApprovalError as exc:
                await self._reply(update, f"ARUNA - REFUSED\n\n{exc}")
                return

            await self._audit(
                actor=f"telegram:{actor}",
                action=f"PROPOSAL_{action.upper()}",
                entity_type="model_proposal",
                entity_id=key,
                after_state={"status": decided},
            )
            await self._reply(update, fmt.decision_recorded(key, decided, actor))

        return handler

    async def _apply_decision(
        self, action: str, stored: dict[str, Any], actor: str, note: str
    ) -> str:
        """Rebuild the proposal, decide it, and store the signed row."""
        from aruna.governance.service import GovernanceService

        service = GovernanceService(store=self._deps.governance)
        proposal = _proposal_from_row(stored)
        decided = (
            await service.approve(proposal, actor=actor, note=note)
            if action == "approve"
            else await service.reject(proposal, actor=actor, note=note)
        )
        return decided.status.value

    # ---- helpers -------------------------------------------------------

    async def _reply(self, update: Update, text: str) -> None:
        message = update.effective_message
        if message is None:
            return
        try:
            await message.reply_text(fmt.truncate(text), disable_web_page_preview=True)
        except PTBTelegramError as exc:
            log.warning("telegram.reply_failed", error=str(exc))

    async def _record_contact(self, update: Update, *, authorized: bool) -> None:
        if self._deps.subscribers is None:
            return
        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            return
        try:
            await self._deps.subscribers.touch(
                chat_id=str(chat.id),
                authorized=authorized,
                chat_type=chat.type,
                username=user.username if user else None,
                display_name=user.full_name if user else chat.title,
            )
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not block a command
            log.warning("telegram.subscriber_record_failed", error=str(exc))

    async def _audit(self, **kwargs: Any) -> None:
        if self._deps.audit is None:
            return
        try:
            await self._deps.audit.record(**kwargs)
        except Exception as exc:  # noqa: BLE001
            log.warning("telegram.audit_failed", error=str(exc))

    async def _within_rate_limit(self, chat_id: str) -> bool:
        """Token-bucket-ish counter in Redis.

        Fails **open**: with no Redis there is no counter, and the allowlist is
        the real access control.  Blocking an operator's ``/kill`` because the
        cache is down would be the worse failure.
        """
        cache = self._deps.cache
        if cache is None or not cache.available:
            return True
        count = await cache.incr("ratelimit", "telegram", chat_id, ttl_sec=RATE_LIMIT_WINDOW_SEC)
        if count is None:
            return True
        if count > RATE_LIMIT_COMMANDS:
            log.warning("telegram.rate_limited", chat_id=chat_id, count=count)
            return False
        return True


def _proposal_from_row(row: dict[str, Any]):
    """Rebuild a proposal from storage, validation included.

    The validation is rebuilt too, because the approval gate refuses anything
    whose evidence has not cleared the bar - and a proposal reconstructed
    without it would sail through on an empty verdict.
    """
    from aruna.governance.proposal import (
        Arm,
        ModelProposal,
        ProposalStatus,
        Validation,
        Verdict,
    )

    validation = None
    stored = row.get("validation")
    if stored:
        validation = Validation(
            verdict=Verdict(stored["verdict"]),
            baseline=Arm(label="baseline", **_arm(stored.get("baseline"))),
            variant=Arm(label="variant", **_arm(stored.get("variant"))),
            effect_points=float(stored.get("effect_points") or 0) / 100,
            sigma=float(stored.get("sigma") or 0),
            required_sigma=float(stored.get("required_sigma") or 2.0),
            variants_tested=int(stored.get("variants_tested") or 1),
            out_of_sample=bool(stored.get("out_of_sample")),
            reasons=tuple(stored.get("reasons") or ()),
        )

    return ModelProposal(
        key=row["proposal_key"],
        title=row["title"],
        hypothesis=row["hypothesis"],
        change=row["change_note"],
        question_key=row.get("question_key"),
        status=ProposalStatus(row["status"]),
        validation=validation,
    )


def _arm(stored: dict[str, Any] | None) -> dict[str, Any]:
    stored = stored or {}
    return {
        "resolved": int(stored.get("resolved") or 0),
        "correct": int(stored.get("correct") or 0),
        "net_pnl": str(stored.get("net_pnl") or "0"),
    }


__all__ = ["RATE_LIMIT_COMMANDS", "RATE_LIMIT_WINDOW_SEC", "BotDeps", "TelegramBot"]
