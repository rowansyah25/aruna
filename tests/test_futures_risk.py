"""Position size, stops, liquidation and economics (FUTURES SPEC 19-26).

Four structural guarantees carry this file. Each blocks a specific way a
leveraged system talks itself into a bad position:

* the stop engine is never shown a target, so it cannot be moved to make a
  ratio look good (SPEC 21);
* position size is never shown leverage or a profit goal, so it cannot be
  inflated toward either (SPEC 19, 16);
* liquidation is computed from the venue's published formula or not at all,
  never approximated (SPEC 24);
* a liquidation that sits in front of the stop scores zero, because the stop is
  then decorative and every risk number derived from it is fiction (SPEC 25).
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from aruna.futures import liquidation as liquidation_module
from aruna.futures import risk as risk_module
from aruna.futures import stops as stops_module
from aruna.futures.liquidation import (
    HEALTHY_STOP_MULTIPLE,
    CascadeRisk,
    buffer_score,
    detect_cascade,
    liquidation_price,
)
from aruna.futures.models import (
    ContractSpec,
    InstrumentType,
    LiquidationEvent,
    MarginMode,
    PositionSide,
)
from aruna.futures.risk import (
    MAX_RISK_PCT,
    MIN_NET_REWARD_RATIO,
    position_size,
    trade_economics,
)
from aruna.futures.stops import stop_loss, take_profit

ENTRY = Decimal(50_000)
ATR = Decimal(500)
EQUITY = Decimal(10_000)


def _contract(**overrides) -> ContractSpec:
    base = {
        "symbol": "BTCUSDT",
        "instrument": InstrumentType.PERPETUAL,
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "tick_size": Decimal("0.10"),
        "step_size": Decimal("0.001"),
        "min_notional": Decimal(5),
        "max_leverage": 125,
        "maintenance_margin_rate": Decimal("0.004"),
        "margin_brackets": (
            (Decimal(0), Decimal("0.004"), Decimal(0)),
            (Decimal(50_000), Decimal("0.005"), Decimal(50)),
        ),
    }
    return ContractSpec(**(base | overrides))


# ---------------------------------------------------------------------------
# FUTURES SPEC 21 - the stop cannot be tuned
# ---------------------------------------------------------------------------


class TestStopLoss:
    def test_the_engine_is_never_shown_a_target_or_a_ratio(self) -> None:
        """Structural: it cannot be tuned toward a number it never receives."""
        signature = inspect.signature(stop_loss)
        for banned in ("target", "reward", "rr", "ratio", "equity", "leverage"):
            assert banned not in signature.parameters

    def test_a_structural_stop_sits_beyond_the_level(self) -> None:
        # Satu ATR di bawah entry. Dulu dua ATR, dan sejak
        # `JANGKAUAN_HORIZON_ATR` masuk (2026-08-26) jarak itu ditambah padding
        # melewati apa yang satu horizon tempuh, jadi levelnya akan dibuang -
        # dan test ini akan menguji jalur volatilitas alih-alih jalur struktur
        # yang jadi pokoknya.
        swing = ENTRY - ATR
        stop = stop_loss(
            entry=ENTRY, side=PositionSide.LONG, atr=ATR, invalidation_level=swing
        )
        assert stop is not None
        assert stop.price < swing  # beyond, so the test of the level survives
        assert stop.from_volatility_only is False
        assert "level yang harus bertahan" in stop.invalidation

    def test_a_short_stop_sits_above_the_level(self) -> None:
        swing = ENTRY + ATR  # cermin dari test di atas, di dalam jangkauan
        stop = stop_loss(
            entry=ENTRY, side=PositionSide.SHORT, atr=ATR, invalidation_level=swing
        )
        assert stop is not None
        assert stop.price > swing

    def test_no_structure_falls_back_to_volatility_and_says_so(self) -> None:
        stop = stop_loss(entry=ENTRY, side=PositionSide.LONG, atr=ATR)
        assert stop is not None
        assert stop.from_volatility_only is True
        assert any("stop yang lebih lemah" in f for f in stop.findings)

    def test_a_level_on_the_wrong_side_of_entry_is_ignored(self) -> None:
        stop = stop_loss(
            entry=ENTRY,
            side=PositionSide.LONG,
            atr=ATR,
            invalidation_level=Decimal(51_000),  # above a long entry
        )
        assert stop is not None
        assert stop.from_volatility_only is True
        assert any("sisi entry yang salah" in f for f in stop.findings)

    def test_a_stop_inside_the_noise_is_flagged(self) -> None:
        """A level almost at entry gives a stop barely past the padding."""
        stop = stop_loss(
            entry=ENTRY,
            side=PositionSide.LONG,
            atr=ATR,
            invalidation_level=ENTRY - Decimal(10),
        )
        assert stop is not None
        assert any("masih di dalam noise" in f for f in stop.findings)

    def test_the_noise_threshold_can_actually_fire(self) -> None:
        """It sits above the padding on purpose: a structural stop is always
        at least ATR_PADDING away, so a threshold at or below the padding would
        be a check that can never trigger."""
        from aruna.futures.stops import ATR_PADDING, MIN_ATR_DISTANCE

        assert MIN_ATR_DISTANCE > ATR_PADDING

    def test_rounding_never_flatters_the_stop(self) -> None:
        """A stop rounded toward entry is tighter than intended."""
        contract = _contract(tick_size=Decimal(100))
        # 49.530 ada 0,94 ATR di bawah entry - di dalam jangkauan horizon, jadi
        # jalur strukturnya yang diuji. Stop sebelum pembulatan 49.280; kalau
        # dibulatkan ke ATAS ia jadi 49.300 dan lebih ketat daripada yang
        # dimaksud, dan itu yang test ini larang.
        stop = stop_loss(
            entry=ENTRY,
            side=PositionSide.LONG,
            atr=ATR,
            invalidation_level=Decimal(49_530),
            contract=contract,
        )
        assert stop is not None
        assert stop.price % Decimal(100) == 0
        assert stop.price <= Decimal(49_280)


class TestTargetsHaveAFloorNotJustACeiling:
    """Found by asking why no plan had ever cleared on live data.

    `take_profit` dropped levels beyond 6 ATR - "a target the market reaches
    once a quarter is not a plan" - and accepted a level a third of an ATR
    away. On live BTCUSDT a 4.85-ATR structural stop was paired with a
    0.31-ATR structural target, giving a gross R:R of 0.06, and the plan was
    refused for "reward too small" when the target had never been one.
    """

    ENTRY = Decimal(63000)
    ATR = Decimal(252)

    def test_a_target_inside_the_noise_is_dropped(self) -> None:
        from aruna.futures.stops import MIN_TARGET_ATR, take_profit

        noise = self.ENTRY + self.ATR * Decimal("0.3")
        profit = take_profit(
            entry=self.ENTRY,
            side=PositionSide.LONG,
            atr=self.ATR,
            structure_levels=(noise,),
        )
        assert noise not in profit.levels
        assert any("di dalam" in f and "ATR dibuang" in f for f in profit.findings)
        # And the ATR fallback takes over rather than leaving no target.
        assert profit.first is not None
        assert abs(profit.first - self.ENTRY) >= self.ATR * MIN_TARGET_ATR

    def test_a_level_beyond_the_floor_is_kept(self) -> None:
        from aruna.futures.stops import take_profit

        real = self.ENTRY + self.ATR * Decimal("2.0")
        profit = take_profit(
            entry=self.ENTRY,
            side=PositionSide.LONG,
            atr=self.ATR,
            structure_levels=(real,),
        )
        assert profit.first is not None
        assert abs(profit.first - real) < self.ATR  # kept, modulo tick rounding

    def test_the_floor_matches_the_fallbacks_nearest_rung(self) -> None:
        """A structural level closer than the fallback's own first step is a
        weaker basis than the fallback, so preferring it is backwards.

        **Yang dikunci sekarang HUBUNGANNYA, bukan literalnya.** Versi lama
        menuliskan `Decimal("1.0") == MIN_TARGET_ATR` - dan angka yang sama
        hidup kedua kalinya sebagai tuple inline di dalam `take_profit`. Dua
        literal yang menyatakan satu invarian bebas melenceng, dan gejalanya
        bukan galat: level struktur di bawah lantai dibuang, lalu jarak yang
        sama persis ditawarkan kembali sebagai fallback, membatalkan lantainya
        sendiri lewat pintu belakang. Sekarang tangganya diturunkan dari
        lantainya, jadi keduanya tidak bisa lagi berselisih.
        """
        from aruna.futures.stops import MIN_TARGET_ATR, TANGGA_FALLBACK

        assert TANGGA_FALLBACK[0] == MIN_TARGET_ATR
        assert list(TANGGA_FALLBACK) == sorted(TANGGA_FALLBACK), (
            "tangga target harus menaik - anak tangga yang lebih dekat "
            "daripada pendahulunya bukan tangga"
        )

    def test_target_satu_atr_ditolak_karena_terukur_terburuk(self) -> None:
        """**Angka lantainya sendiri, bukan hanya hubungannya.**

        Test di atas mengunci "tangga dimulai di lantai", dan itu bertahan di
        nilai berapa pun - jadi ia tidak menjaga apa pun soal LETAK lantainya.
        Cabut-uji membuktikannya: mengembalikan lantai ke satu ATR membiarkan
        seluruh berkas ini hijau.

        Yang dikunci di sini hasil pengukurannya. Nilai harapan per trade dalam
        satuan R, pada bar yang council pilih BUY (n=1.086), stop dipegang tetap
        di 1,5 ATR sehingga ukuran posisi tidak ikut berubah::

            target 1,0 ATR  +0,025   <- terburuk di seluruh tangga
            target 2,0 ATR  +0,042
            target 3,0 ATR  +0,048   <- puncak, lalu datar
            target 6,0 ATR  +0,047

        Satu ATR adalah letak target TERBURUK yang terukur. Sebuah level struktur
        sejauh itu harus dibuang, bukan dipakai.
        """
        from aruna.futures.stops import MIN_TARGET_ATR

        assert Decimal("1.0") < MIN_TARGET_ATR, (
            "lantai target kembali ke satu ATR - letak yang terukur PALING "
            "buruk dari seluruh tangga yang diuji"
        )

        satu_atr = ENTRY + ATR
        target = take_profit(
            entry=ENTRY,
            side=PositionSide.LONG,
            atr=ATR,
            structure_levels=(satu_atr,),
        )

        assert satu_atr not in target.levels
        assert any("dibuang" in f for f in target.findings)


class TestAWideStopIsNamed:
    def test_level_di_luar_jangkauan_horizon_dibuang(self) -> None:
        """**Menggantikan `test_a_stop_beyond_three_atr_says_so`.**

        Aturan lama: stop lebar DINAMAI, tidak pernah dipotong - "moving the
        stop to flatter the ratio would put it where the structure does not
        support it". Larangan itu masih berdiri; yang berubah pertanyaannya.

        Diukur 2026-08-26 atas 9.805 bar 1d: jangkauan melawan posisi dalam satu
        interval p99 = 2,11 ATR, dan sebuah stop lima ATR jauhnya tersentuh
        0,2% waktu. Di produksi, stop struktural 18,6% pada ramalan 24 jam
        tersentuh 0,39% - satu dari 256 hari. Level seperti itu tidak melindungi
        apa pun di dalam jendela yang ia klaim lindungi; ia hanya mengecilkan
        posisi terhadap risiko yang tidak bisa terjadi.

        Jadi ia DIBUANG, bukan dipotong - level yang dipotong bukan struktur dan
        bukan volatilitas - dan stop jatuh ke jalur volatilitas yang sama persis
        dengan "tidak ada struktur sama sekali", karena untuk horizon ini memang
        tidak ada.

        Ambangnya diturunkan dari waktu dan volatilitas saja. Mesin ini tetap
        tidak pernah diperlihatkan target, persis seperti yang dijanjikan
        docstring modulnya.
        """
        from aruna.futures.stops import ATR_FALLBACK, stop_loss

        entry, atr = Decimal(63000), Decimal(252)
        far = entry - atr * Decimal(5)
        stop = stop_loss(
            entry=entry, side=PositionSide.LONG, atr=atr, invalidation_level=far
        )

        assert stop is not None
        assert stop.from_volatility_only is True, (
            "level di luar jangkauan masih dipakai sebagai sandaran struktur"
        )
        assert stop.distance == atr * ATR_FALLBACK
        assert any("di luar" in f and "ATR" in f for f in stop.findings)
        # Jaraknya ikut disebut: operator harus tahu SEBERAPA jauh level itu,
        # bukan hanya bahwa ia dibuang.
        assert any("5.0 ATR" in f for f in stop.findings)

    def test_level_di_dalam_jangkauan_tetap_dipakai(self) -> None:
        """Pasangannya. Tanpa ini, perbaikan di atas bisa lulus dengan membuang
        SETIAP level struktur - dan itu mematikan seluruh gunanya."""
        from aruna.futures.stops import stop_loss

        entry, atr = Decimal(63000), Decimal(252)
        dekat = entry - atr  # satu ATR: di dalam jangkauan satu horizon
        stop = stop_loss(
            entry=entry, side=PositionSide.LONG, atr=atr, invalidation_level=dekat
        )

        assert stop is not None
        assert stop.from_volatility_only is False
        assert stop.price < dekat, "stop harus tetap di luar level, bukan di atasnya"

    def test_an_ordinary_stop_is_not_flagged(self) -> None:
        from aruna.futures.stops import stop_loss

        entry, atr = Decimal(63000), Decimal(252)
        near = entry - atr * Decimal("1.5")
        stop = stop_loss(
            entry=entry, side=PositionSide.LONG, atr=atr, invalidation_level=near
        )
        assert stop is not None
        assert not any("lebar" in f for f in stop.findings)


class TestTakeProfit:
    def test_structure_ahead_of_price_is_preferred(self) -> None:
        # 51.500 ada 3 ATR di depan entry. Dulu fixture ini mulai dari 50.800
        # (1,6 ATR), dan sejak lantai target dinaikkan ke 2 ATR (2026-08-26)
        # level itu dibuang - test-nya akan diam-diam berpindah menguji jalur
        # fallback alih-alih preferensi struktur yang jadi pokoknya.
        levels = (Decimal(51_500), Decimal(52_000), Decimal(52_500))
        target = take_profit(
            entry=ENTRY, side=PositionSide.LONG, atr=ATR, structure_levels=levels
        )
        assert target.levels[0] == Decimal(51_500)
        assert all("struktur di " in r for r in target.reasons)

    def test_levels_behind_price_are_not_targets(self) -> None:
        target = take_profit(
            entry=ENTRY,
            side=PositionSide.LONG,
            atr=ATR,
            structure_levels=(Decimal(49_000), Decimal(50_800)),
        )
        assert Decimal(49_000) not in target.levels

    def test_unreachable_levels_are_dropped(self) -> None:
        """A target the market reaches once a quarter is not a plan."""
        target = take_profit(
            entry=ENTRY,
            side=PositionSide.LONG,
            atr=ATR,
            structure_levels=(Decimal(50_800), Decimal(90_000)),
        )
        assert Decimal(90_000) not in target.levels
        assert any("bukan sebuah plan" in f for f in target.findings)

    def test_no_structure_falls_back_to_atr_and_says_so(self) -> None:
        target = take_profit(entry=ENTRY, side=PositionSide.LONG, atr=ATR)
        assert len(target.levels) == 3
        assert any("dasar yang lebih lemah" in f for f in target.findings)


# ---------------------------------------------------------------------------
# FUTURES SPEC 19, 20 - size from risk, never from leverage
# ---------------------------------------------------------------------------


class TestPositionSize:
    def test_the_engine_is_never_shown_leverage_or_a_profit_target(self) -> None:
        """SPEC 19 and 16, structurally: a number that cannot be passed in
        cannot be chased."""
        signature = inspect.signature(position_size)
        for banned in ("leverage", "target_profit", "daily_target", "goal"):
            assert banned not in signature.parameters

    def test_the_module_never_derives_size_from_leverage(self) -> None:
        source = inspect.getsource(risk_module).split('"""', 2)[-1]
        assert "quantity = " in source
        # The only quantity assignment divides the budget by the stop distance.
        assert "budget / stop_distance" in source

    def test_size_makes_the_planned_loss_equal_the_risk_budget(self) -> None:
        stop = Decimal(49_000)  # 1000 away
        size = position_size(
            equity=EQUITY,
            entry=ENTRY,
            stop=stop,
            side=PositionSide.LONG,
            risk_pct=Decimal("1.0"),
        )
        assert size is not None
        assert size.risk_amount == pytest.approx(Decimal(100), abs=Decimal("0.5"))
        assert size.risk_pct_of_equity == pytest.approx(
            Decimal(1), abs=Decimal("0.01")
        )

    def test_a_wider_stop_produces_a_smaller_position(self) -> None:
        tight = position_size(
            equity=EQUITY, entry=ENTRY, stop=Decimal(49_500), side=PositionSide.LONG
        )
        wide = position_size(
            equity=EQUITY, entry=ENTRY, stop=Decimal(48_000), side=PositionSide.LONG
        )
        assert tight is not None and wide is not None
        assert wide.quantity < tight.quantity
        # ...but both risk the same amount, which is the whole point.
        assert tight.risk_amount == pytest.approx(
            wide.risk_amount, abs=Decimal("1.0")
        )

    def test_leverage_is_an_output_named_implied(self) -> None:
        size = position_size(
            equity=EQUITY, entry=ENTRY, stop=Decimal(49_000), side=PositionSide.LONG
        )
        assert size is not None
        assert size.implied_leverage(EQUITY) is not None
        assert "leverage adalah output" in size.to_dict()["note"]

    def test_risk_above_the_ceiling_is_capped(self) -> None:
        size = position_size(
            equity=EQUITY,
            entry=ENTRY,
            stop=Decimal(49_000),
            side=PositionSide.LONG,
            risk_pct=MAX_RISK_PCT * 5,
        )
        assert size is not None
        assert size.risk_pct_of_equity <= MAX_RISK_PCT
        assert any("melewati batas atas" in f for f in size.findings)

    def test_a_size_below_the_venue_minimum_is_refused(self) -> None:
        """Taking it anyway would mean risking more than planned."""
        size = position_size(
            equity=Decimal(10),
            entry=ENTRY,
            stop=Decimal(49_000),
            side=PositionSide.LONG,
            contract=_contract(min_notional=Decimal(100)),
        )
        assert size is not None
        assert size.viable is False
        assert any(
            "risiko lebih besar dari yang direncanakan" in f for f in size.findings
        )

    def test_a_stop_at_entry_has_no_risk_to_size_from(self) -> None:
        size = position_size(
            equity=EQUITY, entry=ENTRY, stop=ENTRY, side=PositionSide.LONG
        )
        assert size is not None
        assert size.viable is False


