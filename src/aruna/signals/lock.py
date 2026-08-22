"""The prediction lock (SPEC 20).

Turns a council verdict into an immutable prediction. The moment of locking is
the moment ARUNA becomes accountable: everything before it is analysis,
everything after is a claim that can be scored.

Three things this module refuses to do:

* **Lock a non-directional verdict as a trade.** A WAIT is recorded (SPEC 28
  ghost signals need it) but carries no entry or target, because there is no
  position to be right or wrong about.
* **Derive a target it cannot justify.** The target comes from measured ATR.
  When ATR is unavailable the signal locks with no target and says so, rather
  than inventing a round number that would later be scored as if it meant
  something.
* **Publish a prediction that cannot pay for its own execution.** If the target
  move is smaller than the round-trip cost, the position loses money even when
  the direction is right and the target is hit exactly. PHASE 9 measured that
  happening across an entire horizon: 0.398% targets against 0.70% costs, 218
  predictions, a guaranteed loss. See :func:`covers_costs`.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from aruna.agents.context import DecisionContext
from aruna.core.clock import isoformat, now_utc
from aruna.core.enums import Decision, Horizon
from aruna.core.errors import ArunaError
from aruna.core.logging import get_logger
from aruna.council.session import CouncilVerdict
from aruna.signals.models import LockedSignal, SignalStatus
from aruna.signals.paper import cost_model

log = get_logger("aruna.signals.lock")

#: Skala kolom harga di basis data: ``DECIMAL(30,12)``.
#:
#: Sama untuk ``target_price``, ``entry_price``, dan ``reference_price``. Yang
#: bermasalah cuma target, karena ia satu-satunya yang **dihitung** - dua yang
#: lain datang apa adanya dari venue.
_HARGA = Decimal("0.000000000001")

#: Target distance as a multiple of ATR. A modelled assumption, not a
#: calibrated figure - PHASE 9 backtesting is what would validate it.
TARGET_ATR_MULTIPLE = 1.5

#: Below this the council has no edge worth recording as a prediction.
MIN_LOCK_CONFIDENCE = 0.35

#: Evidence older than this multiple of the horizon disqualifies a signal from
#: being published. A "1h call" whose newest settled bar closed six hours ago is
#: not a one-hour forecast: the market has already moved through most of the
#: window the prediction claims to cover. The verdict is still recorded - it is
#: what the council concluded - but it is not presented as a live signal.
MAX_EVIDENCE_AGE_MULTIPLE = 1.0

#: Kelonggaran tambahan di atas satu horizon penuh, dalam detik.
#:
#: **Tanpa ini, hampir setiap signal 15m dan 1h ditahan karena aritmetika,
#: bukan karena datanya basi.** Terukur di log produksi: 271 penahanan berbunyi
#: persis "evidence is 15 minute(s) old against a 15m horizon" dan 76 berbunyi
#: "60 minute(s) old against a 1h horizon" - satu angka yang sama berulang
#: ratusan kali, bukan sebaran. Itu tanda batas yang dilewati tipis, bukan data
#: yang benar-benar tua.
#:
#: Sebabnya struktural. Bar tertutup terbaru berumur antara nol dan satu
#: interval penuh pada saat mana pun, dan ingest sengaja menunggu
#: ``ARUNA_UPKEEP_CANDLE_SETTLE_SEC`` (15 detik) sesudah batas bar sebelum
#: menariknya - supaya bar yang belum final tidak tersimpan. Tick yang mengunci
#: prediksi bisa jatuh di dalam jeda itu, dan menemukan bar sebelumnya: umur
#: satu interval **plus beberapa detik**. Batas tepat satu interval menolaknya.
#:
#: Enam puluh detik: menutupi jeda settle beserta waktu tarik dan tulisnya,
#: dengan margin. Jauh di bawah horizon terpendek yang dipakai (15 menit), jadi
#: bukti yang benar-benar basi - satu horizon penuh terlambat - tetap ditolak.
EVIDENCE_SETTLE_GRACE_SEC = 60.0


class ImmutabilityError(ArunaError):
    """An attempt to change a locked prediction (SPEC 20)."""


class LeakageError(ArunaError):
    """Evidence dated after the moment of the prediction (SPEC 24)."""


def _kalibrasi(kalibrator: Any, mentah: float) -> Any:
    """Petakan keyakinan, dan jangan pernah menjatuhkan penguncian karenanya.

    Kalibrasi adalah lapisan penyempurna. Prediksi yang membawa arah, entry,
    dan stop tidak boleh gagal dikunci karena peta keyakinannya bermasalah -
    yang hilang kalau ia gagal cuma penyesuaian angkanya.
    """
    from aruna.learning.kalibrator import Terkalibrasi

    if kalibrator is None:
        return Terkalibrasi(
            mentah=mentah, nilai=mentah, disesuaikan=False,
            alasan="kalibrator tidak dipasang",
        )
    try:
        return kalibrator.kalibrasi(mentah)
    except Exception:
        log.exception("signals.kalibrasi_failed")
        return Terkalibrasi(
            mentah=mentah, nilai=mentah, disesuaikan=False,
            alasan="kalibrasi gagal",
        )


def build_signal(
    verdict: CouncilVerdict,
    context: DecisionContext,
    *,
    model_version: str,
    council_session_id: int | None = None,
    supersedes: str | None = None,
    locked_at: datetime | None = None,
    kalibrator: Any = None,
) -> LockedSignal:
    """Freeze a council verdict into a prediction.

    Records WAIT and NO_SIGNAL verdicts too. SPEC 28 needs the WAITs to judge
    whether standing aside was right, and a system that only records the calls
    it acted on flatters itself.
    """
    moment = locked_at or now_utc()
    reference = context.state.last_price
    horizon = context.interval

    if context.as_of > moment:
        # SPEC 24. The database CHECK would refuse this too, but by then the
        # message is a constraint name. A prediction built on evidence from
        # after the prediction is not a forecast, and the caller should hear
        # exactly that, at the point it happened.
        raise LeakageError(
            f"cannot lock {context.symbol} {horizon.value}: evidence is dated "
            f"{isoformat(context.as_of)}, after the lock at {isoformat(moment)}. "
            "A prediction cannot be based on data from after it was made."
        )

    entry = reference
    target: Decimal | None = None
    expected_move: float | None = None

    if verdict.decision.is_directional:
        target, expected_move = _project_target(
            context, reference, verdict.decision
        )

    # Kalibrasi keyakinan (bagian 9). `None` tidak menyesuaikan apa pun:
    # pemanggil yang belum punya laporan bukan pemanggil yang sudah mengukur.
    terkalibrasi = _kalibrasi(kalibrator, verdict.confidence)

    reasoning = _reasoning(verdict)
    if terkalibrasi.disesuaikan:
        # Bagian dari catatan beku, bukan penanda runtime: siapa pun yang
        # membaca prediksi ini nanti harus melihat bahwa angkanya sudah
        # dipetakan, dan atas dasar apa.
        reasoning = (
            *reasoning,
            f"keyakinan dikalibrasi {terkalibrasi.mentah:.0%} -> "
            f"{terkalibrasi.nilai:.0%} ({terkalibrasi.alasan})",
        )

    stale = evidence_age_note(context.as_of, horizon, moment)
    if stale:
        # Part of the frozen record, not a runtime flag: whoever reads this
        # prediction later must see the caveat it was made under.
        reasoning = (*reasoning, stale)

    return LockedSignal(
        signal_id=uuid.uuid4().hex[:16],
        market=context.market,
        symbol=context.symbol,
        horizon=horizon,
        direction=verdict.decision,
        # Bagian 9: yang dinyatakan adalah keyakinan yang terbukti, bukan yang
        # diklaim. Yang mentah tetap dibawa - pengukuran kalibrasi berikutnya
        # memakai yang mentah, kalau tidak ia mengukur dirinya sendiri.
        confidence=terkalibrasi.nilai,
        confidence_raw=verdict.confidence,
        reference_price=reference,
        entry_price=entry,
        target_price=target,
        expected_move_pct=expected_move,
        locked_at=moment,
        as_of=context.as_of,
        resolves_at=moment + horizon.duration,
        bid=context.state.bid,
        ask=context.state.ask,
        spread_bps=context.state.spread_bps,
        reasoning=reasoning,
        regime=(
            context.regime.regime.value if context.regime else None
        ),
        news_state=_news_state(context),
        risk_level=verdict.risk.overall.value,
        data_source=context.state.source,
        data_timestamp=context.as_of,
        model_version=model_version,
        council_session_id=council_session_id,
        status=SignalStatus.LOCKED,
        supersedes=supersedes,
    )


def _project_target(
    context: DecisionContext, reference: Decimal, direction: Decision
) -> tuple[Decimal | None, float | None]:
    """Target and expected move from measured volatility.

    Uses ATR because it is the one volatility figure ARUNA actually measures.
    Returns ``(None, None)`` when ATR is unavailable - a target invented from
    nothing would be scored later as though it had been a real forecast.
    """
    atr = context.reading("atr")
    if atr is None or not atr.reliable or not atr.value or reference <= 0:
        return None, None

    distance = Decimal(str(atr.value)) * Decimal(str(TARGET_ATR_MULTIPLE))
    if distance <= 0:
        return None, None

    # **Dibulatkan ke skala kolomnya, MENJAUH dari harga acuan.**
    #
    # ``atr.value`` adalah float, dan ``Decimal(str(...))`` dikali pengalinya
    # menghasilkan lebih dari dua belas angka di belakang koma. Kolomnya
    # ``DECIMAL(30,12)``, jadi MySQL memotongnya sambil memperingatkan - 769
    # baris "Data truncated for column 'target_price'" terhitung di log
    # produksi, lebih banyak daripada kolom mana pun.
    #
    # Arah pembulatannya bukan setengah-genap: ke atas untuk BUY, ke bawah
    # untuk SELL - selalu menjauh dari acuan. Alasannya sama dengan pembulatan
    # kuantitas di :mod:`aruna.signals.paper`: kalau harus meleset, meleset ke
    # arah yang membuat prediksinya **lebih sulit** dipenuhi, bukan lebih
    # mudah. Selisihnya sepersetriliun dan tidak akan pernah mengubah satu
    # hasil pun; yang dijaga adalah tidak pernah ada jalur di sistem ini yang
    # diam-diam memudahkan targetnya sendiri.
    naik = direction is Decision.BUY
    target = (reference + distance) if naik else (reference - distance)
    target = target.quantize(
        _HARGA, rounding=ROUND_CEILING if naik else ROUND_FLOOR
    )
    if target <= 0:
        return None, None

    move = float((target - reference) / reference * Decimal(100))
    return target, round(move, 6)


def _reasoning(verdict: CouncilVerdict) -> tuple[str, ...]:
    """The argument behind the call, captured at lock time (SPEC 20, 39)."""
    lines: list[str] = [
        f"council {verdict.decision.value} at {verdict.confidence * 100:.0f}%",
        *verdict.judgement.reasoning[:4],
    ]
    if verdict.judgement.minority_prevailed:
        lines.append("minority prevailed on evidence weight")
    for opinion in verdict.opinions:
        if not opinion.abstained and opinion.decision.is_directional:
            lines.append(
                f"{opinion.role.value} {opinion.decision.value} "
                f"{opinion.confidence * 100:.0f}%: {opinion.reasoning[0]}"
            )
    if verdict.veto.vetoes:
        lines.append(f"veto: {verdict.veto.summary()}")
    lines.extend(verdict.notes)
    return tuple(lines[:24])


def _news_state(context: DecisionContext) -> str | None:
    recent = context.recent_news(hours=24)
    if not recent:
        return "NO_RECENT_NEWS"
    from aruna.news.models import Sentiment

    positive = sum(1 for i in recent if i.sentiment is Sentiment.POSITIVE)
    negative = sum(1 for i in recent if i.sentiment is Sentiment.NEGATIVE)
    unknown = sum(1 for i in recent if i.sentiment is Sentiment.UNKNOWN)
    return (
        f"{len(recent)} item(s): {positive}+ / {negative}- / {unknown} unreadable"
    )


def evidence_age_note(
    as_of: datetime, horizon: Horizon, locked_at: datetime
) -> str | None:
    """Say so when the evidence is too old for the horizon it predicts over.

    Returns ``None`` when the evidence is fresh enough, otherwise a sentence
    stating the actual age. SPEC 24 already forbids evidence from *after* the
    lock; this is the opposite failure, evidence from so far before it that the
    horizon has effectively already run.
    """
    age_sec = (locked_at - as_of).total_seconds()
    limit_sec = (
        horizon.duration.total_seconds() * MAX_EVIDENCE_AGE_MULTIPLE
        + EVIDENCE_SETTLE_GRACE_SEC
    )
    if age_sec <= limit_sec:
        return None
    return (
        f"evidence is {age_sec / 60:.0f} minute(s) old against a "
        f"{horizon.value} horizon - stale, not published as a live signal"
    )


def round_trip_cost_pct(signal: LockedSignal) -> float:
    """What it costs to open and close this position, as a percent of price.

    Mirrors what :mod:`aruna.signals.paper` actually charges: both fees, the
    modelled slippage, and the quoted spread when the venue published one. The
    spread is counted once - the entry fills at the touch and the exit crosses
    back - which is the same treatment ``open_trade`` and ``close_trade`` apply.

    An approximation, and deliberately the *optimistic* one: it uses the spread
    quoted at lock time, and a real fill can be worse.
    """
    model = cost_model(signal.market)
    cost = float(model.taker_fee_pct + model.sell_fee_pct)
    cost += float(model.slippage_bps) / 100

    if (
        model.charge_spread
        and signal.bid
        and signal.ask
        and signal.ask > signal.bid
        and signal.reference_price > 0
    ):
        cost += float((signal.ask - signal.bid) / signal.reference_price * 100)
    return round(cost, 6)


def covers_costs(signal: LockedSignal) -> tuple[bool, str]:
    """Whether the predicted move could pay for the round trip.

    This is the arithmetic that made PHASE 9's 1h backtest a guaranteed loss:
    the average target was 0.398% against a 0.70% round trip, so **every** 1h
    prediction lost money even when its direction was right and its target was
    hit exactly. Publishing a signal like that is not a bad forecast, it is a
    misleading one.

    A necessary condition, not a sufficient one. Clearing costs does not make a
    signal profitable - the 1d horizon clears them and still lost, because
    targets are rarely reached. Requiring *headroom* above the costs would be a
    strategy choice, and belongs in a proposal rather than here.
    """
    cost = round_trip_cost_pct(signal)

    if signal.expected_move_pct is None:
        return False, (
            f"no target, so the {cost:.2f}% round-trip cost cannot be shown to "
            "be covered - ATR was unavailable, and a prediction that might not "
            "pay for its own execution is not published as tradeable"
        )

    move = abs(signal.expected_move_pct)
    if move <= cost:
        return False, (
            f"target moves {move:.2f}% against a {cost:.2f}% round-trip cost: "
            "this loses money even if the direction is right and the target is "
            "hit exactly"
        )
    return True, f"target {move:.2f}% clears the {cost:.2f}% round-trip cost"


def should_lock(signal: LockedSignal) -> tuple[bool, str]:
    """Whether this signal is fit to be published as a tradeable prediction.

    Takes the built signal rather than the verdict, because three of the four
    reasons to withhold one - the confidence floor, the age of the evidence, and
    whether the move can pay for itself - are only visible once the prices and
    timestamps are attached.

    A ``False`` here does not discard anything. The signal is still stored:
    SPEC 28 needs the calls ARUNA declined to make, and a record of only the
    published ones would flatter the system.
    """
    if not signal.is_directional:
        return False, f"verdict is {signal.direction.value}, not a position"
    if signal.confidence < MIN_LOCK_CONFIDENCE:
        return (
            False,
            f"confidence {signal.confidence:.2f} below the "
            f"{MIN_LOCK_CONFIDENCE:.2f} lock floor",
        )
    stale = evidence_age_note(signal.as_of, signal.horizon, signal.locked_at)
    if stale:
        return False, stale
    viable, note = covers_costs(signal)
    if not viable:
        return False, note
    return True, f"directional, confident, evidence fresh, and {note}"


def verify_integrity(signal: LockedSignal, stored_fingerprint: str) -> None:
    """Confirm a stored prediction still matches what was locked (SPEC 20).

    Called before scoring. If a record has been altered, the outcome computed
    from it is meaningless, so this raises rather than returning a flag that a
    caller might ignore.
    """
    if signal.fingerprint != stored_fingerprint:
        raise ImmutabilityError(
            f"signal {signal.signal_id} has been altered since it was locked: "
            f"fingerprint {signal.fingerprint[:12]} does not match the stored "
            f"{stored_fingerprint[:12]}. Its outcome cannot be scored."
        )


def supersede(
    original: LockedSignal,
    verdict: CouncilVerdict,
    context: DecisionContext,
    *,
    model_version: str,
    council_session_id: int | None = None,
) -> tuple[LockedSignal, LockedSignal]:
    """Issue a revised prediction (SPEC 20).

    Returns ``(superseded_original, new_signal)``. The original is *not* edited
    - only its status changes, and its locked fields keep their original values
    and fingerprint. A changed mind creates a new record; it never rewrites the
    old one.
    """
    if original.status is not SignalStatus.LOCKED:
        raise ImmutabilityError(
            f"signal {original.signal_id} is {original.status.value} and cannot "
            "be superseded again"
        )

    replacement = build_signal(
        verdict,
        context,
        model_version=model_version,
        council_session_id=council_session_id,
        supersedes=original.signal_id,
    )
    retired = replace(original, status=SignalStatus.SUPERSEDED)

    log.info(
        "signal.superseded",
        original=original.signal_id,
        replacement=replacement.signal_id,
        old_direction=original.direction.value,
        new_direction=replacement.direction.value,
    )
    return retired, replacement


__all__ = [
    "EVIDENCE_SETTLE_GRACE_SEC",
    "MAX_EVIDENCE_AGE_MULTIPLE",
    "MIN_LOCK_CONFIDENCE",
    "TARGET_ATR_MULTIPLE",
    "ImmutabilityError",
    "LeakageError",
    "build_signal",
    "covers_costs",
    "evidence_age_note",
    "round_trip_cost_pct",
    "should_lock",
    "supersede",
    "verify_integrity",
]
