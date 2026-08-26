"""The futures signal (FUTURES SPEC 8-14, 37-39, 47, 51).

The assertions that matter most here are about what a plan does NOT contain.
A refused plan carrying an entry price and a leverage reads exactly like an
accepted one to anybody skimming, and skimming is how these get read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from aruna.data.models import Provenance
from aruna.futures.models import (
    ContractSpec,
    FundingRate,
    FuturesSnapshot,
    InstrumentType,
    MarkPrice,
    OpenInterest,
    PositionSide,
)
from aruna.futures.orderbook import OrderBookDepth
from aruna.futures.plan import (
    FORBIDDEN_CLAIMS,
    MIN_SEPAKAT,
    ForbiddenClaim,
    FuturesPlan,
    PlanVerdict,
    build_plan,
    render,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
ENTRY = Decimal(63000)
ATR = Decimal(600)
EQUITY = Decimal(100_000)


def _prov() -> Provenance:
    return Provenance(source="test", server_timestamp=NOW)


def _contract() -> ContractSpec:
    return ContractSpec(
        symbol="BTCUSDT",
        instrument=InstrumentType.PERPETUAL,
        base_asset="BTC",
        quote_asset="USDT",
        tick_size=Decimal("0.10"),
        step_size=Decimal("0.001"),
        min_notional=Decimal(5),
        max_leverage=20,
        maintenance_margin_rate=Decimal("0.004"),
        provenance=_prov(),
    )


def _book(*, as_of: datetime | None = None, depth: Decimal = Decimal(20)):
    return OrderBookDepth(
        symbol="BTCUSDT",
        bids=tuple((ENTRY - Decimal(i + 1), depth) for i in range(20)),
        asks=tuple((ENTRY + Decimal(i + 1), depth) for i in range(20)),
        as_of=as_of or NOW,
    )


def _snapshot(**overrides) -> FuturesSnapshot:
    base = {
        "symbol": "BTCUSDT",
        "captured_at": NOW,
        "mark": MarkPrice(
            symbol="BTCUSDT",
            mark_price=ENTRY,
            index_price=ENTRY,
            last_price=ENTRY,
            as_of=NOW,
            provenance=_prov(),
        ),
        "funding": FundingRate(
            symbol="BTCUSDT",
            rate=Decimal("0.0001"),
            funding_time=NOW,
            next_funding_time=NOW + timedelta(hours=4),
            interval_hours=8,
            provenance=_prov(),
        ),
        "open_interest": OpenInterest(
            symbol="BTCUSDT",
            open_interest=Decimal(80_000),
            notional=None,
            as_of=NOW,
            provenance=_prov(),
        ),
        "order_book": _book(),
        "contract": _contract(),
    }
    return FuturesSnapshot(**(base | overrides))


def _plan(**kwargs) -> FuturesPlan:
    params = {
        "signal_id": "sig-0001",
        "decision": "BUY",
        "snapshot": _snapshot(),
        "equity": EQUITY,
        "atr": ATR,
        "horizon_hours": 4.0,
        "invalidation_level": ENTRY - Decimal(900),
        "structure_levels": (ENTRY + Decimal(1500),),
        "reference": NOW,
    }
    return build_plan(**(params | kwargs))


class TestGerbangKesepakatan:
    """Arah yang disepakati tipis tidak boleh jadi plan.

    Diukur atas korpus 9.805 keputusan lintas 17 bulan, dilaporkan dari paruh
    kedua: pada ambang empat dewan yang "sepakat" benar 50,5% - edge -0,3, tidak
    lebih baik dari koin. Pada lima, 73,0%. Bentuknya tebing, bukan lereng, dan
    itu yang membuat lima bukan pilihan selera.

    Angkanya sendiri tidak dikunci di sini - yang dikunci adalah bahwa gerbangnya
    ADA, menolak di bawah ambang, dan menghitung agen yang benar.
    """

    def test_sepakat_di_bawah_ambang_ditolak(self) -> None:
        plan = _plan(sepakat=MIN_SEPAKAT - 1)
        assert plan.verdict is PlanVerdict.WAIT
        assert not plan.actionable

    def test_penolakannya_menyebut_angkanya(self) -> None:
        """Penolakan tanpa angka tidak bisa dibedakan dari penolakan lain, dan
        operator tidak bisa tahu seberapa dekat tadi."""
        plan = _plan(sepakat=2)
        assert any("2" in r and str(MIN_SEPAKAT) in r for r in plan.refusals), (
            plan.refusals
        )

    def test_sepakat_di_ambang_lolos(self) -> None:
        plan = _plan(sepakat=MIN_SEPAKAT)
        assert plan.verdict is PlanVerdict.PLAN, plan.refusals

    def test_tidak_terukur_bukan_nol(self) -> None:
        """`None` berarti pemanggilnya tidak mengukur, dan itu bukan alasan
        menolak - membalikkannya akan mematikan setiap pemanggil lama diam-diam.
        Jalur produksi SELALU mengoper angka; itu dijaga terpisah di bawah."""
        assert _plan(sepakat=None).verdict is PlanVerdict.PLAN

    def test_yang_dihitung_hanya_pembaca_pasar(self) -> None:
        """**Menghitung sembilan peran membuat ambang yang sama berarti dua hal
        berbeda di korpus dan di produksi.** NEWS/FUNDAMENTAL/RISK membaca bahan
        yang tidak tersedia point-in-time, jadi di replay mereka praktis tidak
        pernah menyetujui apa pun - ambang yang memasukkan mereka akan lolos
        jauh lebih sering di produksi daripada di tempat ia diukur.
        """
        from aruna.agents.base import AgentOpinion
        from aruna.core.enums import AgentRole, Decision
        from aruna.futures.service import _sepakat

        class _Vonis:
            decision = Decision.BUY
            opinions = tuple(
                AgentOpinion(
                    role=peran, decision=Decision.BUY, confidence=0.6,
                    reasoning=("x",),
                )
                for peran in (
                    AgentRole.TECHNICAL,
                    AgentRole.STRUCTURE,
                    AgentRole.NEWS,
                    AgentRole.FUNDAMENTAL,
                    AgentRole.RISK,
                )
            )

        assert _sepakat(_Vonis()) == 2, "tiga peran non-pasar ikut terhitung"

    def test_yang_melawan_tidak_ikut_terhitung(self) -> None:
        from aruna.agents.base import AgentOpinion
        from aruna.core.enums import AgentRole, Decision
        from aruna.futures.service import _sepakat

        class _Vonis:
            decision = Decision.BUY
            opinions = (
                AgentOpinion(role=AgentRole.TECHNICAL, decision=Decision.BUY,
                             confidence=0.6, reasoning=("x",)),
                AgentOpinion(role=AgentRole.MOMENTUM, decision=Decision.SELL,
                             confidence=0.6, reasoning=("x",)),
                AgentOpinion(role=AgentRole.VOLUME, decision=Decision.WAIT,
                             confidence=0.0, reasoning=("x",)),
            )

        assert _sepakat(_Vonis()) == 1

    def test_jalur_hidup_benar_benar_mengopernya(self) -> None:
        """Gerbang yang tidak pernah dipanggil bukan gerbang.

        Cacat ini sudah berulang di proyek ini: kode ditulis, diuji, diekspor,
        dan tidak pernah tersambung ke jalur yang jalan.
        """
        import inspect

        from aruna.futures import service

        sumber = inspect.getsource(service.FuturesPlanService)
        assert "sepakat=_sepakat(verdict)" in sumber

    def test_ambangnya_tidak_melebihi_jumlah_agen_yang_ada(self) -> None:
        """Ambang di atas jumlah pemilih adalah gerbang yang menutup selamanya -
        dan diamnya akan terbaca seperti "pasar sedang tidak ada peluang"."""
        from aruna.agents.market import PERAN_PEMBACA_PASAR

        assert len(PERAN_PEMBACA_PASAR) >= MIN_SEPAKAT


class TestTheHappyPathIsReachable:
    """If nothing can ever produce a PLAN, every refusal test below is vacuous."""

    def test_a_clean_setup_produces_a_plan(self) -> None:
        plan = _plan()
        assert plan.verdict is PlanVerdict.PLAN, plan.refusals
        assert plan.actionable is True

    def test_the_plan_carries_every_number_the_spec_asks_for(self) -> None:
        plan = _plan()
        for field_name in (
            "entry",
            "stop",
            "target",
            "quantity",
            "notional",
            "leverage",
            "margin_required",
            "net_rr",
        ):
            assert getattr(plan, field_name) is not None, field_name
        assert plan.liquidation is not None
        assert plan.buffer is not None

    def test_a_long_stop_sits_below_entry_and_the_target_above(self) -> None:
        plan = _plan()
        assert plan.stop < plan.entry < plan.target

    def test_a_short_is_mirrored(self) -> None:
        plan = _plan(
            decision="SELL",
            invalidation_level=ENTRY + Decimal(900),
            structure_levels=(ENTRY - Decimal(1500),),
        )
        assert plan.verdict is PlanVerdict.PLAN, plan.refusals
        assert plan.side is PositionSide.SHORT
        assert plan.target < plan.entry < plan.stop


class TestRefusalsCarryNoNumbers:
    """The core honesty property of the module.

    A refused plan that still reports an entry and a leverage is indistinguishable
    from an accepted one at a glance, and the glance is what people give it.

    **Dipertajam 2026-08-26: yang dilarang INSTRUKSI, bukan setiap angka.**

    ``net_rr`` dan ``expected_net_pnl`` dulu ikut di daftar ini, dan akibatnya
    sebuah penolakan berbunyi "net reward adalah 0.24x" sementara kolom
    ``net_rr`` di barisnya NULL. Angka yang MENENTUKAN putusan hanya hidup di
    prosa - tidak bisa dikueri, tidak bisa dibandingkan antar hari. Terukur:
    136 baris penolakan "net reward" dengan ``net_rr`` NULL di semuanya, dan
    mendiagnosis 134 penolakan berturut-turut menuntut mereproduksi seluruh
    jalur rencana di mesin produksi.

    Yang membuat pelonggaran ini aman: angkanya SUDAH dicetak di kalimat
    penolakannya. Menyimpan apa yang sudah diucapkan tidak menambah satu pun
    pengungkapan baru - ia hanya membuatnya bisa ditanyakan.

    Yang tetap kosong adalah yang bisa ditindaklanjuti: entry, stop, target,
    quantity, notional, leverage, margin. Itu instruksi, dan sebuah penolakan
    yang membawanya terlihat seperti ARUNA menyarankan trade yang baru saja ia
    tolak.
    """

    #: Instruksi. Tidak pernah ada pada penolakan, apa pun sebabnya.
    BLANK = (
        "entry",
        "stop",
        "target",
        "quantity",
        "notional",
        "leverage",
        "margin_required",
    )

    def _assert_blank(self, plan: FuturesPlan) -> None:
        assert plan.actionable is False
        assert plan.refusals, "a refusal must say why"
        for field_name in self.BLANK:
            assert getattr(plan, field_name) is None, field_name
        assert plan.liquidation is None
        assert plan.buffer is None

    def _assert_tanpa_aritmetika(self, plan: FuturesPlan) -> None:
        """Ditolak SEBELUM aritmetikanya jalan - jadi tidak ada angka putusan.

        Belum terukur bukan nol: sebuah rencana yang ditolak karena datanya
        tidak koheren tidak punya net_rr, dan mengarangnya nol akan membuatnya
        terlihat seperti setup yang dihitung lalu kalah.
        """
        self._assert_blank(plan)
        assert plan.net_rr is None
        assert plan.expected_net_pnl is None

    def test_incoherent_inputs_produce_no_signal(self) -> None:
        stale = _snapshot(order_book=_book(as_of=NOW - timedelta(minutes=30)))
        plan = _plan(snapshot=stale)
        assert plan.verdict is PlanVerdict.NO_SIGNAL
        self._assert_tanpa_aritmetika(plan)

    def test_a_missing_input_produces_no_signal(self) -> None:
        plan = _plan(snapshot=_snapshot(contract=None))
        assert plan.verdict is PlanVerdict.NO_SIGNAL
        self._assert_tanpa_aritmetika(plan)

    def test_a_council_wait_produces_wait_not_a_refusal(self) -> None:
        """These are different statements and must not collapse into one."""
        plan = _plan(decision="WAIT")
        assert plan.verdict is PlanVerdict.WAIT
        assert plan.side is PositionSide.FLAT
        self._assert_tanpa_aritmetika(plan)

    def test_no_atr_refuses_rather_than_inventing_a_stop(self) -> None:
        plan = _plan(atr=None)
        assert plan.verdict is PlanVerdict.REFUSED
        self._assert_tanpa_aritmetika(plan)
        assert any("tidak ada pengukuran ATR" in r for r in plan.refusals)

    @pytest.mark.parametrize("equity", ["0.01", "50", "100", "180"])
    def test_a_small_account_is_told_the_real_reason(self, equity: str) -> None:
        """It used to be told its reward-to-risk had failed.

        `position_size` floors sub-minimum quantities to zero rather than
        returning None, so the zero-size position walked past the sizing gate
        and was refused by the economics, where a zero risk makes net_rr None
        and the sentence rendered "net reward is Nonex the risk". Every account
        under roughly $450 on this contract got that - the ordinary small
        account - and the one thing they could act on was never said.
        """
        plan = _plan(equity=Decimal(equity))
        assert plan.verdict is PlanVerdict.REFUSED
        self._assert_tanpa_aritmetika(plan)
        joined = " ".join(plan.refusals)
        assert "di bawah minimum venue" in joined
        assert "net reward" not in joined

    def test_no_refusal_ever_interpolates_a_missing_number(self) -> None:
        """A number that does not exist must not print as the word None."""
        for plan in (
            _plan(equity=Decimal("0.01")),
            _plan(equity=Decimal(0)),
            _plan(atr=None),
            _plan(decision="WAIT"),
            _plan(structure_levels=(ENTRY + Decimal(50),)),
        ):
            for reason in plan.refusals:
                assert "None" not in reason, reason

    def test_a_reward_that_does_not_clear_the_floor_is_refused(self) -> None:
        """A target barely past entry: right direction, still not worth taking.

        **Dan angkanya ikut tersimpan.** Ini penolakan yang aritmetikanya
        BERJALAN, jadi rasio yang menjatuhkan putusannya harus ada di kolomnya -
        bukan hanya di kalimatnya. Tanpa ini, sebaran penolakan tidak bisa
        ditanyakan sama sekali, dan mendiagnosisnya menuntut mereproduksi
        seluruh jalur rencana di mesin produksi.
        """
        plan = _plan(structure_levels=(ENTRY + Decimal(50),))
        assert plan.verdict is PlanVerdict.REFUSED
        self._assert_blank(plan)

        assert plan.net_rr is not None, (
            "rasio yang menyebabkan penolakan tidak tersimpan - ia hanya hidup "
            "di prosa, dan prosa tidak bisa dikueri"
        )
        assert plan.expected_net_pnl is not None
        # Yang tersimpan harus angka yang SAMA dengan yang diucapkan.
        assert f"{plan.net_rr}x" in " ".join(plan.refusals)

    def test_a_book_too_thin_for_the_size_is_refused(self) -> None:
        thin = _snapshot(order_book=_book(depth=Decimal("0.0001")))
        plan = _plan(snapshot=thin)
        assert plan.verdict is PlanVerdict.REFUSED
        self._assert_blank(plan)
        # Likuiditas diperiksa SESUDAH aritmetika, jadi angkanya sudah ada dan
        # ikut - penolakan yang berbeda tidak menghapus perhitungan yang sudah
        # benar-benar terjadi.
        assert plan.net_rr is not None


class TestOrderOfTheGates:
    """Nothing downstream may reach back and improve something upstream."""

    def test_data_coherence_is_judged_before_anything_is_computed(self) -> None:
        plan = _plan(snapshot=_snapshot(mark=None))
        assert plan.verdict is PlanVerdict.NO_SIGNAL
        # Not merely blank - never attempted.
        assert plan.stop_detail is None
        assert plan.size_detail is None
        assert plan.economics is None

    def test_the_workings_are_kept_up_to_the_point_of_refusal(self) -> None:
        """A refusal has to be auditable, or it is just a shrug."""
        plan = _plan(structure_levels=(ENTRY + Decimal(50),))
        assert plan.verdict is PlanVerdict.REFUSED
        assert plan.stop_detail is not None
        assert plan.size_detail is not None
        assert plan.economics is not None


class TestTheCaveatsAgreeWithEachOther:
    """Rendering a plan showed two liquidation prices 25,000 apart.

    Each rung of the ladder posts a different margin and therefore projects a
    different liquidation price. The plan reported the CHOSEN rung's numbers in
    its fields while attaching the FIRST rung's projection to its caveats, and
    both were stated as fact in one list a person reads top to bottom.
    """

    def test_the_projection_belongs_to_the_chosen_leverage(self) -> None:
        plan = _plan()
        assert plan.projection is not None
        assert plan.leverage_decision is not None
        chosen = plan.leverage_decision.chosen
        assert chosen is not None
        assert plan.projection is chosen.projection

    def test_no_two_caveats_report_a_different_liquidation_price(self) -> None:
        import re

        plan = _plan()
        prices: set[str] = set()
        for caveat in plan.caveats:
            prices.update(re.findall(r"liquidation from ([\d.]+) to ([\d.]+)", caveat))
        assert len(prices) <= 1, f"contradictory liquidation prices: {prices}"

    def test_a_caveat_is_never_listed_twice(self) -> None:
        plan = _plan()
        assert len(plan.caveats) == len(set(plan.caveats))


class TestPrecisionIsNotInvented:
    def test_prices_render_at_the_venue_tick_not_the_division(self) -> None:
        """23 significant digits on a 0.10-tick contract is a false claim."""
        text = render(_plan())
        liquidation = next(
            line for line in text.splitlines() if line.startswith("LIQUIDATION:")
        )
        digits = liquidation.split(":", 1)[1].strip()
        assert "." in digits
        assert len(digits.split(".")[1]) <= 2, digits

    def test_an_unknown_tick_prints_the_number_rather_than_rounding_to_a_guess(
        self,
    ) -> None:
        from aruna.futures.plan import _price

        value = Decimal("56927.71084337349397590361446")
        assert _price(value, None) == str(value)


class TestTheTargetSaysWhereItCameFrom:
    """An ATR-multiple target and a structural target print identically.

    take_profit records which one it produced, and every path discarded it -
    so "64500" derived from 2.5x ATR read exactly like "64500" derived from a
    level price has respected four times.
    """

    def test_a_structural_target_says_so(self) -> None:
        plan = _plan(structure_levels=(ENTRY + Decimal(1500),))
        assert plan.verdict is PlanVerdict.PLAN
        joined = " ".join(plan.caveats).lower()
        assert "structure" in joined or "level" in joined

    def test_an_invented_target_admits_it_was_invented(self) -> None:
        # A tighter stop, so the ATR-derived target clears the ratio and the
        # plan survives to be rendered. With no structure ahead, take_profit
        # falls back to an ATR multiple - and must say so.
        plan = _plan(structure_levels=(), invalidation_level=ENTRY - Decimal(200))
        assert plan.verdict is PlanVerdict.PLAN, plan.refusals
        joined = " ".join(plan.caveats).lower()
        assert "atr" in joined


class TestFundingSign:
    def test_a_short_is_not_charged_for_funding_it_receives(self) -> None:
        """Dropping the sign would make every short look more expensive."""
        positive = _snapshot(
            funding=FundingRate(
                symbol="BTCUSDT",
                rate=Decimal("0.001"),
                funding_time=NOW,
                next_funding_time=NOW + timedelta(hours=1),
                interval_hours=8,
                provenance=_prov(),
            )
        )
        long_plan = _plan(snapshot=positive, horizon_hours=48.0)
        short_plan = _plan(
            snapshot=positive,
            horizon_hours=48.0,
            decision="SELL",
            invalidation_level=ENTRY + Decimal(900),
            structure_levels=(ENTRY - Decimal(1500),),
        )
        if long_plan.actionable and short_plan.actionable:
            assert short_plan.net_rr > long_plan.net_rr

    def test_the_caveat_agrees_with_the_arithmetic_about_who_pays(self) -> None:
        """The number charged a short correctly; the sentence told it the
        opposite. Both come from the same plan and a reader believes the
        sentence."""
        positive = _snapshot(
            funding=FundingRate(
                symbol="BTCUSDT",
                rate=Decimal("0.001"),
                funding_time=NOW,
                next_funding_time=NOW + timedelta(hours=1),
                interval_hours=8,
                provenance=_prov(),
            )
        )
        short_plan = _plan(
            snapshot=positive,
            horizon_hours=48.0,
            decision="SELL",
            invalidation_level=ENTRY + Decimal(900),
            structure_levels=(ENTRY - Decimal(1500),),
        )
        joined = " ".join(short_plan.caveats)
        assert "membayar sisi ini" in joined
        assert "membebani sisi ini" not in joined

    def test_funding_is_counted_in_whole_settlements_not_prorated(self) -> None:
        """One plan carried two funding costs: a prorated eighth of a payment
        in its net R:R and a whole-settlement count in its horizon caveat."""
        no_settlement = _snapshot(
            funding=FundingRate(
                symbol="BTCUSDT",
                rate=Decimal("0.001"),
                funding_time=NOW,
                # The next payment lands after the one-hour horizon ends.
                next_funding_time=NOW + timedelta(hours=7),
                interval_hours=8,
                provenance=_prov(),
            )
        )
        plan = _plan(snapshot=no_settlement, horizon_hours=1.0)
        assert plan.funding is not None
        # A one-hour hold that crosses no settlement pays exactly nothing.
        assert plan.funding.projected_cost_pct == Decimal(0)


class TestSlippageDoesNotContradictTheRatio:
    def test_a_ladder_that_breaks_on_a_clean_fill_names_the_contradiction(
        self,
    ) -> None:
        """"The edge disappears at normal" beside a positive NET R:R is not a
        footnote, it is two figures disagreeing."""
        from dataclasses import replace

        plan = _plan()
        broken = replace(plan, slippage=replace(plan.slippage, breaks_at="normal"))
        text = render(broken)
        assert "bertentangan dengan NET R:R di atas" in text
        assert "edge hilang pada normal" not in text


class TestImmutability:
    def test_the_plan_is_frozen(self) -> None:
        plan = _plan()
        with pytest.raises((AttributeError, TypeError)):
            plan.leverage = 20  # type: ignore[misc]

    def test_the_fingerprint_covers_the_numbers_that_must_not_move(self) -> None:
        from dataclasses import replace

        plan = _plan()
        moved = replace(plan, stop=plan.stop - Decimal(100))
        assert moved.fingerprint != plan.fingerprint

    def test_the_fingerprint_is_stable_across_recomputation(self) -> None:
        plan = _plan()
        assert plan.fingerprint == plan.fingerprint


class TestEveryNonPlanSaysTheSameInstruction:
    """The operator read WAIT as ambiguous, and was right to.

    The message used to open with the verdict code and then explain why there
    were no numbers - answering a question nobody asked. "WAIT" as a word
    reads as a pending state that resolves into a trade later; it is not.
    All three non-PLAN verdicts are the same instruction, and only the
    diagnosis differs.
    """

    @pytest.mark.parametrize(
        ("kwargs", "verdict"),
        [
            ({"decision": "WAIT"}, PlanVerdict.WAIT),
            ({"structure_levels": (ENTRY + Decimal(50),)}, PlanVerdict.REFUSED),
            ({"atr": None}, PlanVerdict.REFUSED),
        ],
    )
    def test_the_instruction_comes_before_the_diagnosis(
        self, kwargs: dict, verdict: PlanVerdict
    ) -> None:
        plan = _plan(**kwargs)
        assert plan.verdict is verdict
        text = render(plan)
        head = text.splitlines()[2]
        assert head == "TIDAK ADA POSISI YANG DIAMBIL."
        # And it precedes the verdict code, not the other way round.
        assert text.index("TIDAK ADA POSISI") < text.index("VERDICT:")

    def test_wait_is_not_left_to_be_guessed_at(self) -> None:
        text = render(_plan(decision="WAIT"))
        assert "council tidak melihat setup yang layak diambil" in text

    def test_refused_is_distinguished_from_wait(self) -> None:
        """Same instruction, different reason - and the reason is stated."""
        text = render(_plan(structure_levels=(ENTRY + Decimal(50),)))
        assert "ada arah, tapi aritmetikanya menolak" in text

    def test_it_promises_no_follow_up_plan(self) -> None:
        """Saying ARUNA looks again must not imply a plan is coming."""
        text = render(_plan(decision="WAIT"))
        assert "menilai ulang dari nol" in text
        assert "bukan" in text and "janji" in text

    def test_the_verdict_code_stays_an_identifier(self) -> None:
        """It is stored, schema-constrained and shared with the spot council;
        only the prose beside it is translated."""
        for plan in (_plan(decision="WAIT"), _plan(atr=None)):
            assert plan.verdict.value in ("WAIT", "REFUSED", "NO_SIGNAL")
            assert plan.verdict.value in render(plan)


class TestSpec51:
    """Forbidden claims are refused at emit time, not discouraged in a docstring."""

    def test_a_clean_plan_renders(self) -> None:
        text = render(_plan())
        assert "ARUNA FUTURES" in text
        assert "LEVERAGE:" in text

    def test_every_refusal_renders_without_numbers(self) -> None:
        text = render(_plan(decision="WAIT"))
        assert "VERDICT: WAIT" in text
        assert "ENTRY:" not in text
        assert "LEVERAGE:" not in text

    @pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
    def test_the_guard_catches_every_forbidden_claim(self, claim: str) -> None:
        """Parametrised over the real list, so adding a phrase adds a test."""
        from aruna.futures.plan import _guard

        with pytest.raises(ForbiddenClaim):
            _guard(f"ARUNA FUTURES - BTCUSDT\n\n{claim}")

    def test_the_guard_catches_a_claim_arriving_from_a_finding(self) -> None:
        """The reason the check runs on output, not on templates.

        This module writes none of the caveat text - it comes from six other
        modules and from the venue - so guarding the templates would guard the
        one source that was never the risk.
        """
        from dataclasses import replace

        from aruna.futures.plan import ForbiddenClaim

        plan = replace(_plan(), caveats=("this leverage aman for the horizon",))
        with pytest.raises(ForbiddenClaim):
            render(plan)

    def test_the_json_path_is_guarded_too(self) -> None:
        """render() and to_dict() are two doors out of the same room.

        A bot, a CLI and an API all reach the reader through to_dict(). Guarding
        only the text a human reads would leave the rule looking enforced while
        the machine-readable path carried the claim through untouched.
        """
        from dataclasses import replace

        plan = replace(_plan(), caveats=("dijamin profit at this size",))
        with pytest.raises(ForbiddenClaim):
            plan.to_dict()

    def test_a_forbidden_refusal_reason_is_caught_on_the_json_path(self) -> None:
        from dataclasses import replace

        plan = replace(_plan(decision="WAIT"), refusals=("risk free setup",))
        with pytest.raises(ForbiddenClaim):
            plan.to_dict()

    def test_the_guard_is_case_insensitive(self) -> None:
        from aruna.futures.plan import _guard

        with pytest.raises(ForbiddenClaim):
            _guard("PASTI PROFIT")


class TestArunaDoesNotTrade:
    def test_every_rendering_states_who_decides(self) -> None:
        for plan in (_plan(), _plan(decision="WAIT")):
            text = render(plan).lower()
            assert "aruna" in text

    def test_the_plan_dict_states_that_nothing_is_executed(self) -> None:
        note = _plan().to_dict()["note"]
        assert "ARUNA tidak trading" in note
        assert "tidak ada order yang dipasang" in note.lower()

    def test_the_module_exposes_no_execution_helper(self) -> None:
        """FUTURES SPEC 3, 50 - structurally, as in the adapter."""
        import inspect

        from aruna.futures import plan as plan_module

        source = inspect.getsource(plan_module)
        body = source.split('"""', 2)[-1]
        for forbidden in ("place_order", "submit_order", "set_leverage", "withdraw"):
            assert forbidden not in body, forbidden
