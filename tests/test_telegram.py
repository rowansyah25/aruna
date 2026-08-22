"""Telegram command registry and message formatting.

These tests never touch the network: they exercise the registry, the honesty of
the not-implemented responses, and the rendered text.
"""

from __future__ import annotations

import pytest
from tests.conftest import make_settings

from aruna.core.config import CURRENT_PHASE
from aruna.core.enums import HealthStatus
from aruna.core.runtime_state import RuntimeState
from aruna.health.models import ComponentHealth, HealthReport
from aruna.notify.telegram import formatting as fmt
from aruna.notify.telegram.registry import (
    PHASE_TITLES,
    build_registry,
    commands_by_phase,
)

#: Every command listed in SPEC 40.
SPEC_40_COMMANDS = {
    "start",
    "status",
    "crypto",
    "btc",
    "eth",
    "sol",
    "stocks",
    "bbca",
    "bbri",
    "bmri",
    "signals",
    "today",
    "performance",
    "weekly",
    "monthly",
    "council",
    "autopsy",
    "research",
    "proposals",
    "approve",
    "reject",
    "kill",
    "resume",
}

PHASE_1_COMMANDS = {"start", "help", "status", "health", "kill", "resume"}


class TestRegistry:
    def test_every_spec_40_command_is_registered(self) -> None:
        registry = build_registry()
        assert set(registry) >= SPEC_40_COMMANDS

    def test_phase_1_commands_have_handlers_once_bound(self) -> None:
        """The registry itself ships unbound; the bot binds PHASE 1 handlers."""
        registry = build_registry()
        assert all(registry[name].handler is None for name in PHASE_1_COMMANDS)

    def test_later_phase_commands_are_marked_unimplemented(self) -> None:
        registry = build_registry()
        for name, command in registry.items():
            if name not in PHASE_1_COMMANDS:
                assert command.implemented is False, name

    def test_every_command_declares_a_known_phase(self) -> None:
        for command in build_registry().values():
            assert command.phase in PHASE_TITLES

    def test_state_changing_commands_are_privileged(self) -> None:
        registry = build_registry()
        for name in ("kill", "resume", "approve", "reject"):
            assert registry[name].privileged is True

    def test_read_only_commands_are_not_privileged(self) -> None:
        registry = build_registry()
        for name in ("status", "help", "signals", "performance"):
            assert registry[name].privileged is False

    def test_grouping_by_phase_is_ordered(self) -> None:
        grouped = commands_by_phase(build_registry())
        assert list(grouped) == sorted(grouped)
        assert 1 in grouped

    def test_signals_waits_for_the_prediction_lock_phase(self) -> None:
        assert build_registry()["signals"].phase == 7

    def test_council_waits_for_the_council_phase(self) -> None:
        assert build_registry()["council"].phase == 6


class TestNoOverdueCommands:
    """A command whose phase has arrived must be bound, not still answering
    with 'NOT IMPLEMENTED'. This is the check that would have caught /council
    sitting unwired for a whole phase."""

    def _bot(self):
        from aruna.notify.telegram.bot import BotDeps, TelegramBot

        class Stub:
            pass

        return TelegramBot(
            BotDeps(
                settings=make_settings(),
                state=RuntimeState(),
                phase=CURRENT_PHASE,
                latest_health=lambda: None,
                market_data=Stub(),
                universe=Stub(),
                council=Stub(),
                signals=Stub(),
                learning=Stub(),
                governance=Stub(),
                futures=Stub(),
            )
        )

    def test_every_arrived_command_is_bound(self) -> None:
        unique = {id(c): c for c in self._bot().registry.values()}.values()
        overdue = [
            c.name for c in unique if c.phase <= CURRENT_PHASE and not c.implemented
        ]
        assert overdue == [], f"commands overdue for PHASE {CURRENT_PHASE}: {overdue}"

    def test_future_commands_stay_unbound(self) -> None:
        """The converse: nothing may claim to work before its phase."""
        unique = {id(c): c for c in self._bot().registry.values()}.values()
        premature = [
            c.name for c in unique if c.phase > CURRENT_PHASE and c.implemented
        ]
        assert premature == []


