"""The futures signal (FUTURES SPEC 8-14, 37-39, 47, 51).

Where F1-F4 become one answer. Every layer before this produced a fragment - a
stop, a size, a ladder of leverages, a liquidity verdict - and none of them can
say whether to take the trade. This module assembles them and, more often than
not, refuses.

**A plan is not a recommendation to trade.** ARUNA remains the analyst
(FUTURES SPEC 50): it never places an order, never changes leverage or margin
mode, never moves funds. The person reading this executes it, ignores it, or
overrides it, and the plan is written so that all three remain obvious.

Four verdicts, and only one of them carries numbers:

``NO_SIGNAL``
    The inputs do not describe one coherent moment (FUTURES SPEC 46). Nothing is
    computed from them, because a position sized from incoherent data is not
    approximately right.
``WAIT``
    The council stood aside. There is no position to size, so no stop, no
    leverage and no liquidation price exist to report.
``REFUSED``
    A directional idea the arithmetic rejects - no stop can be placed, the size
    is below the venue minimum, the net reward does not clear the floor, or no
    leverage on the ladder is safe enough. The reasons travel with it.
``PLAN``
    Everything cleared. The numbers are here, and so is every caveat behind
    them.

A refused plan carries **no** entry, size, leverage or liquidation price. Half a
plan reads like a plan, and the missing half is exactly the part that said no.

**FUTURES SPEC 51 is enforced, not promised.** :func:`render` scans its own
output for the claims the spec forbids - "100% win", "pasti profit", "leverage
aman" - and raises rather than emitting one. A rule that lives only in a
docstring is a rule that survives until someone writes a cheerful template.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.core.claims import FORBIDDEN_CLAIMS as _FORBIDDEN_CLAIMS
from aruna.core.clock import isoformat, now_utc
from aruna.core.errors import ArunaError
from aruna.futures.discipline import DisciplineReport
from aruna.futures.funding import FundingAnalysis, analyse_funding
from aruna.futures.horizon import HorizonProjection, settlements_in
from aruna.futures.integrity import IntegrityReport
from aruna.futures.integrity import check as check_integrity
from aruna.futures.leverage import (
    LeverageDecision,
    advise_margin_mode,
    recommend_leverage,
    stress_test,
)
from aruna.futures.liquidation import BufferScore, Liquidation
from aruna.futures.models import (
    ContractSpec,
    FundingRate,
    FuturesSnapshot,
    MarginMode,
    PositionSide,
    side_of,
)
from aruna.futures.orderbook import LiquidityVerdict, assess_liquidity
from aruna.futures.risk import PositionSize, TradeEconomics, position_size, trade_economics
from aruna.futures.slippage import SlippageReport, stress_slippage
from aruna.futures.stops import (
    StopLoss,
    TakeProfit,
    _round_to_tick,
    stop_loss,
    take_profit,
)
from aruna.signals.lock import (
    MAX_EVIDENCE_AGE_MULTIPLE as _MAX_EVIDENCE_AGE_MULTIPLE,
)

#: Evidence older than this multiple of the horizon cannot forecast it.
#: 1.0 is the same figure the spot lock uses, deliberately: a call whose newest
#: settled bar closed longer ago than the window it predicts has already been
#: overtaken by the market it is describing.
#:
#: **Diimpor, bukan ditulis ulang, sejak 2026-08-23.** "The same figure the spot
#: lock uses, deliberately" adalah invarian - dan sampai hari itu ia cuma
#: kalimat di atas literal kedua. Yang mengubah satu sisi tidak akan tahu sisi
#: lain ada, dan tidak ada test yang merah ketika keduanya melenceng.
MAX_EVIDENCE_AGE_MULTIPLE = _MAX_EVIDENCE_AGE_MULTIPLE

#: Which set of fields the fingerprint currently covers.
#:
#: v1: the original list.
#: v2: adds ``reference_price`` (migration 0016), so a WAIT can be scored.
#:
#: Bump this whenever a field is added to or removed from the basis, and never
#: change an existing version's field list - a row stores the version it was
#: written under, and rewriting that version's basis un-verifies the archive.
FINGERPRINT_VERSION = 2

#: Didefinisikan di :mod:`aruna.core.claims`, bukan di sini. SPEC 51 melarang
#: ARUNA mengucapkannya di jalur mana pun, bukan cuma di futures - dan lapisan
#: agent perlu memakainya untuk menyensor kutipan berita tanpa harus
#: bergantung ke modul futures. Di-re-export supaya import lama tetap jalan.
FORBIDDEN_CLAIMS = _FORBIDDEN_CLAIMS

#: Skala tarif funding, sama dengan kolom ``futures_plans.funding_cost_pct``
#: (``DECIMAL(14,6)``). Lihat alasannya di tempat pemakaiannya.
FUNDING_PCT_SCALE = Decimal("0.000001")


class ForbiddenClaim(ArunaError):
    """Rendered output contained a claim FUTURES SPEC 51 prohibits."""


class PlanVerdict(StrEnum):
    PLAN = "PLAN"
    NO_SIGNAL = "NO_SIGNAL"
    WAIT = "WAIT"
    REFUSED = "REFUSED"


@dataclass(frozen=True, slots=True)
class FuturesPlan:
    """One futures answer, complete with the reasons it might be nothing.

    Immutable for the same reason :class:`~aruna.signals.models.LockedSignal`
    is (SPEC 20): the moment a plan is issued is the moment it becomes
    scoreable, and a stop that can be edited afterwards makes every risk figure
    derived from it unfalsifiable.
    """

    signal_id: str
    symbol: str
    side: PositionSide
    verdict: PlanVerdict
    horizon_hours: float
    created_at: datetime

    #: The mark price this verdict was formed against, on **every** verdict.
    #:
    #: Not a decision number, which is why it survives a refusal: entry, stop
    #: and leverage are instructions, and this is an observation. Without it a
    #: WAIT is unscoreable - "ARUNA stood aside" cannot be judged, only
    #: "ARUNA stood aside at 63,000" can, because only the second says what the
    #: market then did relative to anything.
    reference_price: Decimal | None = None

    #: Present only when :attr:`verdict` is ``PLAN``.
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    quantity: Decimal | None = None
    notional: Decimal | None = None
    leverage: int | None = None
    margin_mode: MarginMode = MarginMode.ISOLATED
    margin_required: Decimal | None = None
    liquidation: Liquidation | None = None
    buffer: BufferScore | None = None
    net_rr: Decimal | None = None
    expected_net_pnl: Decimal | None = None

    #: The workings, kept whether or not the plan cleared.
    integrity: IntegrityReport | None = None
    stop_detail: StopLoss | None = None
    size_detail: PositionSize | None = None
    economics: TradeEconomics | None = None
    leverage_decision: LeverageDecision | None = None
    projection: HorizonProjection | None = None
    slippage: SlippageReport | None = None
    liquidity: LiquidityVerdict | None = None
    funding: FundingAnalysis | None = None

    #: Why this is not a PLAN, when it is not.
    refusals: tuple[str, ...] = field(default_factory=tuple)
    #: Caveats that survived into a plan that did clear.
    caveats: tuple[str, ...] = field(default_factory=tuple)
    margin_mode_reason: str = ""
    #: The venue's price increment, carried so rendering can round to it rather
    #: than print a division's full precision as if it were a measurement.
    tick_size: Decimal | None = None

    @property
    def actionable(self) -> bool:
        """True only for a complete plan.

        Deliberately not "not refused": a WAIT and a NO_SIGNAL are also not
        actionable, and collapsing the three into one boolean is how a caller
        ends up treating "the data was unusable" as "the setup was fine".
        """
        return self.verdict is PlanVerdict.PLAN

    @property
    def fingerprint(self) -> str:
        """Hash of the numbers that must not move after issue.

        Canonicalised the same way the spot lock canonicalises its prices, so
        the hash survives a database round trip - a fingerprint that changes on
        reload would reject every plan it exists to protect.
        """
        return self.fingerprint_at(FINGERPRINT_VERSION)

    def fingerprint_at(self, version: int) -> str:
        """The hash as the given basis version computed it.

        **The basis itself is versioned, and that is not over-engineering.**
        Adding ``reference_price`` to the list in migration 0016 silently
        invalidated every row already stored: the resolver's first real pass
        rejected 26 plans as "changed after issue" when not one had been
        touched. The check was working; the ground moved under it.

        PHASE 7 hit the same class through DECIMAL rounding and fixed it by
        pinning the scale. This is the other half - what moved was not a value
        but the *list of fields* being hashed - and the fix is to record which
        list was in force when the row was written.
        """
        fields = [
            self.signal_id,
            self.symbol,
            self.side.value,
            self.verdict.value,
            f"{self.horizon_hours:.4f}",
        ]
        if version >= 2:
            # Added in 0016. Absent from v1 rows, and re-adding it to their
            # basis is exactly what broke them.
            fields.append(_canonical(self.reference_price))
        fields += [
            _canonical(self.entry),
            _canonical(self.stop),
            _canonical(self.target),
            _canonical(self.quantity),
            str(self.leverage),
            self.margin_mode.value,
            _canonical(self.liquidation.price if self.liquidation else None),
            isoformat(self.created_at),
            "".join(self.refusals),
        ]
        return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "fingerprint": self.fingerprint,
            "symbol": self.symbol,
            "side": self.side.value,
            "verdict": self.verdict.value,
            "actionable": self.actionable,
            "horizon_hours": self.horizon_hours,
            "created_at": isoformat(self.created_at),
            "reference_price": _maybe(self.reference_price),
            "entry": _maybe(self.entry),
            "stop": _maybe(self.stop),
            "target": _maybe(self.target),
            "quantity": _maybe(self.quantity),
            "notional": _maybe(self.notional),
            "leverage": f"{self.leverage}x" if self.leverage else None,
            "margin_mode": self.margin_mode.value,
            "margin_required": _maybe(self.margin_required),
            "liquidation_price": _maybe(
                self.liquidation.price if self.liquidation else None
            ),
            "buffer": self.buffer.to_dict() if self.buffer else None,
            "net_rr": _maybe(self.net_rr),
            "expected_net_pnl": _maybe(self.expected_net_pnl),
            "integrity": self.integrity.to_dict() if self.integrity else None,
            "leverage_decision": (
                self.leverage_decision.to_dict() if self.leverage_decision else None
            ),
            "slippage": self.slippage.to_dict() if self.slippage else None,
            # Guarded on this path too, not only in render(). A forbidden claim
            # arriving from a finding would otherwise be blocked in the text a
            # human reads and pass freely through the JSON a bot, a CLI or an
            # API hands the same human a moment later. One emission path
            # enforcing SPEC 51 and another not is worse than neither, because
            # it makes the rule look enforced.
            "refusals": [_guard(r) for r in self.refusals],
            "caveats": [_guard(c) for c in self.caveats],
            "note": (
                "ARUNA menganalisis; ARUNA tidak trading. Tidak ada order yang "
                "dipasang, tidak ada setting leverage atau margin yang diubah, "
                "dan tidak ada dana yang berpindah (FUTURES SPEC 3, 50). "
                "Eksekusi, abaikan, atau timpa ini - keputusannya ada pada "
                "pembaca"
            ),
        }


def _price(value: Decimal | None, tick: Decimal | None) -> str:
    """A price at the precision the venue actually trades in.

    The liquidation formula is a division, so it returns figures like
    ``56927.71084337349397590361446``. Printing that claims twenty-three digits
    of precision for a contract whose tick is 0.10, and the claim is not merely
    ugly - it invites someone to place a stop inside a gap the venue cannot
    represent. Rounded to the tick, or left as-is when the venue never told us
    what the tick is.
    """
    if value is None:
        return "tidak diketahui"
    if tick is None or tick <= 0:
        return str(value)
    # Two steps, and both are needed.
    #
    # Snap to the venue's grid first: `quantize` alone copies the second
    # operand's *exponent*, so a tick of 0.5 would round to one decimal and
    # print 56927.7 - a price the venue cannot accept. The real snap lives in
    # stops.py and is reused rather than reimplemented, because two copies of a
    # rounding rule is how they drift.
    #
    # Then quantize to the tick's own precision, so the SAME field always
    # renders the same width. Without it the result carried whatever exponent
    # the arithmetic happened to produce, and one entry price printed as
    # "63000" while another printed as "63000.00" - which reads like different
    # precision rather than the same number.
    return format(_round_to_tick(value, tick, down=True).quantize(tick), "f")


def _money(value: Decimal | None) -> str:
    if value is None:
        return "tidak diketahui"
    return format(value.quantize(Decimal("0.01")), "f")


def _canonical(value: Decimal | None) -> str:
    if value is None:
        return "None"
    return format(value.quantize(Decimal("0.000000000001")), "f")


def _maybe(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def build_plan(
    *,
    signal_id: str,
    decision: Any,
    snapshot: FuturesSnapshot,
    equity: Decimal,
    atr: Decimal | None,
    horizon_hours: float,
    invalidation_level: Decimal | None = None,
    structure_levels: tuple[Decimal, ...] = (),
    funding_history: list[FundingRate] | None = None,
    risk_pct: Decimal | None = None,
    open_positions: int = 0,
    correlated_exposure: bool = False,
    hostile_regime: bool = False,
    discipline: DisciplineReport | None = None,
    evidence_as_of: datetime | None = None,
    reference: datetime | None = None,
) -> FuturesPlan:
    """Assemble one futures answer (FUTURES SPEC 8-14, 37-39).

    The order of the gates is the point. Data coherence is checked before
    anything is computed from the data; the council's direction is consulted
    before a position is sized; the stop is placed before the size that depends
    on it; and leverage is chosen last, from a position that already exists.
    Nothing downstream can reach back and adjust something upstream to make a
    number look better.
    """
    now = reference or now_utc()
    side = side_of(decision)

    def refuse(
        verdict: PlanVerdict,
        reasons: list[str],
        **carried: Any,
    ) -> FuturesPlan:
        return FuturesPlan(
            signal_id=signal_id,
            symbol=snapshot.symbol,
            side=side,
            verdict=verdict,
            horizon_hours=horizon_hours,
            created_at=now,
            # Carried through every refusal. See the field's own note: an
            # observation, not an instruction, and the only thing that makes a
            # WAIT scoreable afterwards.
            reference_price=snapshot.reference_price,
            refusals=tuple(reasons),
            **carried,
        )

    # ---- 1. do these inputs describe one moment? (FUTURES SPEC 46) ---------
    integrity = check_integrity(snapshot, reference=now)
    if integrity.blocks_signal:
        return refuse(
            PlanVerdict.NO_SIGNAL,
            [
                integrity.summary(),
                "tidak ada satu angka pun yang dihitung dari input ini: "
                "position size yang dihitung dari data tidak koheren bukan "
                "kira-kira benar, melainkan salah",
            ],
            integrity=integrity,
        )

    # ---- 1b. is the direction as fresh as the price? -----------------------
    #
    # The gate above polices Binance to within sixty seconds. The council's
    # verdict comes from spot candles, and nothing checked those at all - so a
    # plan could pass "these inputs describe one coherent moment" while its
    # LONG/SHORT came from yesterday afternoon. Measured on a live run: every
    # interval's newest bar was eleven hours old against a four-hour horizon,
    # and the direction never changed because the evidence never did.
    #
    # The spot lock has carried this rule since PHASE 7 - "a 1h call whose
    # newest settled bar closed six hours ago is not a one-hour forecast" - and
    # the futures path simply never inherited it.
    if evidence_as_of is not None:
        evidence_age = (now - evidence_as_of).total_seconds() / 3600
        if evidence_age > horizon_hours * MAX_EVIDENCE_AGE_MULTIPLE:
            return refuse(
                PlanVerdict.NO_SIGNAL,
                [
                    f"bukti terbaru dari council ditutup {evidence_age:.1f}h "
                    f"lalu, lebih lama dari horizon {horizon_hours:g}h yang "
                    "sedang diramalkannya. Market sudah bergerak melewati "
                    "sebagian besar window yang diklaim dicakup plan ini, jadi "
                    "arah tersebut bukan ramalan atas window itu"
                ],
                integrity=integrity,
            )

    # ---- 2. did the council take a side? -----------------------------------
    if side is PositionSide.FLAT:
        return refuse(
            PlanVerdict.WAIT,
            [
                "council tidak mengambil sisi, jadi tidak ada posisi untuk "
                "di-size. Tidak ada stop, leverage, atau liquidation price "
                "untuk trade yang tidak pernah diajukan"
            ],
            integrity=integrity,
        )

    entry = snapshot.reference_price
    contract = snapshot.contract
    if entry is None or entry <= 0:
        return refuse(
            PlanVerdict.NO_SIGNAL,
            ["tidak ada mark price, jadi tidak ada harga untuk dijadikan dasar plan"],
            integrity=integrity,
        )

    # ---- 3. where is this idea wrong? (FUTURES SPEC 21) --------------------
    if atr is None or atr <= 0:
        return refuse(
            PlanVerdict.REFUSED,
            [
                "tidak ada pengukuran ATR, jadi stop tidak bisa dipasang. Stop "
                "di angka bulat bukan sebuah level, dan setiap angka risiko "
                "yang diturunkan darinya adalah aritmetika di atas karangan"
            ],
            integrity=integrity,
        )

    stop_detail = stop_loss(
        entry=entry,
        side=side,
        atr=atr,
        invalidation_level=invalidation_level,
        contract=contract,
    )
    if stop_detail is None:
        return refuse(
            PlanVerdict.REFUSED,
            ["stop tidak bisa dipasang dari level yang tersedia"],
            integrity=integrity,
        )

    # ---- 4. how much, from the risk budget alone (FUTURES SPEC 19, 20) -----
    size = position_size(
        equity=equity,
        entry=entry,
        stop=stop_detail.price,
        side=side,
        contract=contract,
        **({"risk_pct": risk_pct} if risk_pct is not None else {}),
    )
    if size is None:
        # `position_size` returns None only for a non-positive risk budget. It
        # does NOT return None for the venue-minimum case, which is what this
        # gate used to claim it caught - see the `viable` check below.
        return refuse(
            PlanVerdict.REFUSED,
            [
                "position size tidak bisa dihitung: risk budget bukan angka "
                "positif, jadi equity dan persen risiko keduanya harus di atas "
                "nol"
            ],
            integrity=integrity,
            stop_detail=stop_detail,
        )
    if not size.viable:
        # Quantity floored to zero: below the venue minimum, or a stop sitting
        # on the entry. `PositionSize.viable` existed and was called from
        # nowhere, so a zero-quantity position walked past this gate and into
        # the economics, where `risk_amount == 0` makes `net_rr` None and the
        # refusal rendered the literal text "net reward is Nonex the risk".
        #
        # Any account under roughly $450 on BTCUSDT hit that - the ordinary
        # small-account case - and was told its reward-to-risk had failed when
        # the truth was that its equity could not meet the contract's size
        # floor. Two different problems, one of them fixable by the reader.
        return refuse(
            PlanVerdict.REFUSED,
            list(size.findings)
            or [
                "risk budget tidak menghasilkan kuantitas yang bisa ditradingkan "
                "pada jarak stop ini, dan membulatkannya ke atas berarti "
                "mengambil risiko lebih besar dari yang direncanakan - trade "
                "yang berbeda dari yang dianalisis"
            ],
            integrity=integrity,
            stop_detail=stop_detail,
            size_detail=size,
        )

    # ---- 5. where is the move finished? (FUTURES SPEC 22) ------------------
    profit = take_profit(
        entry=entry,
        side=side,
        atr=atr,
        structure_levels=structure_levels,
        contract=contract,
    )
    # The nearest level, not the furthest. FUTURES SPEC 23 scores the plan on
    # the target it is actually sized against, and picking the furthest would
    # inflate every reward ratio with the level least likely to be reached.
    target = profit.first
    if target is None:
        return refuse(
            PlanVerdict.REFUSED,
            [
                "tidak ada target yang bisa dicapai: setiap level di depan lebih "
                "jauh dari jarak yang biasanya ditempuh market dalam horizon "
                "ini, dan target yang dicapai market sekali per kuartal bukan "
                "sebuah plan"
            ],
            integrity=integrity,
            stop_detail=stop_detail,
            size_detail=size,
        )

    # ---- 6. what does holding it cost? (FUTURES SPEC 27) -------------------
    funding = None
    funding_cost_pct = Decimal(0)
    if snapshot.funding is not None:
        # Counted, not prorated - and counted the same way the horizon
        # projection counts it, so one plan carries one funding cost. Prorating
        # charged a one-hour hold an eighth of an eight-hour payment while the
        # horizon caveat charged it zero or one whole settlement, and both
        # numbers appeared in the same document.
        settlements, _exact = settlements_in(
            snapshot.funding, horizon_hours, reference=now
        )
        funding = analyse_funding(
            snapshot.funding,
            funding_history,
            horizon_hours=horizon_hours,
            # Without the side its caveat could only say "costs", and said it to
            # the side being paid - while the arithmetic below charged that same
            # side correctly. The number and the sentence disagreed.
            side=side,
            settlements=settlements,
        )
        if funding.projected_cost_pct is not None:
            # A long pays a positive rate; a short receives it. Dropping the
            # sign here would charge a short for funding it collects.
            direction = Decimal(1) if side is PositionSide.LONG else Decimal(-1)
            # Dibulatkan ke skala kolomnya, ``DECIMAL(14,6)``. Tarif funding
            # datang dari pembagian - delapan jam dibagi periode settlement -
            # dan membawa sisa pembagian Decimal sepanjang dua puluh delapan
            # angka. MySQL membulatkannya sendiri lalu mengeluh, satu baris
            # ``Data truncated for column 'funding_cost_pct'`` pada setiap plan
            # yang tersimpan.
            #
            # Sama seperti ``expected_net_pnl``: yang dibuang ada di bawah
            # 1e-6 persen, jadi bukan uang yang hilang - yang hilang adalah log
            # yang bisa dibaca.
            funding_cost_pct = (funding.projected_cost_pct * direction).quantize(
                FUNDING_PCT_SCALE
            )

    # ---- 7. is it worth taking after every cost? (FUTURES SPEC 23) ---------
    economics = trade_economics(
        size=size,
        entry=entry,
        stop=stop_detail.price,
        target=target,
        contract=contract,
        funding_cost_pct=funding_cost_pct,
    )
    if not economics.worth_taking:
        # `net_rr` is None whenever the planned risk is zero, and interpolating
        # it produced "net reward is Nonex the risk" - a fabricated-looking
        # figure in the one sentence explaining a refusal. The zero-risk case
        # is now caught above, and this guard makes the string incapable of
        # regressing into it.
        ratio = economics.net_rr
        detail = (
            f"net reward adalah {ratio}x dari risiko setelah fees, funding dan "
            "slippage"
            if ratio is not None
            else "rasio risk/reward tidak bisa dihitung: risiko yang "
            "direncanakan nol, jadi tidak ada yang bisa dibandingkan dengan "
            "batas bawahnya"
        )
        return refuse(
            PlanVerdict.REFUSED,
            [
                f"{detail}. Biayanya sudah tentu, edge-nya tidak, jadi ini "
                "tidak layak diambil"
            ],
            integrity=integrity,
            stop_detail=stop_detail,
            size_detail=size,
            economics=economics,
            funding=funding,
        )

    # ---- 8. can the book carry it? (FUTURES SPEC 29) -----------------------
    liquidity = None
    if snapshot.order_book is not None:
        liquidity = assess_liquidity(
            snapshot.order_book, size.notional, buying=side is PositionSide.LONG
        )
        if not liquidity.tradeable:
            return refuse(
                PlanVerdict.REFUSED,
                [
                    "order book tidak akan menampung size ini dengan bersih: "
                    + "; ".join(liquidity.findings)
                ],
                integrity=integrity,
                stop_detail=stop_detail,
                size_detail=size,
                economics=economics,
                liquidity=liquidity,
                funding=funding,
            )

    # ---- 9. how much leverage is safe? (FUTURES SPEC 15-18, 32) ------------
    options = stress_test(
        entry=entry,
        stop=stop_detail.price,
        quantity=size.quantity,
        side=side,
        contract=contract,
        atr=atr,
        hostile_regime=hostile_regime,
        poor_liquidity=liquidity is not None and not liquidity.tradeable,
        extreme_funding=funding is not None and funding.extreme,
        funding=snapshot.funding,
        horizon_hours=horizon_hours,
        reference=now,
    )
    margin_mode, mode_reason = advise_margin_mode(
        open_positions=open_positions, correlated_exposure=correlated_exposure
    )
    leverage_decision = recommend_leverage(options, margin_mode=margin_mode)

    # The projection belonging to the rung actually chosen - never merely the
    # first rung that has one. Each rung posts a different margin, so each
    # projects a different liquidation price, and taking the wrong one printed
    # two contradictory liquidation prices in the same list of caveats: the
    # chosen 10x rung's alongside the 2x rung's, 25,000 apart, both stated as
    # fact. Where no rung was chosen, the closest one is used so that the figure
    # at least belongs to something.
    projection = (
        leverage_decision.chosen.projection
        if leverage_decision.chosen is not None
        else max(options, key=lambda o: o.safety_score).projection
        if options
        else None
    )

    if leverage_decision.refused or leverage_decision.chosen is None:
        return refuse(
            PlanVerdict.REFUSED,
            list(leverage_decision.reasoning),
            integrity=integrity,
            stop_detail=stop_detail,
            size_detail=size,
            economics=economics,
            leverage_decision=leverage_decision,
            projection=projection,
            liquidity=liquidity,
            funding=funding,
            margin_mode=margin_mode,
            margin_mode_reason=mode_reason,
        )

    chosen = leverage_decision.chosen

    # ---- 10. does it survive a worse fill? (FUTURES SPEC 30) --------------
    slippage = stress_slippage(
        entry=entry,
        stop=stop_detail.price,
        target=target,
        quantity=size.quantity,
        side=side,
        funding_cost_pct=max(funding_cost_pct, Decimal(0)),
    )

    caveats = _caveats(
        stop_detail=stop_detail,
        profit=profit,
        size=size,
        economics=economics,
        chosen_findings=chosen.findings,
        projection=projection,
        slippage=slippage,
        liquidity=liquidity,
        funding=funding,
        contract=contract,
        integrity=integrity,
        discipline=discipline,
    )

    return FuturesPlan(
        signal_id=signal_id,
        symbol=snapshot.symbol,
        side=side,
        verdict=PlanVerdict.PLAN,
        horizon_hours=horizon_hours,
        created_at=now,
        reference_price=entry,
        entry=entry,
        stop=stop_detail.price,
        target=target,
        quantity=size.quantity,
        notional=size.notional,
        leverage=chosen.leverage,
        margin_mode=margin_mode,
        margin_required=chosen.margin_required,
        liquidation=chosen.liquidation,
        buffer=chosen.buffer,
        net_rr=economics.net_rr,
        expected_net_pnl=economics.net_reward,
        integrity=integrity,
        stop_detail=stop_detail,
        size_detail=size,
        economics=economics,
        leverage_decision=leverage_decision,
        projection=projection,
        slippage=slippage,
        liquidity=liquidity,
        funding=funding,
        caveats=caveats,
        margin_mode_reason=mode_reason,
        tick_size=contract.tick_size if contract else None,
    )


def _caveats(
    *,
    stop_detail: StopLoss,
    profit: TakeProfit,
    size: PositionSize,
    economics: TradeEconomics,
    chosen_findings: tuple[str, ...],
    projection: HorizonProjection | None,
    slippage: SlippageReport,
    liquidity: LiquidityVerdict | None,
    funding: FundingAnalysis | None,
    contract: ContractSpec | None,
    integrity: IntegrityReport,
    discipline: DisciplineReport | None = None,
) -> tuple[str, ...]:
    """Everything true about this plan that makes it weaker.

    Collected rather than summarised. A plan that cleared every gate is still a
    plan built on a projected funding rate, an assumed slippage figure and, on
    this venue, a maintenance rate that is not the real one - and the reader is
    owed each of those separately, not an averaged confidence.
    """
    out: list[str] = []
    out.extend(stop_detail.findings)
    # take_profit says whether the target came from a structural level the
    # market actually stalls at, or from an ATR multiple invented because there
    # was no structure ahead. Both were being discarded, so the two targets
    # printed identically - and "64500" derived from 2.5x ATR reads exactly like
    # "64500" derived from a level price has respected four times.
    out.extend(profit.reasons)
    out.extend(profit.findings)
    out.extend(size.findings)
    out.extend(economics.findings)
    # `chosen_findings` already carries the chosen rung's horizon projection -
    # `stress_test` folds it in - so `projection` is deliberately NOT read here.
    # Adding it appended a second copy, and when it belonged to a different rung
    # the two copies disagreed about the liquidation price.
    out.extend(chosen_findings)
    out.extend(slippage.findings)
    if liquidity is not None:
        out.extend(liquidity.findings)
    if funding is not None:
        out.extend(funding.findings)
    if contract is not None:
        out.extend(contract.notes)
    if integrity.findings:
        out.extend(integrity.findings)
    # FUTURES SPEC 34, and last on purpose. Everything above is a property of
    # this setup; these are properties of the sequence it arrives in, and the
    # reader who most needs to see them is the one about to skip the list.
    if discipline is not None:
        out.extend(discipline.as_caveats())
    # Order-preserving dedup: several layers legitimately raise the same
    # caveat, and repeating it makes the list look longer than the concerns are.
    return tuple(dict.fromkeys(out))


def render(plan: FuturesPlan) -> str:
    """The signal as a person reads it (FUTURES SPEC 37-39).

    Refuses to emit any claim FUTURES SPEC 51 prohibits. The check runs on the
    finished text rather than on the templates, so it also catches a forbidden
    phrase arriving from a finding, a council note or a venue message - none of
    which this module writes.
    """
    lines = [f"ARUNA FUTURES - {plan.symbol}", ""]

    if plan.verdict is not PlanVerdict.PLAN:
        # The instruction first, before the diagnosis.
        #
        # This used to open with the verdict code and then explain why there
        # were no numbers - which answers a question the reader did not ask.
        # The reader's question is binary: take something, or not? And "WAIT"
        # as a word invites exactly the wrong reading, because it sounds like a
        # pending state that resolves into a trade later. It is not. Each tick
        # is decided from scratch, and nothing is owed a follow-up.
        #
        # For the reader, all three non-PLAN verdicts are the SAME instruction.
        # The difference between them is diagnostic, not actionable.
        lines += [
            "TIDAK ADA POSISI YANG DIAMBIL.",
            "",
            f"VERDICT: {plan.verdict.value}  -  {_verdict_meaning(plan.verdict)}",
            "",
        ]
        for reason in plan.refusals:
            lines.append(f"  - {reason}")
        lines += [
            "",
            "Tidak ada entry, stop, size atau leverage yang diberikan, karena",
            "tidak ada posisi untuk diberi angka-angka itu.",
            "",
            "ARUNA akan menilai ulang dari nol pada tick berikutnya. Itu bukan",
            "janji bahwa akan ada plan - hasilnya bisa saja sama lagi.",
        ]
        return _guard("\n".join(lines))

    tick = plan.tick_size
    lines += [
        f"SIDE:        {plan.side.value}",
        f"ENTRY:       {_price(plan.entry, tick)}",
        f"STOP:        {_price(plan.stop, tick)}",
        f"TARGET:      {_price(plan.target, tick)}",
        "",
        f"SIZE:        {plan.quantity} ({_money(plan.notional)} notional)",
        f"LEVERAGE:    {plan.leverage}x  {plan.margin_mode.value}",
        f"MARGIN:      {_money(plan.margin_required)}",
        f"LIQUIDATION: {_price(plan.liquidation.price if plan.liquidation else None, tick)}",
        f"BUFFER:      {plan.buffer.band if plan.buffer else 'tidak diketahui'}"
        f" ({plan.buffer.score if plan.buffer else '-'}/100)",
        "",
        f"NET R:R      {plan.net_rr}",
        f"NET PnL      {_money(plan.expected_net_pnl)} jika target tercapai",
        "",
        f"MARGIN MODE: {plan.margin_mode_reason}",
    ]

    if plan.slippage.breaks_at == "normal":
        # "The edge disappears at normal" is not a caveat, it is a contradiction
        # of the NET R:R printed four lines above it. The two figures answer
        # different questions - the economics charges one slippage assumption,
        # the ladder charges it on both ends - so they can disagree, and when
        # they do the reader is owed the disagreement rather than a phrase that
        # reads like a footnote.
        lines.append(
            "SLIPPAGE:    tangga leverage tidak menemukan edge bahkan pada fill "
            "yang bersih, dan itu bertentangan dengan NET R:R di atas"
        )
        lines.append(
            "             (tangga membebankan slippage di entry DAN exit; R:R "
            "membebankan satu asumsi). Anggap setup ini belum terbukti."
        )
    elif plan.slippage.breaks_at:
        lines.append(
            f"SLIPPAGE:    edge hilang pada {plan.slippage.breaks_at}"
        )

    if plan.caveats:
        lines += ["", "YANG MELEMAHKAN INI:"]
        lines += [f"  - {c}" for c in plan.caveats]

    lines += [
        "",
        "Ini analisis, bukan instruksi. ARUNA tidak memasang order, tidak",
        "mengubah setting leverage atau margin, dan tidak memindahkan dana.",
        "Eksekusi, abaikan, atau timpa - keputusan itu bukan milik ARUNA.",
        "",
        "Stop adalah tempat ide ini terbukti salah. Kalau stop kena, idenya",
        "memang salah, dan itulah hasil yang sudah direncanakan - bukan",
        "kecelakaan.",
    ]
    return _guard("\n".join(lines))


def _verdict_meaning(verdict: PlanVerdict) -> str:
    """One line saying what a verdict means, in plain terms.

    The codes are kept as identifiers - they are stored, constrained by the
    schema, and shared with the spot council - but a code alone leaves the
    reader to guess, and "WAIT" is the one they guess wrong. It reads as
    "hold on, something is coming", when what it records is that the council
    saw nothing to take.
    """
    return {
        PlanVerdict.WAIT: "council tidak melihat setup yang layak diambil",
        PlanVerdict.REFUSED: "ada arah, tapi aritmetikanya menolak",
        PlanVerdict.NO_SIGNAL: "datanya tidak layak dipakai untuk memutuskan",
        PlanVerdict.PLAN: "lolos semua gate",
    }[verdict]


def _guard(text: str) -> str:
    lowered = text.lower()
    for claim in FORBIDDEN_CLAIMS:
        if claim in lowered:
            raise ForbiddenClaim(
                f"menolak mengeluarkan sinyal futures yang memuat {claim!r}: "
                "FUTURES SPEC 51 melarangnya, dan tidak ada kondisi market yang "
                "membuatnya benar. Tidak ada yang bisa dipastikan di sini, dan "
                "laporan yang mengatakan sebaliknya sudah salah sebelum dibaca"
            )
    return text


__all__ = [
    "FORBIDDEN_CLAIMS",
    "ForbiddenClaim",
    "FuturesPlan",
    "PlanVerdict",
    "build_plan",
    "render",
]
