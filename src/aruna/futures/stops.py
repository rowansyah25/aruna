"""Stop loss and take profit (FUTURES SPEC 21, 22).

Both engines answer one question: **where is this idea wrong, and where is it
finished?** Neither answers "where does the ratio look good", and the code is
arranged so it cannot.

FUTURES SPEC 21 is blunt about the failure mode: *jangan memaksakan SL hanya
agar R:R terlihat bagus* - do not force the stop just to make the ratio look
good. That is enforced structurally rather than promised: :func:`stop_loss`
takes no target, no reward, no ratio and no equity. It cannot be tuned toward a
number it has never been shown.

The stop comes from where the market would prove the idea wrong - the swing
that must hold, padded by the volatility that would otherwise stop it out on
noise. The target comes from the structure ahead of price. If the resulting
ratio is poor, that is a fact about the setup, and FUTURES SPEC 23 has the
answer for it: no trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aruna.futures.models import ContractSpec, PositionSide

#: Padding beyond the invalidation level, in ATR. A stop exactly on the swing
#: gets taken out by the wick that tests it; this is the room for the test to
#: happen without the idea being wrong.
ATR_PADDING = Decimal("0.5")

#: Fallback stop distance when no structure is available, in ATR. Wider than
#: the padding on purpose - with nothing to lean on, the stop has to survive
#: ordinary movement rather than sit inside it.
ATR_FALLBACK = Decimal("1.5")

#: A stop closer than this to entry, in ATR, is inside the noise.
#:
#: Must exceed :data:`ATR_PADDING`, and that is not a style preference. A
#: structural stop sits at ``(entry - level) + ATR_PADDING``, so its distance is
#: always greater than the padding - a threshold at or below the padding could
#: never fire, and the check would be decoration.
MIN_ATR_DISTANCE = Decimal("0.75")

#: A structural target closer than this to entry, in ATR, is inside the noise.
#:
#: The mirror of :data:`MIN_ATR_DISTANCE`, and it was missing. ``take_profit``
#: had a ceiling - a level beyond 6 ATR is reached once a quarter and is not a
#: plan - and no floor at all, so a level a third of an ATR away was accepted
#: as a destination. The market crosses that in minutes, in either direction.
#:
#: Measured on live BTCUSDT: a 4.85-ATR structural stop paired with a 0.31-ATR
#: structural target gave a gross R:R of 0.06, and the plan was refused for
#: "reward too small" - when the truth was that the target had never been one.
#:
#: One ATR because that is the nearest rung the ATR fallback itself offers: a
#: structural level closer than the fallback's own first step is worse than the
#: fallback, so preferring it is preferring the weaker basis.
MIN_TARGET_ATR = Decimal("1.0")

#: A stop beyond this many ATR is named as wide. Not capped - see the finding
#: that uses it. Three ATR because the buffer engine already treats three ATR
#: as the distance ordinary movement covers (MIN_ATR_TO_LIQUIDATION), so beyond
#: it the stop is further away than the market's own routine range.
WIDE_STOP_ATR = Decimal("3.0")


@dataclass(frozen=True, slots=True)
class StopLoss:
    price: Decimal
    distance: Decimal
    distance_pct: Decimal
    #: What has to happen for the idea to be wrong, in words.
    invalidation: str
    #: True when no structure was available and ATR alone set the level.
    from_volatility_only: bool = False
    findings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": str(self.price),
            "distance": str(self.distance),
            "distance_pct": str(self.distance_pct),
            "invalidation": self.invalidation,
            "from_volatility_only": self.from_volatility_only,
            "findings": list(self.findings),
            "note": (
                "dipasang di tempat ide ini terbukti salah, bukan di tempat "
                "rasio terlihat bagus; engine ini tidak pernah diperlihatkan "
                "target (FUTURES SPEC 21)"
            ),
        }


@dataclass(frozen=True, slots=True)
class TakeProfit:
    """Up to three exits, nearest first (FUTURES SPEC 22)."""

    levels: tuple[Decimal, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def first(self) -> Decimal | None:
        return self.levels[0] if self.levels else None

    @property
    def furthest(self) -> Decimal | None:
        return self.levels[-1] if self.levels else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "levels": [str(level) for level in self.levels],
            "reasons": list(self.reasons),
            "findings": list(self.findings),
        }


def stop_loss(
    *,
    entry: Decimal,
    side: PositionSide,
    atr: Decimal,
    invalidation_level: Decimal | None = None,
    contract: ContractSpec | None = None,
) -> StopLoss | None:
    """Where this idea is proven wrong (FUTURES SPEC 21).

    ``invalidation_level`` is the structural level that must hold - the swing
    low under a long, the swing high over a short. When it is absent the stop
    falls back to volatility alone and says so, because a stop with nothing to
    lean on is a worse stop and the caller should know which kind it got.

    Takes no target and no ratio, by design. See the module docstring.
    """
    if entry <= 0 or atr <= 0:
        return None

    findings: list[str] = []
    is_long = side is PositionSide.LONG

    if invalidation_level is not None and _is_beyond(invalidation_level, entry, is_long):
        padding = atr * ATR_PADDING
        price = (
            invalidation_level - padding if is_long else invalidation_level + padding
        )
        invalidation = (
            f"{'di bawah' if is_long else 'di atas'} {invalidation_level}, level "
            "yang harus bertahan supaya ide ini masih berlaku"
        )
        from_volatility = False
        findings.append(
            f"stop berada {ATR_PADDING} ATR di luar level invalidasi supaya wick "
            "yang mengetes level itu tidak menutup posisi"
        )
    else:
        if invalidation_level is not None:
            findings.append(
                f"level invalidasi {invalidation_level} yang diberikan ada di "
                "sisi entry yang salah dan diabaikan"
            )
        distance = atr * ATR_FALLBACK
        price = entry - distance if is_long else entry + distance
        invalidation = (
            f"{ATR_FALLBACK} ATR melawan posisi, tanpa level struktur untuk "
            "dijadikan sandaran"
        )
        from_volatility = True
        findings.append(
            "tidak ada struktur yang bisa dipakai: stop ini murni dari "
            "volatilitas, dan itu stop yang lebih lemah daripada stop yang "
            "dipasang di sebuah level"
        )

    if contract is not None:
        price = _round_to_tick(price, contract.tick_size, down=is_long)

    if price <= 0:
        return None

    distance = abs(entry - price)
    if distance < atr * MIN_ATR_DISTANCE:
        findings.append(
            f"stop hanya {distance / atr:.2f} ATR dari entry - masih di dalam "
            "noise, dan besar kemungkinan kena oleh pergerakan yang tidak "
            "berarti apa-apa"
        )
    elif distance > atr * WIDE_STOP_ATR:
        # Deliberately a finding, not a cap. The stop is where the idea is
        # proven wrong, and moving it closer to flatter the ratio would put it
        # somewhere the structure does not support - which is the exact
        # tuning F3 built this module to make impossible.
        #
        # But a stop this wide is information: it says the level that must hold
        # is far away, so the position risks a lot per unit of conviction, and
        # the reward gate will be hard to clear. Silence about that reads as
        # "nothing unusual here".
        findings.append(
            f"stop ini {distance / atr:.1f} ATR dari entry - lebar. Level yang "
            "harus bertahan letaknya jauh, jadi posisi ini mempertaruhkan "
            "banyak untuk setiap satuan keyakinan, dan target harus jauh juga "
            "supaya R:R-nya masuk akal"
        )

    return StopLoss(
        price=price,
        distance=distance,
        distance_pct=(distance / entry * 100).quantize(Decimal("0.000001")),
        invalidation=invalidation,
        from_volatility_only=from_volatility,
        findings=tuple(findings),
    )


def take_profit(
    *,
    entry: Decimal,
    side: PositionSide,
    atr: Decimal,
    structure_levels: tuple[Decimal, ...] = (),
    contract: ContractSpec | None = None,
    max_levels: int = 3,
) -> TakeProfit:
    """Where the move is finished (FUTURES SPEC 22).

    Prefers structure ahead of price - the levels a move actually stalls at -
    and falls back to ATR multiples when there is none. Levels beyond a few ATR
    are dropped rather than listed: a target the market reaches once a quarter
    is not a plan, and FUTURES SPEC 22 asks for realistic ones.
    """
    findings: list[str] = []
    reasons: list[str] = []
    is_long = side is PositionSide.LONG

    ahead = sorted(
        (level for level in structure_levels if _is_beyond(level, entry, not is_long)),
        reverse=not is_long,
    )
    reachable = [
        level
        for level in ahead
        if MIN_TARGET_ATR * atr <= abs(level - entry) <= atr * Decimal(6)
    ]
    too_far = sum(1 for level in ahead if abs(level - entry) > atr * Decimal(6))
    too_near = sum(
        1 for level in ahead if abs(level - entry) < MIN_TARGET_ATR * atr
    )
    if too_far:
        findings.append(
            f"{too_far} level struktur di luar 6 ATR dibuang - target yang "
            "dicapai market sekali per kuartal bukan sebuah plan"
        )
    if too_near:
        findings.append(
            f"{too_near} level struktur di dalam {MIN_TARGET_ATR} ATR dibuang - "
            "target sedekat itu ada di dalam noise, dilintasi market dalam "
            "hitungan menit ke dua arah, jadi itu bukan tujuan"
        )

    levels: list[Decimal] = []
    for level in reachable[:max_levels]:
        levels.append(level)
        reasons.append(f"struktur di {level}")

    if not levels:
        findings.append(
            "tidak ada struktur yang bisa dicapai di depan harga: target jatuh "
            "kembali ke kelipatan ATR, dan itu dasar yang lebih lemah daripada "
            "level yang memang dihormati market"
        )
        for multiple in (Decimal(1), Decimal(2), Decimal(3))[:max_levels]:
            distance = atr * multiple
            levels.append(entry + distance if is_long else entry - distance)
            reasons.append(f"{multiple} ATR dari entry")

    if contract is not None:
        levels = [
            _round_to_tick(level, contract.tick_size, down=is_long) for level in levels
        ]

    levels = [level for level in levels if level > 0]
    return TakeProfit(
        levels=tuple(levels), reasons=tuple(reasons), findings=tuple(findings)
    )


def _is_beyond(level: Decimal, entry: Decimal, below: bool) -> bool:
    """Is ``level`` on the side of ``entry`` this position needs it to be?"""
    return level < entry if below else level > entry


def _round_to_tick(price: Decimal, tick: Decimal, *, down: bool) -> Decimal:
    """Round to a price the venue accepts, always against the position.

    A stop rounded *toward* entry is tighter than intended and a target rounded
    *away* is further than intended; both flatter the setup. Rounding against
    the position costs a tick and never overstates the plan.
    """
    if tick <= 0:
        return price
    units = price / tick
    floored = units.to_integral_value(rounding="ROUND_FLOOR")
    if not down and floored != units:
        floored += 1
    return floored * tick


__all__ = [
    "ATR_FALLBACK",
    "ATR_PADDING",
    "MIN_ATR_DISTANCE",
    "StopLoss",
    "TakeProfit",
    "stop_loss",
    "take_profit",
]
