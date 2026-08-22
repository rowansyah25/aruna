"""Futures plan service and storage (FUTURES SPEC 8-14, 47, 48).

Two things are asserted here that nothing else can assert:

* the spot evidence is **rebased** onto the perpetual's own price scale before
  it touches a futures number;
* the refusals are stored, not only the plans.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from aruna.futures.models import PositionSide
from aruna.futures.service import (
    _atr_of,
    _rebase_ratio,
    _spot_symbol,
    _structure_of,
)

# BTC/USDT spot against the BTCUSDT perpetual's mark.
#
# The 1% premium is deliberate and is the whole reason these numbers are not
# equal. A perpetual trades at a basis to spot, so mark != spot; picking a
# basis large enough to see means a rebase that quietly returned 1 - or was
# skipped entirely - shows up as a failed assertion instead of rounding away.
# Before PASAL 6 the gap was a factor of ~17,800 and any mistake was obvious;
# now it is 1%, and the tests have to work harder to stay honest.
SPOT_PRICE = Decimal("63000")
SPOT_ATR = Decimal("308.70")  # 0.49% of spot, the proportion measured live
MARK = Decimal("63630")  # +1.00% basis


def _context(*, atr=SPOT_ATR, price=SPOT_PRICE, structure=None):
    return SimpleNamespace(
        state=SimpleNamespace(last_price=price),
        structure=structure,
        value=lambda name: float(atr) if name == "atr" and atr is not None else None,
    )


def _snapshot(mark=MARK):
    return SimpleNamespace(reference_price=mark)


class TestUnitsAreRebased:
    """Why this machinery still exists now that both sides are USDT.

    It was written for a units bug: while spot was quoted in IDR, BTC's ATR
    was 5,495,793 against a mark of 63,000. Passed straight through, the stop
    sat 5.5 million "dollars" from a 63,000 entry, the quantity floored to
    zero, and every plan was refused for "below the venue minimum" - a units
    error wearing the costume of a risk decision.

    PASAL 6 removed that bug by removing the second currency. What it did not
    remove is the reason the code guards: a perpetual's mark differs from spot
    by the basis, and ``_rebase_ratio`` returning ``None`` on a missing price
    is what makes a plan refuse rather than invent a stop. Both are asserted
    below on numbers where the difference is 1%, not 17,800x.
    """

    def test_the_ratio_is_the_two_reference_prices(self) -> None:
        ratio = _rebase_ratio(_context(), _snapshot())
        assert ratio == MARK / SPOT_PRICE

    def test_the_ratio_is_not_silently_one(self) -> None:
        """A rebase that ignored its inputs would pass every proportion test.

        With spot and mark now within a percent of each other, ``return 1``
        is a plausible-looking implementation that breaks nothing visible.
        This is the assertion that would catch it.
        """
        ratio = _rebase_ratio(_context(), _snapshot())
        assert ratio != Decimal(1)
        assert ratio == Decimal("1.01")

    def test_a_rebased_atr_is_a_plausible_fraction_of_the_mark(self) -> None:
        atr = _atr_of(_context(), _rebase_ratio(_context(), _snapshot()))
        assert atr is not None
        # ~0.49% of price, on either scale. The proportion is the part that
        # means the same thing on both.
        assert Decimal("0.001") < atr / MARK < Decimal("0.02")

    def test_the_proportion_survives_the_conversion(self) -> None:
        ratio = _rebase_ratio(_context(), _snapshot())
        atr = _atr_of(_context(), ratio)
        assert (atr / MARK).quantize(Decimal("0.00001")) == (
            SPOT_ATR / SPOT_PRICE
        ).quantize(Decimal("0.00001"))

    def test_the_rebased_atr_is_the_spot_atr_scaled_by_the_basis(self) -> None:
        """The absolute figure, not just the proportion.

        A 308.70 ATR on a 63,000 spot is 311.79 against a 63,630 mark. The
        proportion test above holds even if the ATR is passed through
        unscaled, because the same number over the same mark is still a
        plausible fraction; this one does not.
        """
        atr = _atr_of(_context(), _rebase_ratio(_context(), _snapshot()))
        assert atr is not None
        assert atr != SPOT_ATR
        assert atr.quantize(Decimal("0.01")) == Decimal("311.79")

    def test_an_unconvertible_atr_is_unavailable_not_unconverted(self) -> None:
        """Passing the raw figure through would be the original defect."""
        assert _atr_of(_context(), None) is None

    @pytest.mark.parametrize("spot", [None, Decimal(0), Decimal(-1)])
    def test_a_missing_reference_price_yields_no_ratio(self, spot) -> None:
        assert _rebase_ratio(_context(price=spot), _snapshot()) is None

    def test_structure_levels_are_rebased_too(self) -> None:
        """A support measured on spot sits at the mark's equivalent price."""
        structure = SimpleNamespace(
            support=(SimpleNamespace(price=Decimal("62500")),),
            resistance=(SimpleNamespace(price=Decimal("64000")),),
        )
        context = _context(structure=structure)
        ratio = _rebase_ratio(context, _snapshot())
        invalidation, ahead = _structure_of(context, PositionSide.LONG, ratio)
        assert invalidation is not None
        # 62,500 x 1.01, not 62,500 - the level moved with the basis.
        assert invalidation == Decimal("63125.00")
        assert ahead == (Decimal("64640.00"),)

    def test_no_ratio_means_no_levels_rather_than_raw_ones(self) -> None:
        structure = SimpleNamespace(
            support=(SimpleNamespace(price=Decimal("62500")),),
            resistance=(),
        )
        assert _structure_of(_context(structure=structure), PositionSide.LONG, None) == (
            None,
            (),
        )


