"""Domain vocabulary."""

from __future__ import annotations

from datetime import timedelta

import pytest

from aruna.core.enums import (
    CRYPTO_HORIZONS,
    HORIZONS_BY_MODE,
    IDX_INVESTMENT_HORIZONS,
    IDX_TRADING_HORIZONS,
    UNRESTRICTED_AGENT_DECISIONS,
    AgentRole,
    AnalysisMode,
    CouncilRound,
    DataQuality,
    Decision,
    HealthStatus,
    Horizon,
    Market,
    Regime,
    parse_market,
)


class TestMarket:
    def test_exactly_three_markets_exist(self) -> None:
        """FOREX joined on 2026-08-27 for XAUUSD; nothing else did."""
        assert {m.value for m in Market} == {"CRYPTO", "IDX", "FOREX"}

    @pytest.mark.parametrize("value", ["fx", "Currency", "FOREIGN_EXCHANGE"])
    def test_forex_aliases_are_rejected_with_an_explanation(self, value: str) -> None:
        """Only the canonical spelling is legal; the aliases stay shut."""
        with pytest.raises(ValueError, match="write FOREX"):
            parse_market(value)

    def test_parsing_is_case_and_space_insensitive(self) -> None:
        assert parse_market("  crypto ") is Market.CRYPTO


class TestHorizon:
    def test_minutes_and_months_have_distinct_values(self) -> None:
        """The spec writes '1M' for both; storing them identically would make
        outcome records ambiguous."""
        assert Horizon.M1.value == "1m"
        assert Horizon.MO1.value == "1mo"
        assert Horizon.M1.value != Horizon.MO1.value

    @pytest.mark.parametrize(
        ("horizon", "expected"),
        [
            (Horizon.M5, timedelta(minutes=5)),
            (Horizon.H4, timedelta(hours=4)),
            (Horizon.D3, timedelta(days=3)),
            (Horizon.W1, timedelta(weeks=1)),
            (Horizon.MO3, timedelta(days=90)),
            (Horizon.Y1, timedelta(days=365)),
        ],
    )
    def test_durations(self, horizon: Horizon, expected: timedelta) -> None:
        assert horizon.duration == expected

    def test_every_horizon_has_a_duration_and_label(self) -> None:
        for horizon in Horizon:
            assert horizon.duration.total_seconds() > 0
            assert horizon.label

    def test_labels_disambiguate_minutes_from_months(self) -> None:
        assert Horizon.M1.label == "1 minute"
        assert Horizon.MO1.label == "1 month"

    def test_spec_horizon_sets(self) -> None:
        assert len(CRYPTO_HORIZONS) == 10
        assert IDX_TRADING_HORIZONS == (Horizon.D1, Horizon.D3, Horizon.D5, Horizon.W1)
        assert Horizon.MO1 in IDX_INVESTMENT_HORIZONS

    def test_every_mode_maps_to_horizons_of_its_own_market(self) -> None:
        for mode, horizons in HORIZONS_BY_MODE.items():
            assert horizons, f"{mode} has no horizons"
            if mode.market is Market.CRYPTO:
                assert all(h in CRYPTO_HORIZONS for h in horizons)
            else:
                allowed = IDX_TRADING_HORIZONS + IDX_INVESTMENT_HORIZONS
                assert all(h in allowed for h in horizons)


class TestAnalysisMode:
    def test_modes_are_partitioned_by_market(self) -> None:
        """SPEC 31: trading and investment scoring must not be mixed."""
        crypto = {m for m in AnalysisMode if m.market is Market.CRYPTO}
        idx = {m for m in AnalysisMode if m.market is Market.IDX}
        assert len(crypto) == 4
        assert idx == {AnalysisMode.IDX_TRADING, AnalysisMode.IDX_INVESTMENT}
        assert not crypto & idx


class TestCouncil:
    def test_full_roster_is_present(self) -> None:
        assert len(AgentRole) == 12
        assert AgentRole.PROSECUTOR in AgentRole
        assert AgentRole.COUNCIL_JUDGE in AgentRole

    def test_every_agent_may_reach_any_directional_verdict(self) -> None:
        """SPEC 12/48: no agent is permanently BUY, SELL, or opposition."""
        assert {Decision.BUY, Decision.SELL, Decision.WAIT} == UNRESTRICTED_AGENT_DECISIONS

    def test_four_protest_rounds(self) -> None:
        assert len(CouncilRound) == 4


class TestDecision:
    def test_directional_flags(self) -> None:
        assert Decision.BUY.is_directional
        assert Decision.SELL.is_directional
        assert not Decision.WAIT.is_directional
        assert not Decision.NO_SIGNAL.is_directional

    def test_spec_48_vocabulary_is_complete(self) -> None:
        assert {d.value for d in Decision} == {
            "BUY",
            "SELL",
            "WAIT",
            "NO_SIGNAL",
            "UNKNOWN_MARKET",
        }


class TestDataQuality:
    def test_only_ok_permits_a_signal(self) -> None:
        assert DataQuality.OK.blocks_signal is False
        for quality in DataQuality:
            if quality is not DataQuality.OK:
                assert quality.blocks_signal is True


class TestHealthStatus:
    def test_severity_order(self) -> None:
        assert HealthStatus.DOWN.severity > HealthStatus.DEGRADED.severity
        assert HealthStatus.DEGRADED.severity > HealthStatus.UNKNOWN.severity
        assert HealthStatus.UNKNOWN.severity > HealthStatus.UP.severity

    def test_operational_states(self) -> None:
        assert HealthStatus.UP.is_operational
        assert HealthStatus.DISABLED.is_operational
        assert not HealthStatus.DEGRADED.is_operational
        assert not HealthStatus.DOWN.is_operational


class TestRegime:
    def test_spec_9_regimes_are_all_present(self) -> None:
        """SPEC 9 ditambah taksonomi berarah bagian 2 Phase 15.

        ``TRENDING_BULLISH``/``TRENDING_BEARISH``/``BREAKDOWN`` masuk pada
        2026-08-21: arahnya sudah diketahui classifier dan dulu dibuang, dan
        di regime ``TRENDING`` BUY menang 49,8% sementara SELL menang 13,8% -
        satu ember yang menampung keduanya membuat bobot agent per-regime tidak
        bisa membedakannya.

        ``TRENDING`` tetap di sini meski classifier tidak lagi menghasilkannya:
        puluhan ribu baris tersimpan memuatnya, dan membuangnya dari enum
        membuat setiap pembacaan baris lama meledak.

        Himpunan yang PERSIS, bukan subset: anggota baru harus membuat test ini
        gagal keras supaya keputusannya disengaja.
        """
        expected = {
            "TRENDING",
            "TRENDING_BULLISH",
            "TRENDING_BEARISH",
            "RANGING",
            "BREAKOUT",
            "BREAKDOWN",
            "REVERSAL",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
            "NEWS_SHOCK",
            "ACCUMULATION",
            "DISTRIBUTION",
            "UNCERTAIN",
            "ANOMALY",
        }
        assert {r.value for r in Regime} == expected

    def test_sembilan_regime_bagian_2_ada(self) -> None:
        """Bagian 2 Phase 15 mengeja sembilan yang wajib."""
        wajib = {
            "TRENDING_BULLISH", "TRENDING_BEARISH", "RANGING",
            "HIGH_VOLATILITY", "LOW_VOLATILITY", "BREAKOUT", "BREAKDOWN",
            "REVERSAL", "UNCERTAIN",
        }
        assert wajib <= {r.value for r in Regime}
