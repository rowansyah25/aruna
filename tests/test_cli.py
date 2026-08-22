"""CLI wiring."""

from __future__ import annotations

import pytest

from aruna.cli import build_parser, cmd_version


class TestParser:
    @pytest.mark.parametrize(
        "command",
        [
            "version",
            "config",
            "doctor",
            "createdb",
            "migrate",
            "seed",
            "signal",
            "health",
            "run",
        ],
    )
    def test_every_command_is_wired_to_a_function(self, command: str) -> None:
        args = build_parser().parse_args([command])
        assert callable(args.func)

    def test_futures_takes_a_symbol_and_a_size(self) -> None:
        args = build_parser().parse_args(["futures", "BTCUSDT", "--notional", "5000"])
        assert callable(args.func)
        assert args.symbol == "BTCUSDT"
        assert args.notional == 5000.0

    def test_plan_takes_symbols_and_sizing_inputs(self) -> None:
        args = build_parser().parse_args(
            ["plan", "BTCUSDT,ETHUSDT", "--horizon", "1h", "--equity", "5000"]
        )
        assert callable(args.func)
        assert args.symbols == "BTCUSDT,ETHUSDT"
        assert args.horizon == "1h"
        assert args.equity == 5000.0
        assert args.risk is None  # the risk engine's default, not a CLI guess

    def test_futures_requires_a_symbol(self) -> None:
        """There is no default symbol: guessing which perpetual the operator
        meant is exactly the kind of substitution FUTURES SPEC 52 forbids."""
        with pytest.raises(SystemExit):
            build_parser().parse_args(["futures"])

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_unknown_command_exits(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["definitely-not-a-command"])

    def test_migrate_flags(self) -> None:
        args = build_parser().parse_args(["migrate", "--status", "--dry-run"])
        assert args.status is True
        assert args.dry_run is True

    def test_migrate_defaults_to_applying(self) -> None:
        args = build_parser().parse_args(["migrate"])
        assert args.status is False
        assert args.dry_run is False

    def test_seed_accepts_a_universe_file(self) -> None:
        args = build_parser().parse_args(["seed", "--file", "config/universe.json"])
        assert args.file == "config/universe.json"

    def test_signal_defaults_to_locking_without_resolving(self) -> None:
        args = build_parser().parse_args(["signal"])
        assert callable(args.func)
        assert args.resolve is False
        assert args.resolve_only is False
        assert args.horizons is None  # the SPEC 10 default set is chosen at runtime

    def test_signal_can_score_without_locking(self) -> None:
        args = build_parser().parse_args(
            ["signal", "--resolve-only", "--limit", "5", "--horizons", "15m,1h"]
        )
        assert args.resolve_only is True
        assert args.limit == 5
        assert args.horizons == "15m,1h"


class TestExitCodes:
    def test_a_bad_interval_reports_the_message_not_a_traceback(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from aruna.cli import main

        assert main(["analyze", "--intervals", "17q"]) == 1
        assert "unknown interval '17q'" in capsys.readouterr().err

    def test_argparse_exits_with_its_own_code(self) -> None:
        """Argument parsing happens before the try block, so argparse keeps its
        conventional exit 2 and its own usage message."""
        from aruna.cli import main

        with pytest.raises(SystemExit) as exc:
            main(["definitely-not-a-command"])
        assert exc.value.code == 2


class TestFuturesCommand:
    """The command must degrade honestly, because reachability is not a
    permanent property of the venue - it depends on the network it is run
    from, and the same binary has to be right either way."""

    def test_an_unreachable_venue_reports_it_and_fails(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from aruna.cli import main
        from aruna.data.provider import ProviderStatus
        from aruna.futures.binance import BinanceFuturesProvider

        async def blocked(self: BinanceFuturesProvider) -> ProviderStatus:
            return ProviderStatus(reachable=False, detail="DNS hijacked to a filter")

        monkeypatch.setattr(BinanceFuturesProvider, "probe", blocked)

        assert main(["futures", "BTCUSDT"]) == 1
        out = capsys.readouterr().out
        assert "STATUS: DATA SOURCE UNAVAILABLE" in out
        # No price is invented to fill the gap.
        assert "mark price" not in out


class TestVersion:
    def test_version_command_succeeds(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cmd_version(build_parser().parse_args(["version"])) == 0
        assert "ARUNA AI" in capsys.readouterr().out