# ---------------------------------------------------------------------------
# FUTURES SPEC 24, 25 - liquidation
# ---------------------------------------------------------------------------


class TestLiquidation:
    def test_no_contract_means_no_number(self) -> None:
        """SPEC 24: an unaccountable liquidation price is worse than none."""
        assert (
            liquidation_price(
                entry=ENTRY,
                quantity=Decimal("0.1"),
                side=PositionSide.LONG,
                margin=Decimal(1_000),
                contract=None,
            )
            is None
        )

    def test_the_long_formula_matches_the_venue(self) -> None:
        """0.1 BTC at 50,000 on 1,000 margin is 5x; liquidation lands near
        50,000 x (1 - 1/5 + MMR)."""
        result = liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(1_000),
            contract=_contract(margin_brackets=()),
        )
        assert result is not None
        assert result.price == pytest.approx(Decimal(40_160), abs=Decimal(50))

    def test_the_short_formula_mirrors_it(self) -> None:
        result = liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.SHORT,
            margin=Decimal(1_000),
            contract=_contract(margin_brackets=()),
        )
        assert result is not None
        assert result.price == pytest.approx(Decimal(59_761), abs=Decimal(50))

    def test_more_margin_moves_liquidation_further_away(self) -> None:
        thin = liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(500),
            contract=_contract(),
        )
        thick = liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(2_000),
            contract=_contract(),
        )
        assert thin is not None and thick is not None
        assert thick.price < thin.price

    def test_the_bracket_matching_the_notional_is_used(self) -> None:
        result = liquidation_price(
            entry=ENTRY,
            quantity=Decimal(2),  # 100,000 notional -> second bracket
            side=PositionSide.LONG,
            margin=Decimal(20_000),
            contract=_contract(),
        )
        assert result is not None
        assert result.maintenance_rate == Decimal("0.005")
        assert result.maintenance_amount == Decimal(50)

    def test_cross_margin_is_labelled_a_best_case(self) -> None:
        result = liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(1_000),
            contract=_contract(),
            margin_mode=MarginMode.CROSS,
        )
        assert result is not None
        assert any(
            "kasus terbaik, bukan sebagai levelnya" in f for f in result.findings
        )

    def test_the_module_states_the_formula_is_the_venues(self) -> None:
        assert "published formula" in (liquidation_module.__doc__ or "")
        result = liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=Decimal(1_000),
            contract=_contract(),
        )
        assert result is not None
        assert "bukan perkiraan" in result.to_dict()["note"]


