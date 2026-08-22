"""Liquidation price, buffer and cascade (FUTURES SPEC 24, 25, 26).

FUTURES SPEC 24: *jangan menggunakan angka liquidation yang tidak dapat
dipertanggungjawabkan* - do not use a liquidation number you cannot account
for. So this implements Binance's own published formula, with the maintenance
bracket the position actually falls into, and refuses to produce a number when
the inputs for it are missing. A liquidation price that is roughly right is
worse than none: it is the one figure a leveraged trader plans around.

The comparison that matters is the ordering FUTURES SPEC 24 asks for:

    ENTRY  ->  STOP LOSS  ->  LIQUIDATION

If liquidation sits *before* the stop, the stop is decorative. The position is
closed by the exchange at a price the trader never chose, and every risk number
computed from the stop was fiction. :func:`buffer_score` returns 0 for that
case, and the band table calls it REJECT.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.futures.models import ContractSpec, MarginMode, PositionSide

#: Score bands (FUTURES SPEC 25).
BANDS: tuple[tuple[int, str], ...] = (
    (90, "EXCELLENT"),
    (75, "HEALTHY"),
    (60, "CAUTION"),
    (40, "HIGH_RISK"),
    (0, "REJECT"),
)

#: Liquidation distance below this many ATR is reachable by ordinary movement,
#: whatever the stop says.
MIN_ATR_TO_LIQUIDATION = Decimal(3)

#: Liquidation should sit at least this multiple of the stop distance away.
#: At 2.0 the position survives being wrong by twice as much as planned.
HEALTHY_STOP_MULTIPLE = Decimal(3)

#: Share of open interest in forced closes that marks a cascade (FUTURES SPEC 26).
CASCADE_SHARE = Decimal("0.02")


class CascadeRisk(StrEnum):
    NONE = "NONE"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"
    #: No liquidation feed - not the same as no cascade.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Liquidation:
    price: Decimal
    distance: Decimal
    distance_pct: Decimal
    margin_mode: MarginMode
    maintenance_rate: Decimal
    maintenance_amount: Decimal
    findings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "price": str(self.price),
            "distance": str(self.distance),
            "distance_pct": str(self.distance_pct),
            "margin_mode": self.margin_mode.value,
            "maintenance_rate": str(self.maintenance_rate),
            "maintenance_amount": str(self.maintenance_amount),
            "findings": list(self.findings),
            "note": (
                "dihitung dari formula resmi yang diterbitkan venue dan margin "
                "bracket yang ditempati notional ini; bukan perkiraan "
                "(FUTURES SPEC 24)"
            ),
        }


def liquidation_price(
    *,
    entry: Decimal,
    quantity: Decimal,
    side: PositionSide,
    margin: Decimal,
    contract: ContractSpec | None,
    margin_mode: MarginMode = MarginMode.ISOLATED,
) -> Liquidation | None:
    """Where the exchange closes this position (FUTURES SPEC 24).

    Binance USD-M, isolated one-way::

        LP = (WB + cumB - side x Q x EP) / (Q x MMR - side x Q)

    with ``side`` = +1 long, -1 short, ``WB`` the isolated margin, ``cumB`` the
    maintenance amount for the bracket, and ``MMR`` that bracket's maintenance
    rate.

    Returns ``None`` when the contract specification is missing. That is the
    honest answer: without the venue's maintenance schedule any number here is
    invented, and FUTURES SPEC 24 forbids exactly that.
    """
    if contract is None:
        return None
    if entry <= 0 or quantity <= 0 or margin <= 0:
        return None
    if side is PositionSide.FLAT:
        return None

    findings: list[str] = []
    notional = entry * quantity
    rate, amount = contract.bracket_for(notional)
    if not contract.margin_brackets:
        findings.append(
            "venue tidak menerbitkan margin bracket, jadi yang dipakai adalah "
            "maintenance rate dasar. Margin bracket yang sebenarnya hanya "
            "pernah menaikkan rate seiring bertambahnya size, jadi angka ini "
            "MENGECILKAN kebutuhannya dan menempatkan liquidation LEBIH JAUH "
            "dari entry daripada posisi sebenarnya - errornya condong ke "
            "optimisme, dan makin besar posisinya makin besar pula errornya. "
            "Perlakukan harga ini sebagai batas atas ruang yang tersedia, "
            "bukan sebagai hasil pengukuran"
        )

    direction = Decimal(1) if side is PositionSide.LONG else Decimal(-1)
    denominator = quantity * rate - direction * quantity
    if denominator == 0:
        return None

    price = (margin + amount - direction * quantity * entry) / denominator
    if price <= 0:
        # A long funded so heavily it cannot be liquidated by price alone.
        findings.append(
            "harga liquidation yang dihitung berada di nol atau di bawahnya: "
            "pada margin sebesar ini posisi tidak bisa terkena liquidation "
            "hanya karena pergerakan harga"
        )
        price = Decimal(0)

    if margin_mode is MarginMode.CROSS:
        findings.append(
            "margin CROSS: harga ini mengasumsikan seluruh isi wallet menopang "
            "posisi, jadi posisi terbuka lain akan menggesernya. Perlakukan "
            "sebagai kasus terbaik, bukan sebagai levelnya"
        )

    distance = abs(entry - price)
    return Liquidation(
        price=price,
        distance=distance,
        distance_pct=(distance / entry * 100).quantize(Decimal("0.000001")),
        margin_mode=margin_mode,
        maintenance_rate=rate,
        maintenance_amount=amount,
        findings=tuple(findings),
    )


@dataclass(frozen=True, slots=True)
class BufferScore:
    """How much room there is before the exchange takes over (SPEC 25)."""

    score: int
    band: str
    stop_multiple: Decimal | None
    atr_multiple: Decimal | None
    #: True when liquidation sits between entry and the stop.
    liquidation_before_stop: bool = False
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rejected(self) -> bool:
        return self.band == "REJECT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "stop_multiple": (
                str(self.stop_multiple) if self.stop_multiple is not None else None
            ),
            "atr_multiple": (
                str(self.atr_multiple) if self.atr_multiple is not None else None
            ),
            "liquidation_before_stop": self.liquidation_before_stop,
            "rejected": self.rejected,
            "findings": list(self.findings),
            "note": (
                "ini skor, bukan probabilitas profit: yang diukur adalah "
                "seberapa jauh exit paksa dari exchange berada di belakang "
                "exit yang dipilih trader (FUTURES SPEC 25)"
            ),
        }


def buffer_score(
    *,
    entry: Decimal,
    stop: Decimal,
    liquidation: Liquidation | None,
    atr: Decimal | None = None,
) -> BufferScore:
    """Score the room between the stop and liquidation (FUTURES SPEC 25).

    Two independent measures, and the *worse* of them wins:

    * against the stop - liquidation should sit well behind the exit already
      planned;
    * against ATR - a liquidation two ordinary candles away is reachable no
      matter what the stop says.

    Taking the minimum means a position needs both to be comfortable. Averaging
    them would let a huge ATR buffer paper over a stop that sits past the
    liquidation price.
    """
    if liquidation is None:
        return BufferScore(
            score=0,
            band="REJECT",
            stop_multiple=None,
            atr_multiple=None,
            findings=(
                "harga liquidation tidak bisa dihitung, jadi buffer-nya tidak "
                "diketahui. Buffer yang tidak diketahui diperlakukan sebagai "
                "tidak dapat diterima, bukan sebagai dapat diterima "
                "(FUTURES SPEC 52)",
            ),
        )

    findings: list[str] = []
    stop_distance = abs(entry - stop)
    liq_distance = liquidation.distance

    if stop_distance <= 0:
        return BufferScore(
            score=0,
            band="REJECT",
            stop_multiple=None,
            atr_multiple=None,
            findings=(
                # Bukan "tidak ada risiko": frasa itu bertetangga dengan
                # FORBIDDEN_CLAIMS (FUTURES SPEC 51). Yang dimaksud adalah
                # tidak ada jarak risiko untuk menghitung position size.
                "stop berada tepat di harga entry; tidak ada jarak risiko yang "
                "bisa dipakai untuk menghitung position size",
            ),
        )

    stop_multiple = liq_distance / stop_distance
    if stop_multiple <= 1:
        findings.append(
            f"liquidation hanya {stop_multiple:.2f}x jarak stop - exchange "
            "menutup posisi ini sebelum stop tersentuh. Stop-nya cuma hiasan "
            "dan setiap angka yang dihitung darinya adalah fiksi"
        )
        return BufferScore(
            score=0,
            band="REJECT",
            stop_multiple=stop_multiple,
            atr_multiple=(liq_distance / atr) if atr and atr > 0 else None,
            liquidation_before_stop=True,
            findings=tuple(findings),
        )

    # Linear from 1x (0) to HEALTHY_STOP_MULTIPLE (100), then flat.
    stop_score = min(
        Decimal(100),
        (stop_multiple - 1) / (HEALTHY_STOP_MULTIPLE - 1) * 100,
    )

    atr_multiple = None
    atr_score = Decimal(100)
    if atr is not None and atr > 0:
        atr_multiple = liq_distance / atr
        atr_score = min(Decimal(100), atr_multiple / MIN_ATR_TO_LIQUIDATION * 100)
        if atr_multiple < MIN_ATR_TO_LIQUIDATION:
            findings.append(
                f"liquidation cuma berjarak {atr_multiple:.1f} ATR - "
                "pergerakan biasa saja sudah sampai ke sana, berapa pun stop "
                "dipasang"
            )
    else:
        findings.append(
            "ATR tidak diberikan: buffer hanya dinilai terhadap stop, jadi ini "
            "tidak mengatakan apa pun soal apakah volatilitas normal bisa "
            "mencapai harga liquidation"
        )

    score = int(min(stop_score, atr_score))
    band = next(name for floor, name in BANDS if score >= floor)

    if band in ("EXCELLENT", "HEALTHY"):
        findings.append(
            f"liquidation berada {stop_multiple:.1f}x jarak stop di belakang "
            "exit yang direncanakan"
        )
    return BufferScore(
        score=score,
        band=band,
        stop_multiple=stop_multiple,
        atr_multiple=atr_multiple,
        findings=tuple(findings),
    )


@dataclass(frozen=True, slots=True)
class CascadeReport:
    risk: CascadeRisk
    liquidated_notional: Decimal | None = None
    share_of_open_interest: Decimal | None = None
    dominant_side: PositionSide | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def data_available(self) -> bool:
        return self.risk is not CascadeRisk.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk.value,
            "liquidated_notional": (
                str(self.liquidated_notional)
                if self.liquidated_notional is not None
                else None
            ),
            "share_of_open_interest": (
                str(self.share_of_open_interest)
                if self.share_of_open_interest is not None
                else None
            ),
            "dominant_side": (
                self.dominant_side.value if self.dominant_side else None
            ),
            "data_available": self.data_available,
            "findings": list(self.findings),
            "note": (
                "UNKNOWN bukan NONE: tidak adanya feed liquidation berarti "
                "pertanyaan soal cascade belum terjawab, dan pembacaan yang "
                "menyatakan pasar sepi cuma akan jadi karangan "
                "(FUTURES SPEC 26, 52)"
            ),
        }


def detect_cascade(
    liquidations: list[Any],
    *,
    open_interest: Decimal | None = None,
) -> CascadeReport:
    """Are positions being force-closed in size? (FUTURES SPEC 26)

    Returns ``UNKNOWN`` when there is no liquidation feed, which is the current
    state on this venue: Binance withdrew the REST endpoint and the live data
    arrives over the ``forceOrder`` websocket. ``UNKNOWN`` is deliberately not
    ``NONE`` - an empty list from a feed that does not exist looks exactly like
    a calm market, and treating it as one is how a position gets sized into a
    cascade.
    """
    if not liquidations:
        return CascadeReport(
            risk=CascadeRisk.UNKNOWN,
            findings=(
                "tidak ada event liquidation yang diberikan. Di Binance USD-M "
                "ini memang wajar: endpoint REST-nya sudah ditarik dan deteksi "
                "cascade butuh websocket forceOrder. Pertanyaannya belum "
                "terjawab, bukan terjawab 'tidak'",
            ),
        )

    total = sum((event.notional for event in liquidations), Decimal(0))
    longs = sum(
        (e.notional for e in liquidations if e.side is PositionSide.LONG), Decimal(0)
    )
    shorts = total - longs
    dominant = PositionSide.LONG if longs >= shorts else PositionSide.SHORT

    findings = [
        f"{len(liquidations)} penutupan paksa dengan total {total}, mayoritas "
        f"{dominant.value}"
    ]

    if open_interest is None or open_interest <= 0:
        findings.append(
            "tidak ada open interest sebagai pembanding: angka absolutnya tidak "
            "bisa menyatakan apakah ini cascade atau sesi biasa"
        )
        return CascadeReport(
            risk=CascadeRisk.ELEVATED if total > 0 else CascadeRisk.NONE,
            liquidated_notional=total,
            dominant_side=dominant,
            findings=tuple(findings),
        )

    share = total / open_interest
    if share >= CASCADE_SHARE * 2:
        risk = CascadeRisk.HIGH
        findings.append(
            f"penutupan paksa mencapai {share:.1%} dari open interest - "
            "likuiditas di balik pergerakan ini lebih tipis daripada yang "
            "ditunjukkan order book, dan stop yang dipasang di dalamnya bisa "
            "terisi jauh dari levelnya"
        )
    elif share >= CASCADE_SHARE:
        risk = CascadeRisk.ELEVATED
        findings.append(
            f"penutupan paksa mencapai {share:.1%} dari open interest"
        )
    else:
        risk = CascadeRisk.NONE

    return CascadeReport(
        risk=risk,
        liquidated_notional=total,
        share_of_open_interest=share,
        dominant_side=dominant,
        findings=tuple(findings),
    )


__all__ = [
    "BANDS",
    "CASCADE_SHARE",
    "HEALTHY_STOP_MULTIPLE",
    "MIN_ATR_TO_LIQUIDATION",
    "BufferScore",
    "CascadeReport",
    "CascadeRisk",
    "Liquidation",
    "buffer_score",
    "detect_cascade",
    "liquidation_price",
]