class TestCouncilReport:
    def _row(self, **overrides) -> dict:
        from datetime import UTC, datetime

        base = {
            "symbol": "BTC/USDT",
            "interval_code": "1h",
            "decision": "BUY",
            "confidence": 0.58,
            "participating_agents": 5,
            "total_agents": 9,
            "rounds_run": 4,
            "objection_count": 3,
            "correction_count": 1,
            "risk_level": "MODERATE",
            "veto_raised": 0,
            "veto_upheld": 0,
            "no_trade_reasons": [],
            "minority_prevailed": False,
            "as_of": datetime(2026, 8, 15, 1, 0, tzinfo=UTC),
        }
        return base | overrides

    def test_reports_the_verdict_and_the_debate(self) -> None:
        text = fmt.council_report([self._row()], phase=CURRENT_PHASE)
        assert "BUY  58%" in text
        assert "5/9" in text
        assert "3 keberatan, 1 diterima" in text

    def test_never_looks_like_a_signal(self) -> None:
        """SPEC 20's block has an entry, a target and a predicted move. Borrowing
        that layout here would imply a locked prediction that does not exist."""
        text = fmt.council_report([self._row()], phase=CURRENT_PHASE)
        assert "bukan signal" in text
        assert "penguncian adalah langkah terpisah" in text

        # Check the report body only. The footer legitimately names those
        # fields in order to disclaim them. ENTRY/TARGET stay English column
        # labels; a predicted move would now read as "prediksi".
        body = text.split("bukan signal")[0].upper()
        for forbidden in ("ENTRY", "TARGET", "PREDIKSI"):
            assert forbidden not in body

    def test_minority_verdict_is_surfaced(self) -> None:
        text = fmt.council_report(
            [self._row(minority_prevailed=True)], phase=CURRENT_PHASE
        )
        assert "MINORITAS MENANG" in text

    def test_rejected_veto_is_distinguished_from_upheld(self) -> None:
        rejected = fmt.council_report(
            [self._row(veto_raised=2, veto_upheld=0)], phase=CURRENT_PHASE
        )
        upheld = fmt.council_report(
            [self._row(veto_raised=2, veto_upheld=1)], phase=CURRENT_PHASE
        )
        assert "semua ditolak setelah ditinjau" in rejected
        assert "1 dikuatkan" in upheld

    def test_empty_history_says_so(self) -> None:
        text = fmt.council_report([], phase=CURRENT_PHASE)
        assert "Belum ada sesi council" in text

    def test_fits_one_telegram_message(self) -> None:
        rows = [self._row(symbol=f"SYM{i}/USDT") for i in range(8)]
        assert len(fmt.council_report(rows, phase=CURRENT_PHASE)) <= fmt.MAX_MESSAGE_LEN