class TestSidesGetTheirOwnLevels:
    """Handing the engine the wrong side's levels places the stop where the
    target belongs."""

    STRUCTURE = SimpleNamespace(
        support=(
            SimpleNamespace(price=Decimal("900")),
            SimpleNamespace(price=Decimal("950")),
        ),
        resistance=(
            SimpleNamespace(price=Decimal("1050")),
            SimpleNamespace(price=Decimal("1100")),
        ),
    )

    def test_a_long_is_invalidated_below_and_targets_above(self) -> None:
        invalidation, ahead = _structure_of(
            _context(structure=self.STRUCTURE), PositionSide.LONG, Decimal(1)
        )
        assert invalidation == Decimal("950")  # nearest support beneath
        assert ahead[0] == Decimal("1050")

    def test_a_short_is_the_mirror(self) -> None:
        invalidation, ahead = _structure_of(
            _context(structure=self.STRUCTURE), PositionSide.SHORT, Decimal(1)
        )
        assert invalidation == Decimal("1050")  # nearest resistance above
        assert ahead[0] == Decimal("950")

    def test_a_flat_side_gets_nothing(self) -> None:
        assert _structure_of(
            _context(structure=self.STRUCTURE), PositionSide.FLAT, Decimal(1)
        ) == (None, ())


class TestSymbolMapping:
    @pytest.mark.parametrize(
        ("perpetual", "spot"),
        [
            ("BTCUSDT", "BTC/USDT"),
            ("ETHUSDT", "ETH/USDT"),
            ("SOLUSDC", "SOL/USDT"),
            ("btcusdt", "BTC/USDT"),
        ],
    )
    def test_a_perpetual_maps_to_the_spot_symbol_aruna_follows(
        self, perpetual: str, spot: str
    ) -> None:
        assert _spot_symbol(perpetual) == spot


