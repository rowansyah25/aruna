"""Data integrity for futures inputs (FUTURES SPEC 4, 46, 52).

Spot needed one question: is this price fresh? A perpetual needs a harder one:
**are these six inputs describing the same moment?**

A mark price from two seconds ago and an open-interest reading from twenty
minutes ago do not describe one market. Combined they describe a market that
never existed, and nothing about either number reveals the problem - both look
perfectly valid on their own. This module is the only place that can see it,
because it is the only place that holds them together.

The verdict is deliberately blunt. FUTURES SPEC 46 says ``BLOCK SIGNAL`` and
SPEC 52 says ``NO SIGNAL`` when data is stale; there is no "degraded but
usable" state here, because a leveraged position sized from stale liquidity is
not degraded, it is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from aruna.core.clock import isoformat, now_utc
from aruna.futures.models import FuturesSnapshot

#: How old a continuously-published input may be before the set is refused.
MAX_INPUT_AGE_SEC = 60

#: How far apart the newest and oldest *continuous* input may be. Tighter than
#: the absolute age limit on purpose: two inputs can both be recent and still
#: disagree about which minute it is.
MAX_INPUT_SKEW_SEC = 30

#: A *settled* funding rate belongs to a past settlement, up to eight hours ago.
#: A live estimate does not - it arrives on the same call as the mark price and
#: is judged at :data:`MAX_INPUT_AGE_SEC` like any other fresh reading.
MAX_SETTLED_FUNDING_AGE_SEC = 9 * 3600

#: Positioning is published in five-minute buckets, so the newest point is
#: always up to five minutes old *by construction*. Judged at the continuous
#: limit it fails every single time - the first live run blocked 100% of
#: signals on a 236-second-old long/short reading, which is not staleness, it
#: is the publication cadence.
MAX_BUCKETED_AGE_SEC = 15 * 60

#: Inputs published in buckets rather than continuously. Excluded from the skew
#: test for the same reason: they are always older than a tick, so including
#: them would make every coherent set look skewed.
BUCKETED_INPUTS: frozenset[str] = frozenset({"long_short"})

#: A provider clock ahead of ours by more than this is a real problem: it makes
#: every reading look newer than it is, and a large enough skew would hide
#: genuine staleness. Observed on this venue at about 7 seconds.
MAX_CLOCK_LEAD_SEC = 60


class IntegrityVerdict(StrEnum):
    OK = "OK"
    #: One or more required inputs never arrived.
    INCOMPLETE = "INCOMPLETE"
    #: An input is older than the market it claims to describe.
    STALE = "STALE"
    #: Inputs are individually fresh but describe different moments.
    SKEWED = "SKEWED"
    #: The venue's clock is far enough ahead of ours to hide staleness.
    CLOCK_SKEW = "CLOCK_SKEW"


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    verdict: IntegrityVerdict
    checked_at: datetime
    #: Per-input age in seconds, for the operator who has to fix the feed.
    ages: dict[str, float] = field(default_factory=dict)
    skew_sec: float | None = None
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocks_signal(self) -> bool:
        """FUTURES SPEC 46: anything but OK blocks."""
        return self.verdict is not IntegrityVerdict.OK

    def summary(self) -> str:
        if not self.blocks_signal:
            return f"integritas data OK; input tertua {self._oldest():.0f}s"
        return f"{self.verdict.value}: " + "; ".join(self.findings)

    def _oldest(self) -> float:
        return max(self.ages.values()) if self.ages else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "checked_at": isoformat(self.checked_at),
            "ages_sec": {k: round(v, 3) for k, v in self.ages.items()},
            "skew_sec": round(self.skew_sec, 3) if self.skew_sec is not None else None,
            "blocks_signal": self.blocks_signal,
            "findings": list(self.findings),
            "note": (
                "posisi ber-leverage yang di-size dari likuiditas basi bukan "
                "sekadar turun kualitasnya, tapi salah - jadi tidak ada "
                "kondisi yang masih bisa dipakai di antara OK dan diblokir "
                "(FUTURES SPEC 46, 52)"
            ),
        }


def check(
    snapshot: FuturesSnapshot, *, reference: datetime | None = None
) -> IntegrityReport:
    """Decide whether this set of inputs may produce a signal."""
    now = reference or now_utc()
    findings: list[str] = []

    if snapshot.missing:
        findings.append(
            f"input yang hilang: {', '.join(snapshot.missing)} - venue tidak "
            "menyediakannya dan tidak ada yang disubstitusikan"
        )
        return IntegrityReport(
            verdict=IntegrityVerdict.INCOMPLETE,
            checked_at=now,
            findings=tuple(findings),
        )

    ages = _ages(snapshot, now)
    settled_funding = bool(snapshot.funding and snapshot.funding.settled)

    # A negative age means the venue stamped the reading in our future. A little
    # of that is ordinary clock drift; a lot of it would make a stale reading
    # look fresh, so it is caught before the staleness test can be fooled by it.
    ahead = {name: -age for name, age in ages.items() if -age > MAX_CLOCK_LEAD_SEC}
    if ahead:
        findings.append(
            "jam venue mendahului jam kita sebesar "
            + ", ".join(f"{name} {lead:.0f}s" for name, lead in ahead.items())
            + " - pembacaannya akan tampak lebih segar daripada kenyataannya"
        )
        return IntegrityReport(
            verdict=IntegrityVerdict.CLOCK_SKEW,
            checked_at=now,
            ages=ages,
            findings=tuple(findings),
        )

    stale = [
        f"{name} berumur {age:.0f}s (batas {_limit_for(name, settled_funding):.0f}s)"
        for name, age in ages.items()
        if age > _limit_for(name, settled_funding)
    ]
    if stale:
        findings.append("sudah basi: " + ", ".join(stale))
        return IntegrityReport(
            verdict=IntegrityVerdict.STALE,
            checked_at=now,
            ages=ages,
            findings=tuple(findings),
        )

    # Bucketed series are excluded from the skew test for the same reason they
    # have their own age limits: they are always older than a tick, so including
    # them would make every coherent set look skewed.
    excluded = set(BUCKETED_INPUTS)
    if settled_funding:
        excluded.add("funding")
    live = {k: v for k, v in ages.items() if k not in excluded}
    skew = max(live.values()) - min(live.values()) if len(live) > 1 else 0.0
    if skew > MAX_INPUT_SKEW_SEC:
        findings.append(
            f"rentang input mencapai {skew:.0f}s: masing-masing memang masih "
            "segar tapi tidak menggambarkan momen yang sama"
        )
        return IntegrityReport(
            verdict=IntegrityVerdict.SKEWED,
            checked_at=now,
            ages=ages,
            skew_sec=skew,
            findings=tuple(findings),
        )

    return IntegrityReport(
        verdict=IntegrityVerdict.OK, checked_at=now, ages=ages, skew_sec=skew
    )


def _ages(snapshot: FuturesSnapshot, now: datetime) -> dict[str, float]:
    ages: dict[str, float] = {}
    if snapshot.mark:
        ages["mark_price"] = (now - snapshot.mark.as_of).total_seconds()
    if snapshot.funding:
        ages["funding"] = (now - snapshot.funding.funding_time).total_seconds()
    if snapshot.open_interest:
        ages["open_interest"] = (
            now - snapshot.open_interest.as_of
        ).total_seconds()
    book_at = getattr(snapshot.order_book, "as_of", None)
    if book_at is not None:
        ages["order_book"] = (now - book_at).total_seconds()
    if snapshot.long_short:
        ages["long_short"] = (now - snapshot.long_short.as_of).total_seconds()
    return ages


def _limit_for(name: str, settled_funding: bool) -> float:
    """Each input is judged on the schedule it is actually published on.

    Using one clock for all of them is how a check becomes unpassable: the
    live run that first reached this venue was blocked by a long/short reading
    236 seconds old, which is not stale - it is a five-minute bucket.
    """
    if name == "funding":
        # A settled rate belongs to a past settlement; a live estimate arrives
        # with the mark price and is fresh data.
        return MAX_SETTLED_FUNDING_AGE_SEC if settled_funding else MAX_INPUT_AGE_SEC
    if name in BUCKETED_INPUTS:
        return MAX_BUCKETED_AGE_SEC
    return MAX_INPUT_AGE_SEC


__all__ = [
    "BUCKETED_INPUTS",
    "MAX_BUCKETED_AGE_SEC",
    "MAX_CLOCK_LEAD_SEC",
    "MAX_INPUT_AGE_SEC",
    "MAX_INPUT_SKEW_SEC",
    "MAX_SETTLED_FUNDING_AGE_SEC",
    "IntegrityReport",
    "IntegrityVerdict",
    "check",
]
