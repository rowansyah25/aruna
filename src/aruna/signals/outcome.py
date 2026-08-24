"""Outcome engine (SPEC 22, 23).

Scores a locked prediction against what actually happened. Two rules govern
everything here:

* **The original prediction is never edited (SPEC 22).** The outcome is a new
  record that references the signal. Adjusting a past forecast to match the
  result is the single most effective way to make a system unevaluable.
* **Sample during the horizon, not only at its end (SPEC 23).** A call that was
  wrong from the first minute and one that was right for an hour before
  reversing produce the same closing price and demand completely different
  lessons. Only intermediate samples can tell them apart.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from aruna.core.clock import now_utc
from aruna.core.enums import Decision, Horizon
from aruna.core.logging import get_logger
from aruna.signals.models import (
    LockedSignal,
    OutcomeClass,
    OutcomeSample,
    SignalOutcome,
)

log = get_logger("aruna.signals.outcome")

#: SPEC 23 sampling offsets, as fractions of the horizon. Fractions rather than
#: absolute times so the shape of the sampling is the same for a 1m and a 1d
#: prediction.
SAMPLE_FRACTIONS: tuple[float, ...] = (0.1, 0.25, 0.5, 0.75, 1.0)

#: A move smaller than this is not a direction, it is noise. Without it a
#: +0.001% drift would be scored as a correct BUY, and a track record built from
#: those is measuring rounding, not skill.
FLAT_THRESHOLD_PCT = 0.05


#: Intervals ARUNA ingests, finest first. Sampling has to come from one of
#: these; nothing else exists in storage.
#:
#: Public because the refresher derives what it keeps current from this tuple
#: rather than restating it. Widen this and the refresh set widens with it; a
#: hand-written second list would be free to fall out of step, and the symptom
#: would be predictions that cannot be scored rather than an error anyone sees.
STORED_INTERVALS: tuple[Horizon, ...] = (
    Horizon.M1,
    Horizon.M15,
    Horizon.H1,
    Horizon.D1,
)

#: How many observations inside the horizon a sampling interval must be able to
#: yield. Below this the SPEC 23 classes collapse: with one observation there is
#: no path, only an endpoint.
MIN_OBSERVATIONS = 4


def sampling_intervals(horizon: Horizon) -> tuple[Horizon, ...]:
    """Candidate intervals for observing a prediction, best first (SPEC 23).

    Reading a 1d prediction from 1d candles yields exactly one observation - the
    close - and every SPEC 23 class that depends on the *path* becomes
    unreachable. The interval used to sample must be finer than the horizon
    being scored, so this returns the candidates that can produce at least
    :data:`MIN_OBSERVATIONS`, coarsest-of-the-fine-enough first so the query
    reads as few rows as it can get away with.

    The horizon's own interval is always last: it is a poor sample, but it is
    better than refusing to score a prediction at all, and the caller reports
    when it had to fall back.
    """
    span = horizon.duration.total_seconds()
    fine_enough = [
        interval
        for interval in STORED_INTERVALS
        if 0 < interval.duration.total_seconds() * MIN_OBSERVATIONS <= span
    ]
    fine_enough.sort(key=lambda i: i.duration, reverse=True)
    if horizon not in fine_enough:
        fine_enough.append(horizon)
    return tuple(fine_enough)


def sample_offsets(horizon: Horizon) -> list[tuple[str, timedelta]]:
    """When to observe a prediction during its horizon (SPEC 23)."""
    span = horizon.duration
    return [
        (
            f"T+{int(fraction * 100)}%",
            timedelta(seconds=span.total_seconds() * fraction),
        )
        for fraction in SAMPLE_FRACTIONS
    ]


def build_samples(
    signal: LockedSignal, prices: list[tuple[datetime, Decimal]]
) -> list[OutcomeSample]:
    """Observations at the SPEC 23 offsets, measured against the locked price.

    ``prices`` are candidate observations; the one nearest each offset is kept.
    Anything outside ``[locked_at, resolves_at]`` is dropped rather than
    silently used - a price from after the horizon would make the outcome look
    better or worse than the prediction earned (SPEC 24).

    Sampling at fixed offsets rather than keeping every observation is what
    makes the SPEC 23 classes possible: the shape of the path matters, and a
    single closing price cannot distinguish a call that was wrong from the first
    minute from one that was right for most of the horizon and then reversed.
    """
    reference = signal.reference_price
    if reference <= 0:
        return []

    usable = [
        (moment, price)
        for moment, price in sorted(prices)
        if signal.locked_at <= moment <= signal.resolves_at and price > 0
    ]
    if not usable:
        return []

    chosen: dict[datetime, str] = {}
    for label, offset in sample_offsets(signal.horizon):
        target = signal.locked_at + offset
        moment, _ = min(usable, key=lambda p: abs((p[0] - target).total_seconds()))
        # An observation can be nearest to two offsets when the data is coarser
        # than the sampling scheme. First label wins; the duplicate is dropped
        # rather than written twice under different names.
        chosen.setdefault(moment, label)

    # The last observation is always kept: it is what the prediction closed at.
    final_moment = usable[-1][0]
    chosen.setdefault(final_moment, _label(signal, final_moment))

    prices_by_moment = dict(usable)
    samples = [
        _sample(signal, moment, prices_by_moment[moment], label, moment == final_moment)
        for moment, label in sorted(chosen.items())
    ]
    return samples


def _sample(
    signal: LockedSignal,
    moment: datetime,
    price: Decimal,
    label: str,
    is_final: bool,
) -> OutcomeSample:
    move = float((price - signal.reference_price) / signal.reference_price * 100)
    return OutcomeSample(
        signal_id=signal.signal_id,
        offset_label=label,
        sampled_at=moment,
        price=price,
        move_pct=round(move, 6),
        favourable=_is_favourable(signal.direction, move),
        is_final=is_final,
    )


def _label(signal: LockedSignal, moment: datetime) -> str:
    span = (signal.resolves_at - signal.locked_at).total_seconds()
    if span <= 0:
        return "T+0"
    elapsed = (moment - signal.locked_at).total_seconds()
    return f"T+{int(elapsed / span * 100)}%"


def _is_favourable(direction: Decision, move_pct: float) -> bool:
    """Whether the move went the predicted way, beyond the noise band.

    A move inside ``FLAT_THRESHOLD_PCT`` is favourable to nobody: the market did
    not go anywhere, and counting it as a win for whichever side happened to be
    on the right side of the last decimal place would be scoring noise.
    """
    if abs(move_pct) < FLAT_THRESHOLD_PCT:
        return False
    if direction is Decision.BUY:
        return move_pct > 0
    if direction is Decision.SELL:
        return move_pct < 0
    return False


def resolve(
    signal: LockedSignal,
    samples: list[OutcomeSample],
    *,
    resolved_at: datetime | None = None,
) -> SignalOutcome:
    """Score a locked prediction (SPEC 22).

    Produces a new record. The signal itself is read, never written.
    """
    if not samples:
        raise ValueError(
            f"cannot resolve {signal.signal_id}: no price observations in its window"
        )

    ordered = sorted(samples, key=lambda s: s.sampled_at)
    final = ordered[-1]
    moment = resolved_at or now_utc()

    moves = [s.move_pct for s in ordered]
    max_up = max(moves)
    max_down = min(moves)

    if signal.direction is Decision.BUY:
        favourable, adverse = max_up, min(max_down, 0.0)
    elif signal.direction is Decision.SELL:
        favourable, adverse = -max_down, min(-max_up, 0.0)
    else:
        # No position, so neither figure describes a P&L excursion. They record
        # what the market actually did instead - SPEC 28 needs exactly that to
        # judge later whether standing aside cost anything. Zeroing them would
        # throw away the only evidence a ghost-signal analysis could use.
        favourable, adverse = max_up, min(max_down, 0.0)

    direction_correct = _is_favourable(signal.direction, final.move_pct)
    target_reached = _target_reached(signal, ordered)
    outcome_class = _classify(signal, ordered, direction_correct, target_reached)

    error = None
    if signal.expected_move_pct is not None:
        # Signed: positive means the market moved further than predicted.
        error = round(final.move_pct - signal.expected_move_pct, 6)

    outcome = SignalOutcome(
        signal_id=signal.signal_id,
        original_direction=signal.direction,
        reference_price=signal.reference_price,
        predicted_move_pct=signal.expected_move_pct,
        actual_move_pct=final.move_pct,
        final_price=final.price,
        resolved_at=moment,
        direction_correct=direction_correct,
        outcome_class=outcome_class,
        prediction_error=error,
        max_adverse_pct=round(adverse, 6),
        max_favourable_pct=round(favourable, 6),
        target_reached=target_reached,
        samples=tuple(ordered),
    )
    log.info(
        "signal.resolved",
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        direction=signal.direction.value,
        predicted=signal.expected_move_pct,
        actual=final.move_pct,
        correct=direction_correct,
        outcome=outcome_class.value,
    )
    return outcome


def _target_reached(signal: LockedSignal, samples: list[OutcomeSample]) -> bool:
    """True if price touched the target at any point during the horizon.

    Checked across every sample rather than at the close: a prediction whose
    target was hit and then given back did reach it, and pretending otherwise
    would misattribute a timing failure as a directional one.
    """
    if signal.target_price is None or not signal.is_directional:
        return False
    if signal.direction is Decision.BUY:
        return any(s.price >= signal.target_price for s in samples)
    return any(s.price <= signal.target_price for s in samples)


def stop_price(signal: LockedSignal) -> Decimal | None:
    """The stop a symmetric exit rule would use, derived from the target.

    ARUNA has no stop-loss and stores none, so this is computed rather than
    read: the same distance as the target, on the other side of entry. That
    makes reward and risk 1:1, which is the neutral choice - any other ratio is
    a strategy claim needing its own evidence.
    """
    if signal.target_price is None or not signal.is_directional:
        return None
    distance = abs(signal.target_price - signal.entry_price)
    if distance <= 0:
        return None
    stop = (
        signal.entry_price - distance
        if signal.direction is Decision.BUY
        else signal.entry_price + distance
    )
    return stop if stop > 0 else None


def exit_point(
    signal: LockedSignal,
    samples: list[OutcomeSample],
    *,
    take_profit: bool = True,
    stop_loss: bool = False,
) -> tuple[Decimal, datetime, str]:
    """Where a position would have closed under a given exit rule.

    ARUNA's live rule holds to horizon expiry and closes at whatever price is
    there; that is what both flags off reproduces. The flags select the variants
    PHASE 9's finding put on the table:

    * ``take_profit`` - close when the target is touched. Measured on its own it
      made results *worse*: it caps winners while losers still run to expiry.
    * ``stop_loss`` - close at the mirror of the target. Only meaningful
      alongside ``take_profit``, which is the symmetric rule the first result
      pointed at.

    Returns ``(price, when, reason)``.

    **Three assumptions, stated rather than buried.** Exiting *at* the level
    assumes a resting order there filled; the sampled close went past it, so the
    price traded through, which is reasonable but not free. The check uses the
    SPEC 23 samples the outcome is scored from, not every bar, so a level
    touched and given back between samples is missed. And when a sample sits
    beyond both levels the **stop is taken first**, because intrabar order is
    unknowable and assuming the profitable one is how backtests lie.
    """
    final = samples[-1] if samples else None
    if final is None:
        raise ValueError(f"{signal.signal_id}: no observations to exit on")

    if not signal.is_directional or signal.target_price is None:
        return final.price, final.sampled_at, "held to horizon expiry"

    stop = stop_price(signal) if stop_loss else None
    is_buy = signal.direction is Decision.BUY

    for sample in samples:
        # Stop checked first: see the docstring.
        if stop is not None and (
            sample.price <= stop if is_buy else sample.price >= stop
        ):
            return stop, sample.sampled_at, f"stopped out at {sample.offset_label}"
        if take_profit and (
            sample.price >= signal.target_price
            if is_buy
            else sample.price <= signal.target_price
        ):
            return (
                signal.target_price,
                sample.sampled_at,
                f"target touched at {sample.offset_label} of the horizon",
            )
    return final.price, final.sampled_at, "no level reached; held to expiry"


def _classify(
    signal: LockedSignal,
    samples: list[OutcomeSample],
    direction_correct: bool,
    target_reached: bool,
) -> OutcomeClass:
    """SPEC 23: distinguish *how* the prediction went, not just whether."""
    if not signal.is_directional:
        return OutcomeClass.NO_POSITION

    if target_reached:
        return OutcomeClass.TARGET_REACHED

    if direction_correct:
        # Right at the close but never reached the target: the direction was
        # read correctly and the magnitude or the timing was not.
        return OutcomeClass.TARGET_NOT_REACHED

    early = samples[: max(1, len(samples) // 2)]
    if any(s.favourable for s in early):
        # It worked, then gave it back.
        return OutcomeClass.RIGHT_THEN_REVERSED

    if any(s.favourable for s in samples):
        # It came good only late, then closed wrong - the horizon was fighting
        # the call rather than the direction being mistaken.
        return OutcomeClass.RIGHT_DIRECTION_BAD_TIMING

    return OutcomeClass.WRONG_FROM_START


def summarise(outcomes: list[SignalOutcome]) -> dict[str, object]:
    """Aggregate resolved outcomes for the SPEC 41 report."""
    directional = [o for o in outcomes if o.original_direction.is_directional]
    if not directional:
        return {"resolved": len(outcomes), "note": "no directional outcomes yet"}

    correct = [o for o in directional if o.direction_correct]
    errors = [
        abs(o.prediction_error) for o in directional if o.prediction_error is not None
    ]
    classes: dict[str, int] = {}
    for outcome in outcomes:
        key = outcome.outcome_class.value
        classes[key] = classes.get(key, 0) + 1

    return {
        "resolved": len(outcomes),
        "directional": len(directional),
        "direction_accuracy": round(len(correct) / len(directional), 4),
        "target_hit_rate": round(
            sum(1 for o in directional if o.target_reached) / len(directional), 4
        ),
        "mean_absolute_error_pct": (
            round(sum(errors) / len(errors), 4) if errors else None
        ),
        "outcome_classes": classes,
        "mean_max_adverse_pct": round(
            sum(o.max_adverse_pct for o in directional) / len(directional), 4
        ),
        "mean_max_favourable_pct": round(
            sum(o.max_favourable_pct for o in directional) / len(directional), 4
        ),
    }