class TestSignalReports:
    def _signal_row(self, **overrides) -> dict:
        from datetime import UTC, datetime
        from decimal import Decimal

        base = {
            "published": True,
            "withheld_reason": None,
            "signal_id": "abc0000000000001",
            "symbol": "BTC/USDT",
            "market_code": "CRYPTO",
            "horizon_code": "1h",
            "direction": "BUY",
            "confidence": Decimal("0.580"),
            "reference_price": Decimal(1000),
            "target_price": Decimal(1050),
            "expected_move_pct": Decimal("5.0"),
            "locked_at": datetime(2026, 1, 5, 4, 0, tzinfo=UTC),
            "resolves_at": datetime(2026, 1, 5, 5, 0, tzinfo=UTC),
            "status": "LOCKED",
        }
        return base | overrides

    def test_open_signals_are_listed_with_their_targets(self) -> None:
        text = fmt.signals_report([self._signal_row()], phase=CURRENT_PHASE)
        assert "BTC/USDT  1h  BUY  58%" in text
        assert "PAPER TRADING SAJA" in text
        assert "tidak pernah diedit" in text

    def test_a_missing_target_is_stated_not_omitted(self) -> None:
        text = fmt.signals_report(
            [self._signal_row(target_price=None, expected_move_pct=None)],
            phase=CURRENT_PHASE,
        )
        assert "TARGET:   TIDAK TERSEDIA" in text
        assert "ATR tidak bisa diukur" in text

    def test_no_open_signals_says_so_rather_than_showing_nothing(self) -> None:
        text = fmt.signals_report([], phase=CURRENT_PHASE)
        assert "Tidak ada prediksi terpublikasi yang masih terbuka" in text
        # "Nothing published" and "nothing considered" are different states,
        # and the empty screen must not conflate them.
        assert "/today" in text

    def test_a_withheld_call_is_shown_as_withheld_not_as_a_prediction(self) -> None:
        """The lock declined it. Listing it as a call ARUNA made would claim
        the opposite of what the system actually said."""
        text = fmt.today_report(
            [
                self._signal_row(
                    published=False,
                    withheld_reason="confidence 0.20 below the 0.35 lock floor",
                )
            ],
            {},
            phase=CURRENT_PHASE,
        )
        assert "DITAHAN:  confidence 0.20 below" in text
        assert "1 call berarah tidak dipublikasikan" in text

    def test_a_withheld_call_does_not_enter_the_accuracy_count(self) -> None:
        from decimal import Decimal

        rows = [
            self._signal_row(
                published=False,
                withheld_reason="stale evidence",
                outcome_class="WRONG_FROM_START",
                direction_correct=0,
                actual_move_pct=Decimal("-2.0"),
            ),
            self._signal_row(
                signal_id="abc0000000000002",
                outcome_class="TARGET_REACHED",
                direction_correct=1,
                actual_move_pct=Decimal("5.2"),
            ),
        ]
        text = fmt.today_report(rows, {}, phase=CURRENT_PHASE)
        # Two resolved, but only one was a claim ARUNA stood behind.
        assert "(1 posisi terpublikasi)" in text
        assert "BENAR:    1/1 (100%)" in text

    def test_today_lists_unresolved_calls_as_pending(self) -> None:
        text = fmt.today_report([self._signal_row()], {}, phase=CURRENT_PHASE)
        assert "PENDING" in text
        assert "belum ada call berarah yang resolve" in text

    def test_today_never_scores_a_wait_as_wrong(self) -> None:
        """A WAIT has no position, so it cannot be a wrong direction, and it
        must not drag down the count of what ARUNA actually called."""
        from decimal import Decimal

        rows = [
            self._signal_row(
                direction="WAIT",
                outcome_class="NO_POSITION",
                direction_correct=0,
                actual_move_pct=Decimal("1.5"),
            ),
            self._signal_row(
                signal_id="abc0000000000002",
                outcome_class="TARGET_REACHED",
                direction_correct=1,
                actual_move_pct=Decimal("5.2"),
            ),
        ]
        text = fmt.today_report(rows, {}, phase=CURRENT_PHASE)
        assert "TIDAK ADA POSISI" in text
        assert "BENAR:    1/1 (100%)" in text

    def test_today_reports_net_pnl_after_costs(self) -> None:
        from decimal import Decimal

        text = fmt.today_report(
            [self._signal_row()],
            {"trades": 3, "net": Decimal("-1500.00"), "costs": Decimal("4200.00")},
            phase=CURRENT_PHASE,
        )
        assert "PAPER TRADES: 3 ditutup" in text
        assert "(setelah biaya 4.200,00)" in text

    def test_reports_fit_one_telegram_message(self) -> None:
        rows = [self._signal_row(symbol=f"SYM{i}/USDT") for i in range(12)]
        assert len(fmt.signals_report(rows, phase=CURRENT_PHASE)) <= fmt.MAX_MESSAGE_LEN
        assert (
            len(fmt.today_report(rows, {}, phase=CURRENT_PHASE)) <= fmt.MAX_MESSAGE_LEN
        )


