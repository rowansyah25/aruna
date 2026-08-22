"""The full ``/command`` surface from SPEC 40, with the phase each one needs.

Every command in the specification is registered from day one, but a command
whose subsystem does not exist yet answers with what it is waiting for instead
of a plausible-looking blank result.  SPEC 49 is explicit that an unbuilt
feature must not be presented as working, and a bot that silently ignores
``/signals`` is indistinguishable from one that has no signals to report.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

#: What each phase delivers, used to explain why a command is unavailable.
PHASE_TITLES: dict[int, str] = {
    1: "core infrastructure, database, config, logging, Telegram, health monitor",
    2: "crypto and IDX market data providers",
    3: "technical, structure, momentum, volume, volatility, regime",
    4: "news, fundamental, correlation",
    5: "AI agents, self-critic, prosecutor, risk, no-trade engine",
    6: "council, cross-protest, rebuttal, veto, judge",
    7: "prediction lock, paper trading, multi-horizon, outcome engine",
    8: "loss autopsy, counterfactual, ghost signal, calibration",
    9: "backtest, walk-forward, out-of-sample, decision replay",
    10: "shadow model, drift, experiments, human approval",
    12: "pembelajaran adaptif: pola, strategi, spesialisasi agent",
}

Handler = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class Command:
    name: str
    phase: int
    summary: str
    #: Bound at startup for implemented commands; None means "not built yet".
    handler: Handler | None = None
    #: Commands that change system state require an authorized chat.
    privileged: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def implemented(self) -> bool:
        return self.handler is not None

    @property
    def phase_title(self) -> str:
        return PHASE_TITLES.get(self.phase, "a later phase")

    def unavailable_text(self, current_phase: int) -> str:
        return (
            f"/{self.name} - NOT IMPLEMENTED\n\n"
            f"{self.summary}\n\n"
            f"Scheduled for PHASE {self.phase}: {self.phase_title}.\n"
            f"This build is PHASE {current_phase}.\n\n"
            "STATUS: FEATURE NOT AVAILABLE"
        )


def build_registry() -> dict[str, Command]:
    """Every command from SPEC 40, plus /help."""
    commands = [
        # ---- PHASE 1: operational ---------------------------------------
        Command("start", 1, "Register this chat and show what ARUNA can do now."),
        Command("help", 1, "List every command and the phase it becomes available."),
        Command("status", 1, "System status: components, uptime, kill switch, config."),
        Command("health", 1, "Full health report with per-component latency."),
        Command(
            "kill",
            1,
            "Engage the kill switch. ARUNA stops producing signals until /resume.",
            privileged=True,
        ),
        Command("resume", 1, "Release the kill switch.", privileged=True),
        # ---- PHASE 2: market data ---------------------------------------
        Command("crypto", 2, "Crypto market overview across the configured universe."),
        Command("btc", 2, "BTC/USDT snapshot."),
        Command("eth", 2, "ETH/USDT snapshot."),
        Command("sol", 2, "SOL/USDT snapshot."),
        Command("stocks", 2, "IDX market overview across the configured universe."),
        Command("bbca", 2, "BBCA snapshot."),
        Command("bbri", 2, "BBRI snapshot."),
        Command("bmri", 2, "BMRI snapshot."),
        # ---- PHASE 6: council --------------------------------------------
        Command("council", 6, "Latest council session: decisions, protests, veto, judge."),
        # ---- PHASE 7: signals and paper trading --------------------------
        Command("signals", 7, "Open locked predictions and their horizons."),
        Command("today", 7, "Everything ARUNA signalled today, with outcomes so far."),
        # ---- PHASE 8: performance and post-mortem ------------------------
        Command("performance", 8, "Win rate, expectancy, profit factor, calibration."),
        Command("weekly", 8, "Weekly performance report."),
        Command("monthly", 8, "Monthly performance report."),
        Command("autopsy", 8, "Loss autopsies and successful-objection analysis."),
        # ---- PHASE 12: pembelajaran adaptif ------------------------------
        #
        # Keduanya BACA-SAJA. Tidak ada perintah yang memicu putaran
        # pembelajaran: ia menulis ratusan baris hasil, dan sebuah pesan chat
        # yang memicu siklus tulis bisa datang dua kali beruntun. Putarannya
        # berjalan sendiri sekali sehari di loop upkeep (PASAL 12.27).
        Command("strategies", 12, "Katalog strategi, statusnya, dan hasilnya."),
        Command("learning", 12, "Hasil putaran pembelajaran terakhir."),
        # ---- FUTURES F5: perpetual plans ---------------------------------
        Command(
            "plans",
            10,
            "Recent futures plans and the refusals, with today's tally.",
            aliases=("futures",),
        ),
        # ---- PHASE 10: model governance ----------------------------------
        Command("research", 10, "Research questions ARUNA has raised from its own losses."),
        Command("proposals", 10, "Model change proposals awaiting human approval."),
        Command("approve", 10, "Approve a validated model proposal.", privileged=True),
        Command("reject", 10, "Reject a model proposal.", privileged=True),
    ]
    registry: dict[str, Command] = {}
    for command in commands:
        registry[command.name] = command
        for alias in command.aliases:
            registry[alias] = command
    return registry


def commands_by_phase(registry: dict[str, Command]) -> dict[int, list[Command]]:
    grouped: dict[int, list[Command]] = {}
    for command in _unique(registry):
        grouped.setdefault(command.phase, []).append(command)
    for items in grouped.values():
        items.sort(key=lambda c: c.name)
    return dict(sorted(grouped.items()))


def _unique(registry: dict[str, Command]) -> list[Command]:
    seen: dict[int, Command] = {}
    for command in registry.values():
        seen.setdefault(id(command), command)
    return list(seen.values())


__all__ = ["PHASE_TITLES", "Command", "Handler", "build_registry", "commands_by_phase"]