class TestBufferScore:
    def _liquidation(self, margin: Decimal):
        return liquidation_price(
            entry=ENTRY,
            quantity=Decimal("0.1"),
            side=PositionSide.LONG,
            margin=margin,
            contract=_contract(),
        )

    def test_liquidation_before_the_stop_scores_zero(self) -> None:
        """The failure that makes every other risk number fiction."""
        score = buffer_score(
            entry=ENTRY,
            stop=Decimal(35_000),  # further than liquidation at ~40,160
            liquidation=self._liquidation(Decimal(1_000)),
            atr=ATR,
        )
        assert score.score == 0
        assert score.band == "REJECT"
        assert score.liquidation_before_stop is True
        assert "Stop-nya cuma hiasan" in score.findings[0]

    def test_a_distant_liquidation_scores_well(self) -> None:
        score = buffer_score(
            entry=ENTRY,
            stop=Decimal(49_000),
            liquidation=self._liquidation(Decimal(4_000)),
            atr=ATR,
        )
        assert score.score >= 60
        assert score.band in ("EXCELLENT", "HEALTHY", "CAUTION")
        assert score.stop_multiple is not None and score.stop_multiple > 1

    def test_the_worse_of_the_two_measures_wins(self) -> None:
        """A huge ATR buffer must not paper over a tight stop multiple, or the
        reverse."""
        liquidation = self._liquidation(Decimal(4_000))
        generous_atr = buffer_score(
            entry=ENTRY, stop=Decimal(49_000), liquidation=liquidation, atr=Decimal(1)
        )
        tiny_atr = buffer_score(
            entry=ENTRY,
            stop=Decimal(49_000),
            liquidation=liquidation,
            atr=Decimal(20_000),
        )
        assert tiny_atr.score < generous_atr.score

    def test_a_missing_liquidation_is_unacceptable_not_acceptable(self) -> None:
        score = buffer_score(entry=ENTRY, stop=Decimal(49_000), liquidation=None)
        assert score.score == 0
        assert score.rejected is True
        assert "diperlakukan sebagai tidak dapat diterima" in score.findings[0]

    def test_no_atr_narrows_the_claim(self) -> None:
        score = buffer_score(
            entry=ENTRY,
            stop=Decimal(49_000),
            liquidation=self._liquidation(Decimal(4_000)),
        )
        assert any("tidak mengatakan apa pun soal" in f for f in score.findings)

    def test_the_score_is_not_a_probability_of_profit(self) -> None:
        score = buffer_score(
            entry=ENTRY,
            stop=Decimal(49_000),
            liquidation=self._liquidation(Decimal(4_000)),
            atr=ATR,
        )
        assert "bukan probabilitas profit" in score.to_dict()["note"]

    def test_the_healthy_multiple_is_reachable(self) -> None:
        assert HEALTHY_STOP_MULTIPLE > 1