class TestPerformanceReports:
    def test_nothing_resolved_shows_no_figures_at_all(self) -> None:
        """The tempting failure is a 0% win rate over zero trades, which reads
        as a result rather than as an absence."""
        text = fmt.performance_report({}, {}, None, phase=CURRENT_PHASE)
        assert "Belum ada yang resolve di periode ini" in text
        assert "dihitung dari nol\nobservasi" in text
        assert "%" not in text.split("Belum ada yang resolve")[1].split("Jalankan:")[0]

    def test_the_sample_size_comes_before_the_win_rate(self) -> None:
        from decimal import Decimal

        text = fmt.performance_report(
            {"trades": 4, "wins": 3, "net": Decimal("1200.00"),
             "gross": Decimal("5000.00"), "costs": Decimal("3800.00")},
            {"resolved": 4, "correct": 3},
            None,
            phase=CURRENT_PHASE,
        )
        assert text.index("RESOLVE:") < text.index("BENAR:")
        assert "hitungan mentah" in text
        assert "bukan probabilitas\nterkalibrasi" in text

    def test_an_unmeasured_calibration_bucket_says_what_it_needs(self) -> None:
        calibration = {
            "verdict": "INSUFFICIENT SAMPLE: 4 resolved",
            "buckets": [
                {"bucket": "65-80%", "predictions": 4, "accuracy": None,
                 "mean_confidence": 0.7, "needs": 16},
            ],
        }
        text = fmt.performance_report(
            {}, {"resolved": 4, "correct": 3}, calibration, phase=CURRENT_PHASE
        )
        assert "butuh 16 lagi" in text
        assert "INSUFFICIENT SAMPLE" in text

    def test_a_tracked_but_unmeasured_agent_is_not_hidden(self) -> None:
        """"Tracked, not yet measurable" and "not tracked" are different states.

        Reliability was being measured and stored and shown to nobody.
        """
        text = fmt.performance_report(
            {},
            {"resolved": 4, "correct": 3},
            None,
            phase=CURRENT_PHASE,
            reliability=[
                {"agent": "TECHNICAL", "scored_opinions": 4, "accuracy": None,
                 "multiplier": None},
            ],
        )
        assert "1 agen dilacak" in text
        assert "cukup opini terskor" in text

    def test_a_measured_agent_shows_its_multiplier(self) -> None:
        text = fmt.performance_report(
            {},
            {"resolved": 40, "correct": 22},
            None,
            phase=CURRENT_PHASE,
            reliability=[
                {"agent": "TECHNICAL", "scored_opinions": 40, "accuracy": 0.55,
                 "multiplier": 1.05},
            ],
        )
        assert "TECHNICAL" in text
        assert "55% dari 40" in text
        assert "x1.05" in text

    def test_a_measured_bucket_shows_said_versus_was(self) -> None:
        calibration = {
            "verdict": "OVERCONFIDENT in 65-80%",
            "buckets": [
                {"bucket": "65-80%", "predictions": 25, "accuracy": 0.44,
                 "mean_confidence": 0.72, "needs": 0},
            ],
        }
        text = fmt.performance_report(
            {}, {"resolved": 25, "correct": 11}, calibration, phase=CURRENT_PHASE
        )
        assert "bilang 72%, ternyata 44%" in text
        assert "OVERCONFIDENT" in text


class TestAutopsyReport:
    def _autopsy_row(self, **overrides) -> dict:
        from decimal import Decimal

        base = {
            "symbol": "BTC/USDT",
            "horizon_code": "1h",
            "direction": "BUY",
            "confidence": Decimal("0.850"),
            "outcome_class": "WRONG_FROM_START",
            "actual_move_pct": Decimal("-3.20"),
            "max_adverse_pct": Decimal("-4.10"),
            "findings": ["the read was wrong when it was made"],
        }
        return base | overrides

    def test_an_empty_autopsy_is_not_presented_as_a_clean_record(self) -> None:
        text = fmt.autopsy_report([], [], phase=CURRENT_PHASE)
        assert "bukan klaim akurasi" in text
        assert "belum ada yang\nresolve" in text

    def test_a_loss_shows_its_findings(self) -> None:
        text = fmt.autopsy_report([self._autopsy_row()], [], phase=CURRENT_PHASE)
        assert "WRONG_FROM_START" in text
        assert "-3.20%" in text
        assert "the read was wrong when it was made" in text
        assert "tidak\nmengubah bobot apa pun" in text

    def test_ghost_signals_are_not_called_mistakes(self) -> None:
        from decimal import Decimal

        ghosts = [
            {
                "symbol": "ETH/USDT",
                "horizon_code": "1d",
                "missed_move_pct": Decimal("4.30"),
                "direction": "BUY",
            }
        ]
        text = fmt.autopsy_report([], ghosts, phase=CURRENT_PHASE)
        assert "+4.30%" in text
        assert "tidak otomatis kesalahan" in text

    def test_reports_fit_one_telegram_message(self) -> None:
        rows = [self._autopsy_row(symbol=f"SYM{i}/USDT") for i in range(10)]
        assert len(fmt.autopsy_report(rows, [], phase=CURRENT_PHASE)) <= fmt.MAX_MESSAGE_LEN


