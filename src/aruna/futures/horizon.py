"""Leverage per horizon (FUTURES SPEC 32).

The tempting implementation is a table - ``5m -> 10x, 1h -> 7x, 1d -> 3x`` -
and it would be indefensible. Those numbers come from nowhere. They would look
exactly like a measurement while being someone's intuition, which is the thing
FUTURES SPEC 51 and SPEC 49 forbid most directly.

So this module derives the one per-horizon effect that **is** arithmetic:

    In isolated margin, funding is paid out of the posted margin. Less margin
    means the liquidation price sits closer to the entry. A position held
    across nine settlements has paid nine times, and its liquidation price at
    the end of that hold is not the one it started with.

That is the whole idea. The horizon does not change the profit, the loss, or
the stop - F3 and :mod:`aruna.futures.leverage` already establish that leverage
changes only the margin posted and therefore the liquidation distance. A longer
hold spends some of that margin on funding, so it *also* moves liquidation
closer, and by an amount that can be computed rather than guessed.

**What this module refuses to invent.** Longer holds are also exposed to gaps
and to violent wicks that a stop does not survive but a liquidation does. That
exposure is real and this module does not model it, because ARUNA has no
measurement of it. It is named in the findings as unmeasured rather than
converted into a penalty that would look derived.

**The direction is not assumed.** A short position with positive funding
*receives* payment, and its margin grows. Reporting decay in both directions
would be a fabricated conservatism, so the sign is carried through honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from aruna.core.clock import now_utc
from aruna.futures.liquidation import BufferScore, Liquidation, buffer_score, liquidation_price
from aruna.futures.models import ContractSpec, FundingRate, MarginMode, PositionSide

#: Beyond this many settlements the projection stops being a projection.
#:
#: The venue republishes the rate every interval, and only the *next* one is its
#: forecast. Every settlement after that assumes today's rate persists. Assuming
#: it ninety times over - thirty days at eight hours - is not a conservative
#: estimate, it is fiction with a decimal point, so it is refused rather than
#: reported with a caveat nobody reads.
MAX_PROJECTED_SETTLEMENTS = 90


@dataclass(frozen=True, slots=True)
class HorizonProjection:
    """What this position looks like at the end of the hold (SPEC 32)."""

    horizon_hours: float
    #: How many funding payments the hold crosses.
    settlements: int
    #: True when the count came from the venue's own next-settlement time.
    #: False when it was derived from the interval alone and rounded up.
    settlements_exact: bool
    #: Positive = paid out of margin. Negative = received. ``None`` = unknown.
    funding_cost: Decimal | None
    entry_margin: Decimal
    #: ``None`` when there was no funding observation to project from.
    projected_margin: Decimal | None
    entry_liquidation: Liquidation | None
    projected_liquidation: Liquidation | None
    entry_buffer: BufferScore
    #: Equal to :attr:`entry_buffer` when the decay could not be computed - but
    #: :attr:`decay_known` is what tells you which, and callers must check it.
    projected_buffer: BufferScore
    #: False when no funding was supplied. The decay is then *unknown*, which is
    #: not the same as zero, and nothing downstream may treat it as zero.
    decay_known: bool
    refused: bool = False
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def margin_erosion_pct(self) -> Decimal | None:
        """Share of the posted margin spent on funding. Negative = margin grew."""
        if self.projected_margin is None or self.entry_margin <= 0:
            return None
        spent = self.entry_margin - self.projected_margin
        return (spent / self.entry_margin * 100).quantize(Decimal("0.01"))

    @property
    def band_changed(self) -> bool:
        return (
            self.decay_known
            and self.projected_buffer.band != self.entry_buffer.band
        )

    @property
    def effective_buffer(self) -> BufferScore:
        """The worse of the two states.

        The same rule :func:`~aruna.futures.liquidation.buffer_score` already
        applies to its own two measures: a position is only as safe as its
        weakest reading, and averaging entry against horizon-end would let a
        comfortable start paper over an uncomfortable finish.
        """
        if not self.decay_known:
            return self.entry_buffer
        return min(
            (self.entry_buffer, self.projected_buffer), key=lambda b: b.score
        )

    @property
    def material(self) -> bool:
        """Did the horizon actually change the verdict?

        Deliberately derived from the band rather than from a threshold of its
        own. A new constant here would be one more unfounded number, and the
        band boundaries already encode where a difference starts to matter.
        """
        return self.band_changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "horizon_hours": self.horizon_hours,
            "settlements": self.settlements,
            "settlements_exact": self.settlements_exact,
            "funding_cost": (
                str(self.funding_cost) if self.funding_cost is not None else None
            ),
            "entry_margin": str(self.entry_margin),
            "projected_margin": (
                str(self.projected_margin)
                if self.projected_margin is not None
                else None
            ),
            "margin_erosion_pct": (
                str(self.margin_erosion_pct)
                if self.margin_erosion_pct is not None
                else None
            ),
            "entry_liquidation": (
                str(self.entry_liquidation.price) if self.entry_liquidation else None
            ),
            "projected_liquidation": (
                str(self.projected_liquidation.price)
                if self.projected_liquidation
                else None
            ),
            "entry_buffer": self.entry_buffer.to_dict(),
            "projected_buffer": (
                self.projected_buffer.to_dict() if self.decay_known else None
            ),
            "decay_known": self.decay_known,
            "material": self.material,
            "refused": self.refused,
            "findings": list(self.findings),
            "note": (
                "satu-satunya efek per-horizon yang dimodelkan di sini adalah "
                "funding yang dibayar dari margin, yang menggeser harga "
                "liquidation. Eksposur gap dan wick pada hold yang panjang itu "
                "nyata, tidak terukur, dan disebutkan apa adanya alih-alih "
                "diubah jadi penalti (FUTURES SPEC 32)"
            ),
        }


def _at_tick(price: Decimal | None, tick: Decimal | None) -> str:
    """A price at the precision the venue trades in.

    The liquidation formula is a division and returns figures like
    ``56927.71084337349397590361446``. Carrying all of that into a sentence a
    person reads asserts twenty-three digits of precision for a contract whose
    tick is 0.10. The full value stays on :class:`Liquidation`, where callers
    that need it can find it; the prose gets the tradeable number.
    """
    if price is None:
        return "tidak diketahui"
    if tick is None or tick <= 0:
        return str(price)
    return format(price.quantize(tick), "f")


def settlements_in(
    funding: FundingRate,
    horizon_hours: float,
    *,
    reference: datetime | None = None,
) -> tuple[int, bool]:
    """How many funding payments a hold of this length crosses.

    Exact when the venue published its next settlement time: a one-hour scalp
    entered seven hours before the next settlement pays nothing at all, and a
    one-hour scalp entered ten minutes before it pays once. Rounding that to
    "an eighth of a payment" would invent a cost the position never incurs.

    Falls back to the interval alone when there is no next-settlement time -
    a settled historical rate has none - and rounds **up**, because overstating
    a cost is the safe direction. The second return value says which happened,
    and it is not decoration: an inexact count must not be presented as a
    measurement.
    """
    if funding.interval_hours <= 0 or horizon_hours <= 0:
        return 0, True

    interval = Decimal(funding.interval_hours)
    hours = Decimal(str(horizon_hours))

    if funding.next_funding_time is not None:
        now = reference or now_utc()
        end = now + timedelta(hours=horizon_hours)
        first = funding.next_funding_time

        if first <= now:
            # The venue's "next" settlement is already in the past, so this
            # observation is stale. Counting from it charged the position for
            # settlements that had already happened before it was opened - a
            # week-old timestamp added twenty-one phantom payments, and the
            # count was still reported as exact.
            #
            # The cadence is still known, so the schedule is advanced to the
            # next boundary that is genuinely ahead - but the result is marked
            # inexact, because this is now extrapolation from a stale stamp
            # rather than a figure the venue published.
            elapsed = (now - first).total_seconds() / 3600
            steps = int(Decimal(str(elapsed)) / interval) + 1
            first = first + timedelta(hours=float(interval) * steps)
            if end < first:
                return 0, False
            span = (end - first).total_seconds() / 3600
            return int(Decimal(str(span)) / interval) + 1, False

        if end < first:
            return 0, True
        span = (end - first).total_seconds() / 3600
        return int(Decimal(str(span)) / interval) + 1, True

    # No published schedule: assume a payment could land at any point, which
    # means a hold of h hours crosses at most floor(h / interval) + 1 of them.
    return int(hours / interval) + 1, False


def project_to_horizon(
    *,
    entry: Decimal,
    stop: Decimal,
    quantity: Decimal,
    side: PositionSide,
    margin: Decimal,
    contract: ContractSpec | None,
    funding: FundingRate | None,
    horizon_hours: float,
    atr: Decimal | None = None,
    margin_mode: MarginMode = MarginMode.ISOLATED,
    reference: datetime | None = None,
) -> HorizonProjection:
    """Re-price the liquidation buffer at the end of the hold (SPEC 32)."""
    entry_liq = liquidation_price(
        entry=entry,
        quantity=quantity,
        side=side,
        margin=margin,
        contract=contract,
        margin_mode=margin_mode,
    )
    entry_buf = buffer_score(entry=entry, stop=stop, liquidation=entry_liq, atr=atr)

    def unknown(
        findings: list[str],
        *,
        refused: bool = False,
        settlements: int = 0,
        exact: bool = False,
    ) -> HorizonProjection:
        # `settlements` is carried rather than hardcoded to 0. A refused
        # year-long projection reported `settlements=0` in its own field while
        # its finding said "crosses 1095 settlements" - the field and the
        # sentence describing the field contradicting each other in one object.
        return HorizonProjection(
            horizon_hours=horizon_hours,
            settlements=settlements,
            settlements_exact=exact,
            funding_cost=None,
            entry_margin=margin,
            projected_margin=None,
            entry_liquidation=entry_liq,
            projected_liquidation=None,
            entry_buffer=entry_buf,
            projected_buffer=entry_buf,
            decay_known=False,
            refused=refused,
            findings=tuple(findings),
        )

    if funding is None:
        return unknown(
            [
                "tidak ada observasi funding, jadi penyusutan margin sepanjang "
                "horizon ini tidak diketahui - dan itu tidak sama dengan nol, "
                "dan tidak ada di sini yang boleh memperlakukannya sebagai nol "
                "(FUTURES SPEC 52)"
            ]
        )

    settlements, exact = settlements_in(funding, horizon_hours, reference=reference)

    if settlements > MAX_PROJECTED_SETTLEMENTS:
        return unknown(
            [
                f"hold {horizon_hours:g} jam melewati {settlements} settlement, "
                f"lebih dari {MAX_PROJECTED_SETTLEMENTS} yang akan diproyeksikan "
                "di sini. Hanya rate berikutnya yang merupakan perkiraan "
                "exchange; sisanya mengasumsikan rate hari ini bertahan, dan "
                "mengasumsikannya sebanyak itu bukan sebuah estimasi"
            ],
            refused=True,
            settlements=settlements,
            exact=exact,
        )

    notional = entry * quantity
    # A long pays when the rate is positive; a short receives it. Carrying the
    # sign rather than taking the absolute value is the difference between a
    # projection and a pessimism.
    direction = Decimal(1) if side is PositionSide.LONG else Decimal(-1)
    cost = funding.rate * notional * Decimal(settlements) * direction
    projected_margin = margin - cost

    findings: list[str] = []

    if settlements == 0:
        findings.append(
            "hold ini tidak melewati satu pun funding settlement, jadi funding "
            "tidak membebaninya sama sekali - settlement berikutnya jatuh "
            f"setelah horizon {horizon_hours:g} jam"
        )
    elif not exact:
        findings.append(
            f"{settlements} settlement diasumsikan dari interval "
            f"{funding.interval_hours} jam lalu dibulatkan ke atas: exchange "
            "tidak mempublikasikan waktu settlement berikutnya, jadi hitungan "
            "ini adalah batas atas, bukan hasil pengukuran"
        )

    if settlements > 1:
        findings.append(
            f"dari {settlements} settlement ini hanya yang berikutnya yang "
            "merupakan perkiraan exchange; sisanya mengasumsikan rate saat ini "
            "bertahan, dan itu tidak akan terjadi"
        )

    if projected_margin <= 0:
        findings.append(
            f"funding saja menghabiskan seluruh margin {margin} dalam "
            f"{horizon_hours:g} jam - posisi ditutup oleh exchange sebelum "
            "tesisnya sempat terbukti benar atau salah"
        )
        return HorizonProjection(
            horizon_hours=horizon_hours,
            settlements=settlements,
            settlements_exact=exact,
            funding_cost=cost,
            entry_margin=margin,
            projected_margin=projected_margin,
            entry_liquidation=entry_liq,
            projected_liquidation=None,
            entry_buffer=entry_buf,
            projected_buffer=buffer_score(
                entry=entry, stop=stop, liquidation=None, atr=atr
            ),
            decay_known=True,
            findings=tuple(findings),
        )

    projected_liq = liquidation_price(
        entry=entry,
        quantity=quantity,
        side=side,
        margin=projected_margin,
        contract=contract,
        margin_mode=margin_mode,
    )
    projected_buf = buffer_score(
        entry=entry, stop=stop, liquidation=projected_liq, atr=atr
    )

    projection = HorizonProjection(
        horizon_hours=horizon_hours,
        settlements=settlements,
        settlements_exact=exact,
        funding_cost=cost,
        entry_margin=margin,
        projected_margin=projected_margin,
        entry_liquidation=entry_liq,
        projected_liquidation=projected_liq,
        entry_buffer=entry_buf,
        projected_buffer=projected_buf,
        decay_known=True,
        findings=(),
    )

    erosion = projection.margin_erosion_pct
    if erosion is not None and erosion > 0:
        tick = contract.tick_size if contract else None
        findings.append(
            f"funding menghabiskan {erosion}% dari margin yang disetor dalam "
            f"{horizon_hours:g} jam, menggeser liquidation dari "
            f"{_at_tick(entry_liq.price if entry_liq else None, tick)} ke "
            f"{_at_tick(projected_liq.price if projected_liq else None, tick)}"
        )
    elif erosion is not None and erosion < 0:
        findings.append(
            f"sisi ini menerima funding pada rate saat ini, menambah "
            f"{-erosion}% ke margin yang disetor selama hold dan menjauhkan "
            "liquidation - kalau rate-nya bertahan, dan itu tidak dijanjikan"
        )

    if projected_buf.band != entry_buf.band:
        findings.append(
            f"buffer turun dari {entry_buf.band} ke {projected_buf.band} "
            "sepanjang horizon ini: tingkat leverage yang masih aman saat entry "
            "bukan tingkat leverage yang masih aman di akhir hold"
        )

    findings.append(
        "gap dan wick pada hold yang lebih panjang tidak dimodelkan di sini - "
        "stop tidak tereksekusi menembus wick yang brutal, sementara liquidation "
        "tetap terjadi, dan ARUNA tidak punya pengukuran atas eksposur itu"
    )

    return HorizonProjection(
        horizon_hours=horizon_hours,
        settlements=settlements,
        settlements_exact=exact,
        funding_cost=cost,
        entry_margin=margin,
        projected_margin=projected_margin,
        entry_liquidation=entry_liq,
        projected_liquidation=projected_liq,
        entry_buffer=entry_buf,
        projected_buffer=projected_buf,
        decay_known=True,
        findings=tuple(findings),
    )


__all__ = [
    "MAX_PROJECTED_SETTLEMENTS",
    "HorizonProjection",
    "project_to_horizon",
    "settlements_in",
]