# ---------------------------------------------------------------------------
# FUTURES SPEC 26 - cascade
# ---------------------------------------------------------------------------


class TestCascade:
    def _event(self, side: PositionSide, notional: str) -> LiquidationEvent:
        from datetime import UTC, datetime

        from aruna.data.models import Provenance

        moment = datetime(2026, 1, 5, tzinfo=UTC)
        return LiquidationEvent(
            symbol="BTCUSDT",
            side=side,
            price=ENTRY,
            quantity=Decimal(1),
            notional=Decimal(notional),
            occurred_at=moment,
            provenance=Provenance(source="test", server_timestamp=moment),
        )

    def test_no_feed_is_unknown_not_none(self) -> None:
        """An empty list from a feed that does not exist looks exactly like a
        calm market."""
        report = detect_cascade([])
        assert report.risk is CascadeRisk.UNKNOWN
        assert report.data_available is False
        assert "belum terjawab, bukan terjawab 'tidak'" in report.findings[0]
        assert "UNKNOWN bukan NONE" in report.to_dict()["note"]

    def test_a_large_share_of_open_interest_is_high_risk(self) -> None:
        report = detect_cascade(
            [self._event(PositionSide.LONG, "5000")],
            open_interest=Decimal(100_000),
        )
        assert report.risk is CascadeRisk.HIGH
        assert any(
            "lebih tipis daripada yang ditunjukkan order book" in f
            for f in report.findings
        )

    def test_a_small_share_is_no_cascade(self) -> None:
        report = detect_cascade(
            [self._event(PositionSide.LONG, "100")], open_interest=Decimal(100_000)
        )
        assert report.risk is CascadeRisk.NONE

    def test_without_open_interest_the_absolute_figure_cannot_decide(self) -> None:
        report = detect_cascade([self._event(PositionSide.LONG, "5000")])
        assert any(
            "tidak bisa menyatakan apakah ini cascade" in f for f in report.findings
        )

    def test_the_dominant_side_is_reported(self) -> None:
        report = detect_cascade(
            [
                self._event(PositionSide.LONG, "100"),
                self._event(PositionSide.SHORT, "900"),
            ],
            open_interest=Decimal(100_000),
        )
        assert report.dominant_side is PositionSide.SHORT