class TestUnavailableText:
    def test_names_the_phase_and_says_it_is_not_implemented(self) -> None:
        """SPEC 49: an unbuilt feature must never look like a working one."""
        text = build_registry()["signals"].unavailable_text(CURRENT_PHASE)

        assert "NOT IMPLEMENTED" in text
        assert "PHASE 7" in text
        assert f"PHASE {CURRENT_PHASE}" in text
        assert "prediction lock" in text

    def test_fits_in_one_telegram_message(self) -> None:
        for command in build_registry().values():
            text = command.unavailable_text(CURRENT_PHASE)
            assert len(text) <= fmt.MAX_MESSAGE_LEN


class TestFormatting:
    @pytest.fixture
    def report(self) -> HealthReport:
        return HealthReport(
            components=(
                ComponentHealth(
                    name="database", status=HealthStatus.UP, message="ok", latency_ms=3.2
                ),
                ComponentHealth(
                    name="redis", status=HealthStatus.DOWN, message="not connected"
                ),
                ComponentHealth(name="telegram", status=HealthStatus.DISABLED),
            )
        )

    def test_welcome_states_paper_mode_and_phase(self) -> None:
        text = fmt.welcome(make_settings(), CURRENT_PHASE, authorized=True)
        assert "PAPER TRADING SAJA" in text
        assert f"PHASE {CURRENT_PHASE}" in text
        assert "Chat ini: DIIZINKAN" in text

    def test_welcome_warns_an_unauthorized_chat(self) -> None:
        text = fmt.welcome(make_settings(), CURRENT_PHASE, authorized=False)
        assert "Chat ini: TIDAK DIIZINKAN" in text
        assert "ARUNA_TELEGRAM_CHAT_ID" in text

    def test_help_lists_availability_per_phase(self) -> None:
        text = fmt.help_text(build_registry(), CURRENT_PHASE)
        assert "TERSEDIA" in text
        assert "/status" in text
        assert f"PHASE {CURRENT_PHASE}" in text
        # A command from a phase that has not shipped must still be listed,
        # with the phase it waits for - never silently absent.
        assert "/proposals" in text

    def test_status_reports_components_and_kill_switch(self, report: HealthReport) -> None:
        text = fmt.status_summary(report, make_settings(), RuntimeState(), CURRENT_PHASE)
        assert "KESELURUHAN: DOWN" in text
        assert "database" in text
        assert "TRADING:   DIIZINKAN" in text

    async def test_status_shows_an_engaged_kill_switch(self, report: HealthReport) -> None:
        state = RuntimeState()
        await state.activate_kill_switch(reason="spread blowout", actor="telegram:1")
        text = fmt.status_summary(report, make_settings(), state, CURRENT_PHASE)

        assert "KILL SWITCH: AKTIF" in text
        assert "spread blowout" in text
        assert "TRADING:   DIBLOKIR (kill switch)" in text

    def test_status_without_a_sweep_says_so(self) -> None:
        text = fmt.status_summary(None, make_settings(), RuntimeState(), CURRENT_PHASE)
        assert "belum ada health sweep yang selesai" in text

    def test_health_detail_includes_latency(self, report: HealthReport) -> None:
        text = fmt.health_detail(report)
        assert "DATABASE" in text
        assert "3.2 ms" in text

    def test_health_alert_explains_the_no_signal_rule(self, report: HealthReport) -> None:
        text = fmt.health_alert(report, report.failing())
        assert "PERINGATAN HEALTH" in text
        assert "NO SIGNAL" in text

    def test_only_a_background_run_may_hold_the_bot(self) -> None:
        """Telegram allows one getUpdates consumer per token.

        `startup()` promised in its own docstring that ``background=False``
        starts no periodic loops, then started the poller regardless. Every
        short CLI command therefore stole the token from `aruna run` for the
        length of its own run, and announced "ARUNA ONLINE" then "Shutting
        down. No signals until restart." on the way past - so ARUNA looked
        like it kept dying when it was a `plan` command finishing normally.

        Asserted against the source because constructing a real application
        needs a database; what matters is that the call is guarded at all.
        """
        import inspect

        from aruna import app as app_module

        source = inspect.getsource(app_module.ArunaApplication.startup)
        start = source.index("_start_ingestion")
        guard = source.index("if background:", start)
        telegram = source.index("_start_telegram", start)
        assert guard < telegram, "the Telegram poller is started unguarded"

    @pytest.mark.asyncio
    async def test_a_recovery_is_actually_delivered(self) -> None:
        """The defect this replaces: DOWN went out, DOWN->UP never did.

        The startup-burst guard was a status test - "every changed component is
        operational" - which is exactly what a recovery looks like. So MySQL
        falling over at 03:00 was announced and MySQL returning at 03:02 was
        not, for the life of the process. The operator's last word about a
        component was permanently its failure.

        Asserting the formatter alone would have passed against the old code;
        only driving the app-level hook catches it.
        """
        from aruna.app import ArunaApplication
        from aruna.health.alerts import HealthAlertPolicy

        app = ArunaApplication.__new__(ArunaApplication)
        app.settings = make_settings()
        app._health_alerted = False
        # Kebijakan PASAL 17/18 menyimpan riwayat antar-panggilan. Ia diisi di
        # sini, bukan dibuat otomatis saat diakses: sebuah kebijakan yang lahir
        # baru pada tiap panggilan kehilangan seluruh riwayat penindasannya,
        # dan banjir alert-nya kembali persis seperti semula - tanpa satu pun
        # test merah.
        app._alert_policy = HealthAlertPolicy()

        sent: list[str] = []

        class _Bot:
            started = True

            async def send(self, text: str) -> None:
                sent.append(text)

        class _Cache:
            async def set_json(self, *_a, **_k) -> None:
                return None

        app.bot = _Bot()
        app.cache = _Cache()

        down = (
            ComponentHealth(
                name="database", status=HealthStatus.DOWN, message="gone"
            ),
        )
        up = (
            ComponentHealth(
                name="database", status=HealthStatus.UP, message="reconnected"
            ),
        )

        # First sweep: everything operational, and this one IS startup noise.
        await app._on_health_change(HealthReport(components=up), up)
        assert sent == []

        await app._on_health_change(HealthReport(components=down), down)
        await app._on_health_change(HealthReport(components=up), up)

        assert len(sent) == 2, "the recovery was swallowed"
        assert "PERINGATAN HEALTH" in sent[0]
        assert "HEALTH PULIH" in sent[1]

    def test_a_recovery_is_not_headed_alert(self) -> None:
        """Recoveries reach this function now that they are no longer swallowed.

        "HEALTH ALERT: database UP" is its own small dishonesty - the reader
        scans the header, not the body.
        """
        recovered = (
            ComponentHealth(
                name="database", status=HealthStatus.UP, message="reconnected"
            ),
        )
        text = fmt.health_alert(HealthReport(components=recovered), recovered)
        assert "HEALTH PULIH" in text
        assert "PERINGATAN HEALTH" not in text
        assert "NO SIGNAL" not in text

    def test_kill_and_resume_messages_name_the_actor(self) -> None:
        assert "telegram:1" in fmt.kill_switch_engaged("reason", "telegram:1")
        assert "telegram:1" in fmt.kill_switch_released("telegram:1")

    def test_kill_message_says_outcome_tracking_continues(self) -> None:
        """Stopping outcome recording would corrupt the evidence record."""
        text = fmt.kill_switch_engaged("r", "a")
        assert "tetap dilacak" in text
        assert "pencatatan\noutcome adalah bukti" in text

    def test_unauthorized_message_names_the_chat(self) -> None:
        text = fmt.unauthorized("999")
        assert "999" in text
        assert "audit log" in text

    def test_truncation_respects_the_telegram_limit(self) -> None:
        text = fmt.truncate("x" * 6000)
        assert len(text) <= fmt.MAX_MESSAGE_LEN
        assert text.endswith("(dipotong)")

    def test_short_text_is_untouched(self) -> None:
        assert fmt.truncate("hello") == "hello"

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [(45, "45s"), (125, "2m 5s"), (7_200, "2h 0m"), (90_000, "1d 1h 0m")],
    )
    def test_duration_rendering(self, seconds: int, expected: str) -> None:
        assert fmt._duration(seconds) == expected

    def test_every_message_fits_one_telegram_message(self, report: HealthReport) -> None:
        messages = [
            fmt.welcome(make_settings(), CURRENT_PHASE, authorized=True),
            fmt.help_text(build_registry(), CURRENT_PHASE),
            fmt.status_summary(report, make_settings(), RuntimeState(), CURRENT_PHASE),
            fmt.health_detail(report),
            fmt.health_alert(report, report.failing()),
        ]
        for message in messages:
            assert len(message) <= fmt.MAX_MESSAGE_LEN
