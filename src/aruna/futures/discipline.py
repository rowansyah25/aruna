"""Behavioural discipline (FUTURES SPEC 34).

Every gate F1-F5 built judges **one setup in isolation**: is this stop sensible,
is this leverage safe, does this reward cover its costs. Each answers honestly
and each is blind to the same thing - the sequence the setup arrives in.

Three losses in a row and then double the size. Raise the leverage after a
losing trade to win it back. Take something, anything, because nothing has
cleared today. Every one of those passes the earlier gates, because every one of
them is individually reasonable. They are only visible from above.

So this module reads the record rather than the setup, and the record is already
there: ``futures_plans`` stores every plan **and every refusal** with a
timestamp, and ``futures_plan_results`` stores how each one ended.

**It advises; it does not veto.** FUTURES SPEC 50 keeps ARUNA an analyst, and a
system that silently suppressed a valid plan because of yesterday's losses would
be making a position-management decision on the reader's behalf. What it does
instead is say plainly what pattern it sees, in the plan itself, where the
reader cannot miss it.

**It never suggests trading more.** There is deliberately no counterpart to
these warnings - no "you are due", no "conditions favour size". A discipline
engine that can talk you into a position is not a discipline engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from aruna.core.clock import isoformat

#: Consecutive losses after which sizing up is revenge, not conviction. Set at
#: three because two is an ordinary run for any strategy with a win rate near a
#: half - and this codebase measured exactly 50% on its spot rules, so two in a
#: row is the single most common outcome there is.
REVENGE_LOSS_STREAK = 3

#: How far back a streak is read. Losses a fortnight apart are not a streak,
#: they are a sample.
STREAK_WINDOW_HOURS = 72

#: Risk growth after a loss that reads as chasing. A tenth is noise from
#: rounding to the venue's step size; half again is a decision.
SIZE_ESCALATION_RATIO = Decimal("1.25")

#: Plans in a day beyond which the operator is hunting rather than waiting.
#: Derived from the horizon, not chosen: at 4h a market offers six independent
#: setups a day, and taking more means taking them inside each other.
OVERTRADING_PER_DAY = 6


class Pattern(StrEnum):
    #: Size or leverage raised while a losing streak is running.
    REVENGE = "REVENGE"
    #: Risk raised after a loss, at any streak length.
    ESCALATION_AFTER_LOSS = "ESCALATION_AFTER_LOSS"
    #: Leverage raised above the previous plan's, after a loss.
    LEVERAGE_ESCALATION = "LEVERAGE_ESCALATION"
    #: More plans today than the horizon can independently offer.
    OVERTRADING = "OVERTRADING"
    #: A run of losses, with no escalation. Named, not warned about.
    LOSING_STREAK = "LOSING_STREAK"


@dataclass(frozen=True, slots=True)
class DisciplineFinding:
    pattern: Pattern
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": self.pattern.value, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class DisciplineReport:
    """What the recent record says about the person reading it."""

    checked_at: datetime
    findings: tuple[DisciplineFinding, ...] = field(default_factory=tuple)
    #: Losses in a row, ending at the most recent resolved plan.
    loss_streak: int = 0
    plans_today: int = 0
    #: False when the history handed in carried no outcome column at all.
    #:
    #: Then `loss_streak` is 0 because nothing was read, not because nothing
    #: was lost, and saying "clean" would be an affirmative claim about a
    #: question never asked. This is exactly the state that existed in
    #: production until the history query learned to join its results.
    outcomes_known: bool = True

    @property
    def clean(self) -> bool:
        """No pattern found - and the outcomes were actually looked at.

        Without the second half this returned True for a system that had never
        read an outcome in its life.
        """
        return not self.findings and self.outcomes_known

    @property
    def patterns(self) -> tuple[Pattern, ...]:
        return tuple(f.pattern for f in self.findings)

    def as_caveats(self) -> tuple[str, ...]:
        """Text to carry into the plan itself.

        Carried rather than merely logged, because the reader who most needs to
        see it is the one who is not currently reading logs.
        """
        return tuple(f.detail for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": isoformat(self.checked_at),
            "clean": self.clean,
            "outcomes_known": self.outcomes_known,
            "loss_streak": self.loss_streak if self.outcomes_known else None,
            "plans_today": self.plans_today,
            "findings": [f.to_dict() for f in self.findings],
            "note": (
                "saran, bukan veto. ARUNA tidak menahan sebuah plan gara-gara "
                "kerugian kemarin - itu keputusan position management, dan itu "
                "ada di tangan Anda (FUTURES SPEC 34, 50)"
            ),
        }


def review(
    *,
    history: list[dict[str, Any]],
    proposed_risk: Decimal | None = None,
    proposed_leverage: int | None = None,
    reference: datetime | None = None,
    horizon_hours: float = 4.0,
) -> DisciplineReport:
    """Read the recent record for the patterns FUTURES SPEC 34 names.

    ``history`` is newest-first, as the repository returns it: each entry needs
    ``created_at``, and optionally ``outcome``, ``risk_amount`` and
    ``leverage``. Entries that never became positions are counted for
    overtrading but cannot win or lose, so they never enter a streak.
    """
    now = reference or _utcnow()
    findings: list[DisciplineFinding] = []

    # A row that carries no `outcome` KEY has not been joined to its result;
    # a row that carries `outcome=None` has been joined and simply has not
    # resolved yet. The two look identical to `.get()` and they are not the
    # same fact, and the difference decides whether "no losing streak" is an
    # answer or an absence of one.
    joined = any("outcome" in row for row in history)
    resolved = [
        row
        for row in history
        if row.get("outcome") in {"TARGET_HIT", "STOPPED_OUT", "LIQUIDATED"}
    ]
    streak = _loss_streak(resolved, now)

    # ---- 1. a streak, stated whether or not anything else is wrong ---------
    if streak >= REVENGE_LOSS_STREAK:
        findings.append(
            DisciplineFinding(
                Pattern.LOSING_STREAK,
                f"{streak} kerugian berturut-turut dalam "
                f"{STREAK_WINDOW_HOURS} jam terakhir. Tidak ada di sini yang "
                "bilang setup berikutnya lebih buruk dari kelihatannya - tapi "
                "tidak ada juga yang bilang lebih baik, dan rentetan sepanjang "
                "ini adalah titik di mana menaikkan position size mulai terasa "
                "masuk akal",
            )
        )

    last_loss = _most_recent_loss(resolved)

    # ---- 2. risk raised after a loss --------------------------------------
    if last_loss is not None and proposed_risk is not None:
        previous = _decimal(last_loss.get("risk_amount"))
        if previous is not None and previous > 0:
            ratio = proposed_risk / previous
            if ratio >= SIZE_ESCALATION_RATIO:
                pattern = (
                    Pattern.REVENGE
                    if streak >= REVENGE_LOSS_STREAK
                    else Pattern.ESCALATION_AFTER_LOSS
                )
                findings.append(
                    DisciplineFinding(
                        pattern,
                        f"plan ini mempertaruhkan {ratio:.2f}x dari plan "
                        "terakhir yang rugi. Menaikkan position size setelah "
                        "rugi tidak mengembalikan kerugian itu; hanya "
                        "memperbesar kerugian berikutnya, dan kerugianlah yang "
                        "berlipat",
                    )
                )

    # ---- 3. leverage raised after a loss ----------------------------------
    if last_loss is not None and proposed_leverage is not None:
        previous_leverage = last_loss.get("leverage")
        if previous_leverage and proposed_leverage > int(previous_leverage):
            findings.append(
                DisciplineFinding(
                    Pattern.LEVERAGE_ESCALATION,
                    f"leverage {proposed_leverage}x lebih tinggi dari "
                    f"{int(previous_leverage)}x pada plan terakhir yang rugi. "
                    "Dengan position size berbasis risiko, ini tidak menambah "
                    "profit - hanya mendekatkan liquidation, jadi pemulihan "
                    "yang Anda rasakan itu bukan pemulihan",
                )
            )

    # ---- 4. too many plans in a day ---------------------------------------
    #
    # Counts PLAN verdicts only. Refusals were counted here at first, on the
    # reasoning that a refused plan is still a look at the market and hunting is
    # hunting - which is true of a person running the command twenty times, and
    # false of a scheduler. Once the loop ran every fifteen minutes it flagged
    # itself, and a warning that fires on ARUNA's own diligence is noise.
    #
    # What overtrading is actually about is positions **taken**. A refusal is
    # the gates working; it is not a trade.
    cutoff = now - timedelta(hours=24)
    today = [
        row
        for row in history
        if _at(row) is not None
        and _at(row) >= cutoff
        and row.get("verdict") in (None, "PLAN")
    ]
    limit = max(1, int(24 / horizon_hours)) if horizon_hours > 0 else OVERTRADING_PER_DAY
    if len(today) > limit:
        findings.append(
            DisciplineFinding(
                Pattern.OVERTRADING,
                f"{len(today)} plan dalam 24 jam melawan horizon "
                f"{horizon_hours:g} jam, yang hanya menawarkan sekitar {limit} "
                "setup independen. Lewat dari itu setup-nya saling tumpang "
                f"tindih, jadi ini bukan {len(today)} taruhan terpisah - ini "
                "satu taruhan yang diambil berulang kali",
            )
        )

    return DisciplineReport(
        checked_at=now,
        findings=tuple(findings),
        loss_streak=streak,
        plans_today=len(today),
        outcomes_known=joined or not history,
    )


def _utcnow() -> datetime:
    from aruna.core.clock import now_utc

    return now_utc()


def _at(row: dict[str, Any]) -> datetime | None:
    value = row.get("created_at")
    return value if isinstance(value, datetime) else None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _loss_streak(resolved: list[dict[str, Any]], now: datetime) -> int:
    """Consecutive losses ending at the most recent resolved plan.

    A win breaks the streak. So does falling out of the window: losses a
    fortnight apart are a sample, not a run, and treating them as one would
    make the warning fire forever on a system that trades rarely.
    """
    cutoff = now - timedelta(hours=STREAK_WINDOW_HOURS)
    streak = 0
    for row in resolved:
        at = _at(row)
        if at is not None and at < cutoff:
            break
        if row.get("outcome") == "TARGET_HIT":
            break
        streak += 1
    return streak


def _most_recent_loss(resolved: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in resolved:
        if row.get("outcome") in {"STOPPED_OUT", "LIQUIDATED"}:
            return row
    return None


__all__ = [
    "OVERTRADING_PER_DAY",
    "REVENGE_LOSS_STREAK",
    "SIZE_ESCALATION_RATIO",
    "STREAK_WINDOW_HOURS",
    "DisciplineFinding",
    "DisciplineReport",
    "Pattern",
    "review",
]
