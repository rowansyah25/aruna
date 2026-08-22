"""Leverage engine (FUTURES SPEC 15, 16, 17, 18, 31, 32, 36).

**With risk-based sizing, leverage does not change how much you win or lose.**

That sentence is the whole design, and it is worth stating plainly because it
contradicts what leverage is usually assumed to do. F3 sized the position from
the risk budget and the stop distance, so:

* the loss when the stop fills is the risk budget - at every leverage;
* the profit when the target fills is the same figure - at every leverage;
* what changes is the **margin posted**, and therefore **how far away the
  liquidation price sits**.

So leverage is not a profit dial. It is a safety dial with a capital cost:
higher leverage frees margin and moves liquidation closer; lower leverage ties
up margin and pushes it away. Anyone reaching for more leverage to make more
money has misunderstood the arithmetic, and FUTURES SPEC 16 forbids the
behaviour this module makes impossible - there is no profit target parameter
anywhere in it.

FUTURES SPEC 15 wants **one number**, not a range. :func:`recommend_leverage`
returns the highest candidate whose safety still clears the bar, because beyond
that point extra leverage buys nothing at all - not more profit, only a nearer
liquidation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aruna.futures.horizon import HorizonProjection, project_to_horizon
from aruna.futures.liquidation import (
    BufferScore,
    buffer_score,
    liquidation_price,
)
from aruna.futures.models import ContractSpec, FundingRate, MarginMode, PositionSide

#: The ladder FUTURES SPEC 18 names. 1x is always available and needs no test:
#: it is the safest possible choice and the fallback when none of these clear.
CANDIDATES: tuple[int, ...] = (2, 3, 4, 5, 7, 10)

#: Buffer score a leverage must reach to be recommendable.
MIN_SAFE_BUFFER = 75

#: Safety score bands (FUTURES SPEC 36), same shape as the buffer bands.
BANDS: tuple[tuple[int, str], ...] = (
    (90, "EXCELLENT"),
    (75, "ACCEPTABLE"),
    (60, "CAUTION"),
    (40, "HIGH_RISK"),
    (0, "REJECTED"),
)

#: Penalties applied to the safety score by condition. Each is a *reduction*
#: from the buffer score, never an addition - nothing about a setup makes a
#: liquidation price further away than the arithmetic says it is.
HOSTILE_REGIME_PENALTY = 25
POOR_LIQUIDITY_PENALTY = 20
EXTREME_FUNDING_PENALTY = 10


@dataclass(frozen=True, slots=True)
class LeverageOption:
    """One rung of the ladder, fully costed (FUTURES SPEC 18)."""

    leverage: int
    margin_required: Decimal
    liquidation: Any
    buffer: BufferScore
    safety_score: int
    band: str
    findings: tuple[str, ...] = field(default_factory=tuple)
    #: Set when a horizon was supplied (FUTURES SPEC 32). The rung is then
    #: scored on the *worse* of its entry and end-of-hold states.
    projection: HorizonProjection | None = None

    @property
    def acceptable(self) -> bool:
        return self.safety_score >= MIN_SAFE_BUFFER

    @property
    def rejected(self) -> bool:
        return self.band == "REJECTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "leverage": f"{self.leverage}x",
            "margin_required": str(self.margin_required),
            "liquidation_price": (
                str(self.liquidation.price) if self.liquidation else None
            ),
            "buffer_score": self.buffer.score,
            "safety_score": self.safety_score,
            "band": self.band,
            "acceptable": self.acceptable,
            "findings": list(self.findings),
            "horizon": self.projection.to_dict() if self.projection else None,
        }


@dataclass(frozen=True, slots=True)
class LeverageDecision:
    """One recommended number, and the ladder it was chosen from."""

    recommended: int | None
    options: tuple[LeverageOption, ...]
    margin_mode: MarginMode
    reasoning: tuple[str, ...] = field(default_factory=tuple)
    #: Set when nothing on the ladder was safe enough.
    refused: bool = False

    @property
    def chosen(self) -> LeverageOption | None:
        return next(
            (o for o in self.options if o.leverage == self.recommended), None
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended": f"{self.recommended}x" if self.recommended else None,
            "margin_mode": self.margin_mode.value,
            "refused": self.refused,
            "stress_test": [o.to_dict() for o in self.options],
            "reasoning": list(self.reasoning),
            "note": (
                "dengan sizing berbasis risiko, leverage mengubah margin yang "
                "disetor dan jarak ke liquidation - bukan profit atau "
                "kerugiannya. Leverage tambahan hanya membeli liquidation yang "
                "lebih dekat, tidak ada yang lain (FUTURES SPEC 15, 16)"
            ),
        }


def stress_test(
    *,
    entry: Decimal,
    stop: Decimal,
    quantity: Decimal,
    side: PositionSide,
    contract: ContractSpec | None,
    atr: Decimal | None = None,
    margin_mode: MarginMode = MarginMode.ISOLATED,
    hostile_regime: bool = False,
    poor_liquidity: bool = False,
    extreme_funding: bool = False,
    candidates: tuple[int, ...] = CANDIDATES,
    funding: FundingRate | None = None,
    horizon_hours: float | None = None,
    reference: Any = None,
) -> tuple[LeverageOption, ...]:
    """Cost every rung of the ladder (FUTURES SPEC 18, 32).

    The position is already sized; only the margin changes. Each rung therefore
    produces a different liquidation price and a different buffer, and nothing
    else about the trade moves.

    When ``horizon_hours`` is given, each rung is scored on the **worse** of its
    entry state and its state at the end of the hold (FUTURES SPEC 32). Funding
    is paid out of the posted margin, so a rung that is comfortable at entry can
    be uncomfortable nine settlements later - and the rung has to answer for
    both, exactly as :func:`~aruna.futures.liquidation.buffer_score` already
    takes the worse of its own two measures.
    """
    notional = entry * quantity
    options: list[LeverageOption] = []

    # An unknown venue cap is not a cap of 1. The bracket endpoint needs a
    # signed request this adapter cannot make, so `max_leverage` is routinely
    # None - and treating that as 1x skipped every rung, emptied the ladder, and
    # reported "no safe leverage" for a reason that had nothing to do with
    # safety. Unknown means the ladder runs and the gap is stated.
    cap = contract.max_leverage if contract is not None else None
    cap_unknown = contract is not None and cap is None

    for leverage in candidates:
        if cap is not None and leverage > cap:
            continue
        margin = notional / Decimal(leverage)
        liquidation = liquidation_price(
            entry=entry,
            quantity=quantity,
            side=side,
            margin=margin,
            contract=contract,
            margin_mode=margin_mode,
        )
        buffer = buffer_score(
            entry=entry, stop=stop, liquidation=liquidation, atr=atr
        )

        findings: list[str] = []

        # FUTURES SPEC 32. The projection replaces the buffer rather than
        # adjusting it: `effective_buffer` is the worse of entry and end-of-hold,
        # so a rung cannot pass on its entry state alone.
        projection: HorizonProjection | None = None
        if horizon_hours is not None:
            projection = project_to_horizon(
                entry=entry,
                stop=stop,
                quantity=quantity,
                side=side,
                margin=margin,
                contract=contract,
                funding=funding,
                horizon_hours=horizon_hours,
                atr=atr,
                margin_mode=margin_mode,
                reference=reference,
            )
            buffer = projection.effective_buffer
            findings.extend(projection.findings)

        score = buffer.score
        if hostile_regime:
            score -= HOSTILE_REGIME_PENALTY
            findings.append(
                "regime sedang tidak bersahabat dengan leverage: posisi ini "
                "terekspos ke lebih dari sekadar tesisnya sendiri"
            )
        if poor_liquidity:
            score -= POOR_LIQUIDITY_PENALTY
            findings.append(
                "likuiditas tidak akan menampung size ini dengan bersih, jadi "
                "stop bisa terisi jauh dari levelnya"
            )
        if extreme_funding:
            score -= EXTREME_FUNDING_PENALTY
            findings.append(
                "funding sedang ekstrem; biaya menahan posisi menumpuk di atas "
                "risikonya"
            )

        if cap_unknown:
            findings.append(
                "batas leverage milik venue tidak berhasil diambil, jadi tidak "
                "diketahui apakah tingkat ini diizinkan - cek dulu sebelum "
                "bertindak"
            )
        score = max(0, min(100, score))
        band = next(name for floor, name in BANDS if score >= floor)
        findings.extend(buffer.findings)

        options.append(
            LeverageOption(
                leverage=leverage,
                margin_required=margin,
                liquidation=liquidation,
                buffer=buffer,
                safety_score=score,
                band=band,
                findings=tuple(findings),
                projection=projection,
            )
        )
    return tuple(options)


def recommend_leverage(
    options: tuple[LeverageOption, ...],
    *,
    proposals: dict[str, int] | None = None,
    margin_mode: MarginMode = MarginMode.ISOLATED,
) -> LeverageDecision:
    """Pick one number (FUTURES SPEC 15, 17).

    The highest rung that still clears :data:`MIN_SAFE_BUFFER`. Higher would
    move liquidation closer while changing neither the profit nor the loss;
    lower would tie up margin for a safety margin already sufficient.

    ``proposals`` are what the council members argued for (FUTURES SPEC 17).
    They can only ever pull the recommendation *down*: an agent may object that
    the arithmetic is too aggressive, but no agent may talk the engine past a
    rung the liquidation maths refused. Argument does not move a liquidation
    price.
    """
    reasoning: list[str] = []

    # FUTURES SPEC 32. Stated up front, because "3x for a three-day hold" and
    # "3x" are different claims and the reader cannot tell them apart otherwise.
    projection = next((o.projection for o in options if o.projection), None)
    if projection is not None:
        if projection.refused:
            reasoning.append(
                f"horizon {projection.horizon_hours:g}h tidak diproyeksikan: "
                + "; ".join(projection.findings)
            )
        elif not projection.decay_known:
            reasoning.append(
                f"tangga ini hanya dinilai pada kondisi entry - penyusutan "
                f"margin selama {projection.horizon_hours:g}h tidak bisa "
                "dihitung, dan itu berarti tidak diketahui, bukan nol"
            )
        else:
            reasoning.append(
                f"setiap tingkat dinilai dari yang lebih buruk antara kondisi "
                f"entry-nya dan kondisinya setelah {projection.settlements} "
                f"funding settlement selama {projection.horizon_hours:g}h"
            )

    acceptable = [o for o in options if o.acceptable]
    if not acceptable:
        best = max(options, key=lambda o: o.safety_score, default=None)
        reasoning.append(
            # Deliberately not "tidak ada leverage aman di tangga": the phrase
            # "leverage aman" is on FORBIDDEN_CLAIMS (FUTURES SPEC 51) and
            # plan.render() would raise on this refusal rather than emit it.
            "tidak ada tingkat leverage di tangga yang buffer-nya memadai"
            + (
                f"; yang terbaik adalah {best.leverage}x dengan skor "
                f"{best.safety_score}/100"
                if best
                else ""
            )
        )
        reasoning.append(
            "1x tetap tersedia dan selalu jadi pilihan yang paling minim "
            "risiko, tapi tidak ada apa pun di sini yang merekomendasikan "
            "posisi ber-leverage pada setup ini (FUTURES SPEC 52)"
        )
        return LeverageDecision(
            recommended=None,
            options=options,
            margin_mode=margin_mode,
            reasoning=tuple(reasoning),
            refused=True,
        )

    choice = max(acceptable, key=lambda o: o.leverage)
    reasoning.append(
        f"{choice.leverage}x adalah tingkat tertinggi yang skor keamanannya "
        f"masih melewati {MIN_SAFE_BUFFER}/100 (skornya {choice.safety_score})"
    )
    reasoning.append(
        "leverage lebih besar hanya akan mendekatkan liquidation tanpa "
        "mengubah profit di target maupun kerugian di stop - posisi ini sudah "
        "di-size berdasarkan risiko"
    )

    if proposals:
        lowest_name, lowest = min(proposals.items(), key=lambda kv: kv[1])
        if lowest < choice.leverage:
            reasoning.append(
                f"{lowest_name} mengusulkan {lowest}x, di bawah "
                f"{choice.leverage}x hasil aritmetika - yang diambil adalah "
                "yang lebih rendah, karena keberatan yang beralasan boleh "
                "menurunkan leverage dan tidak ada yang boleh menaikkannya"
            )
            capped = [o for o in acceptable if o.leverage <= lowest]
            if capped:
                choice = max(capped, key=lambda o: o.leverage)
            else:
                reasoning.append(
                    f"tidak ada tingkat pada atau di bawah {lowest}x yang "
                    "melewati batas keamanan, jadi tidak ada posisi "
                    "ber-leverage yang direkomendasikan"
                )
                return LeverageDecision(
                    recommended=None,
                    options=options,
                    margin_mode=margin_mode,
                    reasoning=tuple(reasoning),
                    refused=True,
                )
        highest_name, highest = max(proposals.items(), key=lambda kv: kv[1])
        if highest > choice.leverage:
            reasoning.append(
                f"{highest_name} mengusulkan {highest}x dan ditolak: argumen "
                "tidak menggeser liquidation price"
            )

    return LeverageDecision(
        recommended=choice.leverage,
        options=options,
        margin_mode=margin_mode,
        reasoning=tuple(reasoning),
    )


def advise_margin_mode(
    *,
    open_positions: int = 0,
    correlated_exposure: bool = False,
) -> tuple[MarginMode, str]:
    """Recommend a margin mode (FUTURES SPEC 31).

    ARUNA never changes an account setting; this is advice for the person who
    does. ISOLATED by default because it bounds the damage of one bad idea to
    the margin posted against it - CROSS lets a single position reach the whole
    wallet, and the liquidation price it produces is a best case that other
    positions move.
    """
    if open_positions > 0 and correlated_exposure:
        return MarginMode.ISOLATED, (
            f"{open_positions} posisi berkorelasi sudah terbuka: di bawah "
            "CROSS semuanya berbagi satu margin pool, jadi satu liquidation "
            "bisa cascade ke semuanya"
        )
    return MarginMode.ISOLATED, (
        "ISOLATED membatasi kerugian pada margin yang disetor untuk ide ini; "
        "CROSS mengekspos seluruh wallet ke ide ini"
    )


__all__ = [
    "BANDS",
    "CANDIDATES",
    "EXTREME_FUNDING_PENALTY",
    "HOSTILE_REGIME_PENALTY",
    "MIN_SAFE_BUFFER",
    "POOR_LIQUIDITY_PENALTY",
    "LeverageDecision",
    "LeverageOption",
    "advise_margin_mode",
    "recommend_leverage",
    "stress_test",
]