class TestPenilaianDikunciDenganSimbolPlan:
    """Council mengeja ``BTC/USDT``; plan bernama ``BTCUSDT``.

    Penilaian dicari berdasarkan nama plan. Memakai ejaan council membuat
    pencariannya tidak pernah cocok, dan bagian PENILAIAN hilang dari pesan
    tanpa error, tanpa log - hanya bagian yang tidak ada. Tepat kelas cacat
    yang lolos review karena semua unit test-nya tetap hijau.
    """

    class _Berhenti(Exception):
        """Menghentikan `_plan_one` tepat setelah baris yang diuji."""

    @pytest.mark.asyncio
    async def test_simbol_perpetual_yang_diteruskan(self, monkeypatch) -> None:
        import asyncio
        from datetime import UTC, datetime

        from aruna.core.enums import Decision
        from aruna.futures import service as modul
        from aruna.futures.service import FuturesPlanService

        dicatat: dict = {}

        def _tangkap(verdict, *, symbol=None):
            dicatat["symbol"] = symbol
            raise self._Berhenti

        monkeypatch.setattr(modul, "note_of", _tangkap)

        verdict = SimpleNamespace(
            symbol="BTC/USDT", interval="4h",
            decision=SimpleNamespace(value=Decision.BUY.value),
            confidence=0.6, opinions=(),
            protest=SimpleNamespace(objections=(), rebuttals=(), disagreement=0.1),
            veto=SimpleNamespace(vetoes=(), upheld=(), reviews=()),
            judgement=SimpleNamespace(minority_prevailed=False),
        )

        svc = FuturesPlanService.__new__(FuturesPlanService)
        svc._council = SimpleNamespace(convene=lambda ctx: verdict)
        svc._council_store = None
        # Bagian 16.2: jalur ini sekarang menyimpan funding dan open interest.
        svc._metrik = None
        svc._deliberation = SimpleNamespace(
            build_context=lambda *a, **k: asyncio.sleep(0, SimpleNamespace(as_of=None))
        )
        svc._resolve_asset = lambda symbol: asyncio.sleep(
            0, SimpleNamespace(id=1, symbol=symbol)
        )

        provider = SimpleNamespace(
            snapshot=lambda symbol: asyncio.sleep(0, SimpleNamespace(symbol=symbol))
        )

        with pytest.raises(self._Berhenti):
            await svc._plan_one(
                provider, "BTCUSDT",
                horizon=SimpleNamespace(value="4h"),
                equity=Decimal("10000"),
                risk_pct=None,
                now=datetime(2026, 8, 18, tzinfo=UTC),
            )

        assert dicatat["symbol"] == "BTCUSDT", (
            "penilaian dikunci dengan ejaan council, bukan ejaan plan - "
            "pencariannya tidak akan pernah cocok"
        )


class TestRunCounts:
    """FUTURES SPEC 48 counts every verdict, not only the plans."""

    def test_catatan_council_dikunci_per_simbol(self) -> None:
        from aruna.futures.service import PlanRun

        run = PlanRun(
            councils=(
                SimpleNamespace(symbol="BTCUSDT"),
                SimpleNamespace(symbol="ETHUSDT"),
            )
        )
        assert set(run.notes) == {"BTCUSDT", "ETHUSDT"}

    def test_the_tally_covers_all_four_verdicts(self) -> None:
        from aruna.futures.plan import PlanVerdict
        from aruna.futures.service import PlanRun

        def _plan(verdict):
            return SimpleNamespace(verdict=verdict)

        run = PlanRun(
            plans=(
                _plan(PlanVerdict.PLAN),
                _plan(PlanVerdict.REFUSED),
                _plan(PlanVerdict.REFUSED),
                _plan(PlanVerdict.WAIT),
                _plan(PlanVerdict.NO_SIGNAL),
            ),
            stored=5,
        )
        counts = run.to_dict()
        assert counts == {
            "considered": 5,
            "plans": 1,
            "refused": 2,
            "waited": 1,
            "no_signal": 1,
            "stored": 5,
            "errors": [],
        }

    def test_a_run_that_stored_nothing_says_so(self) -> None:
        from aruna.futures.service import PlanRun

        assert PlanRun().to_dict()["stored"] == 0