# ---------------------------------------------------------------------------
# FUTURES SPEC 23 - economics
# ---------------------------------------------------------------------------


class TestEconomics:
    def _size(self):
        return position_size(
            equity=EQUITY,
            entry=ENTRY,
            stop=Decimal(49_000),
            side=PositionSide.LONG,
            contract=_contract(),
        )

    def test_net_is_below_gross_once_costs_are_counted(self) -> None:
        economics = trade_economics(
            size=self._size(),
            entry=ENTRY,
            stop=Decimal(49_000),
            target=Decimal(53_000),
            contract=_contract(),
        )
        assert economics.net_reward < economics.gross_reward
        assert economics.total_costs > 0
        assert economics.net_rr < economics.gross_rr

    def test_a_setup_whose_edge_costs_eat_is_refused(self) -> None:
        economics = trade_economics(
            size=self._size(),
            entry=ENTRY,
            stop=Decimal(49_000),
            target=Decimal(50_200),  # reward barely above the stop distance
            contract=_contract(),
        )
        assert economics.worth_taking is False
        assert any("tidak layak diambil" in f for f in economics.findings)

    def test_funding_enters_the_net_figure(self) -> None:
        free = trade_economics(
            size=self._size(),
            entry=ENTRY,
            stop=Decimal(49_000),
            target=Decimal(53_000),
            contract=_contract(),
        )
        expensive = trade_economics(
            size=self._size(),
            entry=ENTRY,
            stop=Decimal(49_000),
            target=Decimal(53_000),
            contract=_contract(),
            funding_cost_pct=Decimal("0.5"),
        )
        assert expensive.net_reward < free.net_reward
        assert any("proyeksi dari funding rate" in f for f in expensive.findings)

    def test_only_the_net_ratio_is_offered_as_a_decision_input(self) -> None:
        economics = trade_economics(
            size=self._size(),
            entry=ENTRY,
            stop=Decimal(49_000),
            target=Decimal(53_000),
            contract=_contract(),
        )
        assert "gross mengabaikan biaya yang sudah tentu" in economics.to_dict()["note"]
        assert MIN_NET_REWARD_RATIO > 0

    def test_the_stop_engine_and_the_economics_never_meet(self) -> None:
        """SPEC 21 again, from the other side: nothing in stops.py imports the
        economics, so a ratio cannot flow back into stop placement."""
        source = inspect.getsource(stops_module)
        assert "trade_economics" not in source
        assert "net_rr" not in source
