"""What actually happened to the plans (FUTURES SPEC 40-45, 48).

The spot learning layer asks whether a direction was right. A perpetual needs a
question spot never has to ask: **did the exchange close the position before the
trader could?** A liquidation is not a bad outcome on a spectrum with a bad
fill. It is the plan having been wrong about the one thing it promised to be
right about, because every risk figure in it was derived from a stop that never
got the chance to fill.

So this module scores five endings, and treats one of them differently from the
rest.

**Sample thresholds do not apply to liquidations.** Everywhere else in ARUNA a
figure below its threshold reports ``INSUFFICIENT_SAMPLE``, because reading a
trend from four observations is how a system fools itself. A liquidation is not
a trend. One liquidation on a plan ARUNA recommended is a defect in the leverage
engine, reportable at n=1, and averaging it into a win rate is exactly the kind
of arithmetic that would hide it.

**Nothing here has measured anything yet.** No futures plan has been stored, let
alone resolved. Every figure this module produces today reports insufficient
sample, and :meth:`FuturesLearningReport.verdict` says so in the first sentence.
That sentence is the most important one in the module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.core.clock import wib
from aruna.futures.models import PositionSide
from aruna.futures.plan import FuturesPlan, PlanVerdict

#: Resolved plans needed before a win rate means anything. Matched to the spot
#: layer's MIN_TOTAL_SAMPLE so a futures figure and a spot figure are read on
#: the same terms.
MIN_SAMPLE = 50

#: Settled funding observations needed before the projection can be scored
#: against reality.
MIN_FUNDING_SAMPLE = 20


#: A move smaller than this, as a percent of the reference price, could not
#: have paid for a round trip. Derived from the cost floor the spot system
#: measured: 0.70% round-trip against 0.398% average targets was an
#: arithmetically guaranteed loss, so a move under that is not an opportunity
#: that was missed - it is one that never existed.
GHOST_MOVE_PCT = Decimal("0.70")


class PlanOutcome(StrEnum):
    TARGET_HIT = "TARGET_HIT"
    STOPPED_OUT = "STOPPED_OUT"
    #: The exchange closed it. The stop was decorative and so was the risk plan.
    LIQUIDATED = "LIQUIDATED"
    #: The horizon elapsed with neither level reached.
    EXPIRED = "EXPIRED"
    OPEN = "OPEN"


class GhostVerdict(StrEnum):
    """Was standing aside right? (FUTURES SPEC 40-45, after spot's SPEC 28.)

    Deliberately not "would we have won". A WAIT states no direction, so no
    outcome can vindicate one - claiming a missed win would be inventing a call
    ARUNA never made. What can be measured is whether a move large enough to
    pay for itself existed at all.
    """

    #: The market went nowhere worth trading. Standing aside cost nothing.
    QUIET = "QUIET"
    #: A move large enough to cover a round trip happened. ARUNA did not say
    #: which way, so this is an opportunity that existed - not one it called.
    MOVE_EXISTED = "MOVE_EXISTED"
    #: No path supplied.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PriceBar:
    """One settled bar of the path a position actually travelled."""

    as_of: datetime
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class PlanResult:
    """How one plan ended, and how close it came to ending worse."""

    signal_id: str
    symbol: str
    side: PositionSide
    outcome: PlanOutcome
    entry: Decimal
    exit_price: Decimal | None
    #: Furthest the position went against itself, in percent of entry.
    max_adverse_pct: Decimal | None = None
    #: True when price reached the liquidation level at any point, whether or
    #: not the stop would have fired first.
    touched_liquidation: bool = False
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.outcome is not PlanOutcome.OPEN

    @property
    def won(self) -> bool:
        return self.outcome is PlanOutcome.TARGET_HIT

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "outcome": self.outcome.value,
            "entry": str(self.entry),
            "exit_price": str(self.exit_price) if self.exit_price else None,
            "max_adverse_pct": (
                str(self.max_adverse_pct)
                if self.max_adverse_pct is not None
                else None
            ),
            "touched_liquidation": self.touched_liquidation,
            "findings": list(self.findings),
        }


@dataclass(frozen=True, slots=True)
class GhostResult:
    """What the market did while ARUNA stood aside."""

    signal_id: str
    symbol: str
    verdict: GhostVerdict
    reference: Decimal
    #: Largest move either way, percent of the reference price.
    max_move_pct: Decimal | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def stood_aside_well(self) -> bool:
        return self.verdict is GhostVerdict.QUIET

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "verdict": self.verdict.value,
            "reference": str(self.reference),
            "max_move_pct": (
                str(self.max_move_pct) if self.max_move_pct is not None else None
            ),
            "findings": list(self.findings),
            "note": (
                "WAIT tidak menyatakan arah apa pun, jadi tidak ada hasil yang "
                "bisa membenarkannya. Ini mengukur apakah ada move yang layak "
                "ditradingkan, bukan apakah ARUNA akan menangkapnya "
                "(FUTURES SPEC 40-45)"
            ),
        }


def score_wait(
    plan: FuturesPlan, path: list[PriceBar], *, reference: Decimal | None = None
) -> GhostResult | None:
    """Judge a WAIT (FUTURES SPEC 40-45, after spot's SPEC 28 ghost signals).

    Standing aside is a decision, and a system that never scores its refusals
    cannot tell diligence from timidity. Futures stored twenty-four WAITs and
    judged none of them until this existed.

    Returns ``None`` for anything that was not a WAIT - a refusal is a
    different question (the gates rejected it) and a plan is scored by
    :func:`score_plan`.
    """
    if plan.verdict is not PlanVerdict.WAIT:
        return None

    anchor = reference if reference is not None else plan.reference_price
    if anchor is None or anchor <= 0:
        return GhostResult(
            signal_id=plan.signal_id,
            symbol=plan.symbol,
            verdict=GhostVerdict.UNKNOWN,
            reference=Decimal(0),
            findings=(
                "tidak ada harga referensi yang tercatat untuk WAIT ini, jadi "
                "apa yang dilakukan market setelahnya tidak bisa diukur "
                "terhadapnya",
            ),
        )

    if not path:
        return GhostResult(
            signal_id=plan.signal_id,
            symbol=plan.symbol,
            verdict=GhostVerdict.UNKNOWN,
            reference=anchor,
            findings=(
                "tidak ada jalur harga yang diberikan, jadi tidak ada yang "
                "bisa diketahui",
            ),
        )

    high = max(bar.high for bar in path)
    low = min(bar.low for bar in path)
    move = max(abs(high - anchor), abs(anchor - low)) / anchor * 100
    move = move.quantize(Decimal("0.001"))

    if move >= GHOST_MOVE_PCT:
        return GhostResult(
            signal_id=plan.signal_id,
            symbol=plan.symbol,
            verdict=GhostVerdict.MOVE_EXISTED,
            reference=anchor,
            max_move_pct=move,
            findings=(
                f"harga bergerak {move}% dari harga referensi selama ARUNA "
                f"diam, melewati {GHOST_MOVE_PCT}% yang dibebankan satu round "
                "trip. Move yang layak ditradingkan memang ada - ARUNA tidak "
                "menyebut arah, jadi ini bukan kemenangan yang terlewat, hanya "
                "peluang yang tidak diambil",
            ),
        )

    return GhostResult(
        signal_id=plan.signal_id,
        symbol=plan.symbol,
        verdict=GhostVerdict.QUIET,
        reference=anchor,
        max_move_pct=move,
        findings=(
            f"harga bergerak paling jauh {move}%, di bawah {GHOST_MOVE_PCT}% "
            "yang dibebankan satu round trip. Diam di sini tidak merugikan "
            "apa pun",
        ),
    )


def score_plan(plan: FuturesPlan, path: list[PriceBar]) -> PlanResult | None:
    """Walk the path a position travelled and say how it ended.

    Returns ``None`` for anything that was never a position: a WAIT, a refusal,
    a NO_SIGNAL. Those are scored by :func:`score_refusals`, which asks a
    different question, and folding them in here would let plans ARUNA declined
    to make count toward the win rate of the ones it did.

    **The order of the checks is the whole point.** Liquidation is tested first
    and per bar, before the stop and before the target, because that is the
    order the exchange applies them. A bar that traded through both the stop and
    the liquidation price is a liquidation - the position was gone.
    """
    if plan.verdict is not PlanVerdict.PLAN:
        return None
    if plan.entry is None or plan.stop is None or plan.target is None:
        return None
    if not path:
        return PlanResult(
            signal_id=plan.signal_id,
            symbol=plan.symbol,
            side=plan.side,
            outcome=PlanOutcome.OPEN,
            entry=plan.entry,
            exit_price=None,
            findings=(
                "tidak ada jalur harga yang diberikan, jadi tidak ada yang "
                "diketahui tentang plan ini",
            ),
        )

    is_long = plan.side is PositionSide.LONG
    liquidation = plan.liquidation.price if plan.liquidation else None
    worst = plan.entry
    touched_liquidation = False
    findings: list[str] = []

    for bar in path:
        adverse = bar.low if is_long else bar.high
        if (adverse < worst) if is_long else (adverse > worst):
            worst = adverse

        # 1. The exchange acts first, and it does not wait its turn.
        if liquidation is not None and _reached(bar, liquidation, is_long):
            touched_liquidation = True
            findings.append(
                f"harga menyentuh level liquidation {liquidation} pada "
                f"{wib(bar.as_of)}. Exchange yang menutup posisi ini; "
                "stop tidak pernah kebagian kesempatan, dan setiap angka risiko "
                "yang diturunkan dari stop itu menggambarkan trade yang tidak "
                "pernah terjadi"
            )
            return PlanResult(
                signal_id=plan.signal_id,
                symbol=plan.symbol,
                side=plan.side,
                outcome=PlanOutcome.LIQUIDATED,
                entry=plan.entry,
                exit_price=liquidation,
                max_adverse_pct=_pct(plan.entry, worst),
                touched_liquidation=True,
                findings=tuple(findings),
            )

        # 2. Then the stop, for the same reason: within one bar, the adverse
        #    level is assumed to be reached before the favourable one. Assuming
        #    the other order would let every plan claim the win on any bar that
        #    straddled both, which flatters the record precisely on the bars
        #    where the outcome was genuinely in doubt.
        if _reached(bar, plan.stop, is_long):
            return PlanResult(
                signal_id=plan.signal_id,
                symbol=plan.symbol,
                side=plan.side,
                outcome=PlanOutcome.STOPPED_OUT,
                entry=plan.entry,
                exit_price=plan.stop,
                max_adverse_pct=_pct(plan.entry, worst),
                touched_liquidation=touched_liquidation,
                findings=(
                    "stop terisi: idenya salah, dan itu memang hasil yang sudah "
                    "direncanakan sejak awal",
                ),
            )

        # 3. Only then the target.
        if _reached(bar, plan.target, not is_long):
            return PlanResult(
                signal_id=plan.signal_id,
                symbol=plan.symbol,
                side=plan.side,
                outcome=PlanOutcome.TARGET_HIT,
                entry=plan.entry,
                exit_price=plan.target,
                max_adverse_pct=_pct(plan.entry, worst),
                touched_liquidation=touched_liquidation,
            )

    return PlanResult(
        signal_id=plan.signal_id,
        symbol=plan.symbol,
        side=plan.side,
        outcome=PlanOutcome.EXPIRED,
        entry=plan.entry,
        exit_price=path[-1].close,
        max_adverse_pct=_pct(plan.entry, worst),
        touched_liquidation=touched_liquidation,
        findings=(
            "horizon habis tanpa satu level pun tersentuh, jadi ini tidak "
            "mengatakan apa pun soal arah - hanya bahwa move-nya tidak datang "
            "dalam waktu yang diklaim",
        ),
    )


def _reached(bar: PriceBar, level: Decimal, downward: bool) -> bool:
    """Did this bar trade through the level, in the direction that matters?"""
    return bar.low <= level if downward else bar.high >= level


def _pct(entry: Decimal, worst: Decimal) -> Decimal | None:
    if entry <= 0:
        return None
    return (abs(worst - entry) / entry * 100).quantize(Decimal("0.001"))


@dataclass(frozen=True, slots=True)
class FuturesLearningReport:
    """What the resolved plans say, and what they are not numerous enough to say."""

    results: tuple[PlanResult, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> tuple[PlanResult, ...]:
        return tuple(r for r in self.results if r.resolved)

    @property
    def total(self) -> int:
        return len(self.resolved)

    @property
    def wins(self) -> int:
        return sum(1 for r in self.resolved if r.won)

    @property
    def liquidations(self) -> tuple[PlanResult, ...]:
        return tuple(
            r for r in self.resolved if r.outcome is PlanOutcome.LIQUIDATED
        )

    @property
    def near_liquidations(self) -> tuple[PlanResult, ...]:
        """Plans that touched the liquidation level but ended another way.

        Reported separately and without a threshold. A plan whose price reached
        liquidation and survived on a wick was not a safe plan that worked; it
        was an unsafe plan that got away with it, and counting it as a win
        launders the distinction.
        """
        return tuple(
            r
            for r in self.resolved
            if r.touched_liquidation and r.outcome is not PlanOutcome.LIQUIDATED
        )

    @property
    def sufficient(self) -> bool:
        return self.total >= MIN_SAMPLE

    @property
    def win_rate(self) -> float | None:
        """``None`` below the sample threshold - never a percentage of four."""
        if not self.sufficient or self.total == 0:
            return None
        return self.wins / self.total

    @property
    def verdict(self) -> str:
        # Liquidations are reported before, and regardless of, the sample
        # threshold. Waiting for fifty of them before mentioning one would be
        # an absurd reading of a rule that exists to prevent over-reading noise.
        if self.liquidations:
            return (
                f"{len(self.liquidations)} LIQUIDATION pada plan yang ARUNA "
                "rekomendasikan. Ini bukan soal win rate dan tidak ada ukuran "
                "sample yang membuatnya jadi soal win rate: leverage engine "
                "menempatkan posisi yang kemudian ditutup oleh exchange, yang "
                "berarti stop-nya cuma hiasan dan setiap angka risiko yang "
                "diturunkan darinya adalah fiksi"
            )
        if not self.sufficient:
            return (
                f"SAMPLE TIDAK CUKUP: {self.total} plan yang sudah selesai, "
                f"{MIN_SAMPLE} dibutuhkan sebelum sebuah win rate berarti "
                "apa-apa. Tidak ada klaim yang dibuat soal apakah aturan-aturan "
                "ini bekerja di futures"
            )
        rate = self.win_rate or 0.0
        return (
            f"{rate:.0%} dari {self.total} plan yang sudah selesai mencapai "
            "target-nya"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_resolved": self.total,
            "wins": self.wins,
            "win_rate": self.win_rate,
            "sufficient_sample": self.sufficient,
            "liquidations": [r.to_dict() for r in self.liquidations],
            "near_liquidations": [r.to_dict() for r in self.near_liquidations],
            "verdict": self.verdict,
            "note": (
                "liquidation dilaporkan pada n=1 dan dikecualikan dari ambang "
                "sample: ini cacat pada leverage engine, bukan satu titik data "
                "dalam sebuah distribusi (FUTURES SPEC 40-45)"
            ),
        }


def daily_report(
    report: FuturesLearningReport,
    *,
    plans_made: int,
    refusals: int,
    waits: int,
    no_signals: int,
    as_of: datetime,
) -> str:
    """The daily account of what ARUNA did and what came of it (SPEC 48).

    The refusals are on the first screen, not buried under the plans. A day on
    which ARUNA produced two plans and declined forty times is a day it mostly
    said no, and a report that leads with the two describes a different system
    from the one that ran.
    """
    considered = plans_made + refusals + waits + no_signals
    lines = [
        "ARUNA FUTURES - HARIAN",
        wib(as_of),
        "",
        f"DITINJAU:       {considered}",
        f"  plan:         {plans_made}",
        f"  ditolak:      {refusals}",
        f"  menunggu:     {waits}",
        f"  tanpa sinyal: {no_signals}",
        "",
    ]

    if considered and not plans_made:
        lines.append(
            "Tidak ada plan yang dikeluarkan hari ini. Itu sebuah output, bukan "
            "kegagalan menghasilkan output."
        )
        lines.append("")

    lines.append(report.verdict)

    if report.liquidations:
        lines += ["", "LIQUIDATED:"]
        for result in report.liquidations:
            lines.append(f"  {result.symbol} {result.side.value} {result.signal_id}")
            lines += [f"    - {f}" for f in result.findings]

    if report.near_liquidations:
        lines += ["", "MENYENTUH LIQUIDATION DAN SELAMAT:"]
        for result in report.near_liquidations:
            lines.append(
                f"  {result.symbol} {result.side.value} {result.signal_id} "
                f"({result.outcome.value})"
            )
        lines.append(
            "  Ini bukan plan aman yang berhasil. Ini plan tidak aman yang "
            "kebetulan lolos."
        )

    lines += [
        "",
        "ARUNA tidak menempatkan order, tidak mengubah pengaturan leverage atau "
        "margin, dan tidak memindahkan dana hari ini, atau di hari mana pun "
        "(FUTURES SPEC 3, 50).",
    ]
    return "\n".join(lines)


__all__ = [
    "GHOST_MOVE_PCT",
    "MIN_FUNDING_SAMPLE",
    "MIN_SAMPLE",
    "FuturesLearningReport",
    "GhostResult",
    "GhostVerdict",
    "PlanOutcome",
    "PlanResult",
    "PriceBar",
    "daily_report",
    "score_plan",
    "score_wait",
]
