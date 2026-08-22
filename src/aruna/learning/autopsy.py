"""Loss autopsy and successful objections (SPEC 25, 26).

When a prediction loses, the useful question is not *that* it lost but which
part of the reasoning failed. The council record holds the whole argument -
every opinion, every objection, every rejected veto - so a loss can be traced
back to the specific claim that did not survive contact with the market.

SPEC 26 is the sharper half. An objection that was raised, overruled, and then
turned out to be correct is the most valuable record in the system: it names a
blind spot precisely, with a dissenting agent already attached to it. Those are
counted here.

**This module explains; it does not adjust.** Nothing in an autopsy feeds back
into a weight. Reliability (SPEC 30) does that, from a much larger sample and
with a stated threshold. A system that re-weighted itself after every loss
would be chasing noise, and would do it with great confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from aruna.core.enums import Decision, Stance, VetoReviewOutcome
from aruna.signals.models import OutcomeClass

#: Stance yang berarti menentang - lawan dari SUPPORT. Sama seperti
#: ``OBJECTING_STANCES`` di :mod:`aruna.db.repositories.learning`.
_OBJECTING_STANCES = frozenset(
    {Stance.OBJECT.value, Stance.COUNTER_PROPOSE.value}
)

#: Outcome classes that count as a failed prediction worth dissecting.
LOSING_CLASSES: frozenset[OutcomeClass] = frozenset(
    {
        OutcomeClass.WRONG_FROM_START,
        OutcomeClass.RIGHT_THEN_REVERSED,
        OutcomeClass.RIGHT_DIRECTION_BAD_TIMING,
    }
)

#: What each losing class most often means, in the system's own terms. Stated
#: as a hypothesis, never as a diagnosis: one loss cannot distinguish a broken
#: model from an ordinary unlucky draw.
FAILURE_HYPOTHESES: dict[OutcomeClass, str] = {
    OutcomeClass.WRONG_FROM_START: (
        "the read was wrong when it was made - the evidence supported the "
        "opposite call, or supported nothing"
    ),
    OutcomeClass.RIGHT_THEN_REVERSED: (
        "the read was right and the exit was not - the move happened and was "
        "given back inside the horizon"
    ),
    OutcomeClass.RIGHT_DIRECTION_BAD_TIMING: (
        "the direction was right on a longer timescale than the horizon allowed"
    ),
}


@dataclass(frozen=True, slots=True)
class Autopsy:
    """Why one prediction failed, reconstructed from the stored argument."""

    signal_id: str
    symbol: str
    horizon: str
    direction: Decision
    confidence: float
    outcome_class: OutcomeClass
    predicted_move_pct: float | None
    actual_move_pct: float
    max_adverse_pct: float
    net_pnl: str | None = None

    #: Conditions recorded at lock time, for pattern-hunting later.
    regime: str | None = None
    risk_level: str | None = None
    news_state: str | None = None

    #: Agents that backed the losing direction, heaviest first.
    backers: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    #: Agents that argued the other way and were overruled.
    dissenters: tuple[str, ...] = field(default_factory=tuple)
    #: Objections raised against the winning side that were not conceded.
    unanswered_objections: tuple[str, ...] = field(default_factory=tuple)
    #: Vetoes raised and rejected on review.
    rejected_vetoes: tuple[str, ...] = field(default_factory=tuple)
    findings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def hypothesis(self) -> str:
        return FAILURE_HYPOTHESES.get(self.outcome_class, "unclassified failure")

    @property
    def sebab(self) -> Any:
        """Kategori bagian 12: **kenapa**, bukan apa (:class:`SebabKalah`).

        Berdampingan dengan :attr:`hypothesis`, tidak menggantikannya. Yang
        lama menjawab apa yang terjadi - bacaan salah sejak awal, bacaan benar
        tapi keluarnya tidak - dan membuangnya menghapus keterangan yang tidak
        ada di kategori mana pun.
        """
        from aruna.learning.sebab import klasifikasi

        return klasifikasi(self)

    def summary(self) -> str:
        return (
            f"{self.symbol} {self.horizon} {self.direction.value} "
            f"{self.confidence * 100:.0f}% -> {self.outcome_class.value} "
            f"({self.actual_move_pct:+.2f}%)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "horizon": self.horizon,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 3),
            "outcome_class": self.outcome_class.value,
            "hypothesis": self.hypothesis,
            # Bagian 12. Ikut tersimpan, bukan dihitung lalu dibuang -
            # keluarga cacat yang sudah berulang di repo ini.
            "sebab": self.sebab.value,
            "sebab_penjelasan": self.sebab.penjelasan,
            "predicted_move_pct": self.predicted_move_pct,
            "actual_move_pct": round(self.actual_move_pct, 4),
            "max_adverse_pct": round(self.max_adverse_pct, 4),
            "net_pnl": self.net_pnl,
            "conditions": {
                "regime": self.regime,
                "risk_level": self.risk_level,
                "news_state": self.news_state,
            },
            "backers": [{"agent": a, "weight": w} for a, w in self.backers],
            "dissenters": list(self.dissenters),
            "unanswered_objections": list(self.unanswered_objections),
            "rejected_vetoes": list(self.rejected_vetoes),
            "findings": list(self.findings),
            "note": (
                "an autopsy explains one loss; it does not adjust any weight - "
                "that needs a sample, and SPEC 30 handles it"
            ),
        }


def perform_autopsy(record: dict[str, Any]) -> Autopsy | None:
    """Dissect one losing prediction (SPEC 25).

    ``record`` is the joined row: the locked signal, its outcome, its paper
    trade, and the council session behind it. Returns ``None`` when the
    prediction did not lose - there is no autopsy to perform on a winner, and
    manufacturing one would bury the real failures in noise.
    """
    outcome_class = _outcome_class(record.get("outcome_class"))
    if outcome_class is None or outcome_class not in LOSING_CLASSES:
        return None

    direction = Decision(record["direction"])
    weights = record.get("weights") or []
    backers = tuple(
        sorted(
            (
                (w["role"], float(w.get("weight") or 0))
                for w in weights
                if w.get("decision") == direction.value
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
    )
    dissenters = tuple(
        w["role"]
        for w in weights
        if w.get("decision") in ("BUY", "SELL") and w.get("decision") != direction.value
    )

    # Dua perbandingan di bawah ini dulu memakai nilai yang tidak pernah ada:
    # "OPPOSE" (Stance tidak punya anggota itu) dan "REJECTED" (nilainya
    # VETO_REJECTED). Keduanya selalu kosong, jadi setiap autopsy melaporkan
    # "tidak ada objection yang tak terjawab" dan "tidak ada veto yang ditolak"
    # sebagai pernyataan fakta, padahal pertanyaannya tidak pernah diajukan -
    # dan baris _with_findings() yang bergantung padanya tidak pernah jalan.
    # Diambil dari enum sekarang, supaya salah ketik jadi error, bukan senyap.
    objections = record.get("objections") or []
    unanswered = tuple(
        f"{o['accuser']} -> {o['target']} [{o['ground']}]: {o.get('detail') or ''}"
        .strip()
        for o in objections
        if o.get("stance") in _OBJECTING_STANCES and not o.get("conceded")
    )
    rejected = tuple(
        f"{v['reason']}: {v.get('rationale') or 'ditolak setelah ditinjau'}"
        for v in (record.get("vetoes") or [])
        if v.get("outcome") == VetoReviewOutcome.VETO_REJECTED.value
    )

    autopsy = Autopsy(
        signal_id=record["signal_id"],
        symbol=record["symbol"],
        horizon=record["horizon_code"],
        direction=direction,
        confidence=float(record.get("confidence") or 0),
        outcome_class=outcome_class,
        predicted_move_pct=_number(record.get("predicted_move_pct")),
        actual_move_pct=_number(record.get("actual_move_pct")) or 0.0,
        max_adverse_pct=_number(record.get("max_adverse_pct")) or 0.0,
        net_pnl=str(record["net_pnl"]) if record.get("net_pnl") is not None else None,
        regime=record.get("regime"),
        risk_level=record.get("risk_level"),
        news_state=record.get("news_state"),
        backers=backers,
        dissenters=dissenters,
        unanswered_objections=unanswered,
        rejected_vetoes=rejected,
    )
    return _with_findings(autopsy)


def _with_findings(autopsy: Autopsy) -> Autopsy:
    """Observations about this loss. Each is checkable against the record."""
    findings: list[str] = [autopsy.hypothesis]

    if autopsy.dissenters:
        findings.append(
            f"{len(autopsy.dissenters)} agent(s) argued the other way and were "
            f"overruled: {', '.join(autopsy.dissenters)}"
        )
    if autopsy.unanswered_objections:
        findings.append(
            f"{len(autopsy.unanswered_objections)} objection(s) against this "
            "call were never conceded"
        )
    if autopsy.rejected_vetoes:
        findings.append(
            f"{len(autopsy.rejected_vetoes)} veto(es) were raised and rejected "
            "on review"
        )
    if autopsy.confidence >= 0.8:
        findings.append(
            f"stated at {autopsy.confidence * 100:.0f}% - a high-confidence "
            "loss is worth more than a marginal one when calibrating"
        )
    if (
        autopsy.predicted_move_pct is not None
        and autopsy.actual_move_pct * autopsy.predicted_move_pct > 0
        and abs(autopsy.actual_move_pct) < abs(autopsy.predicted_move_pct) / 2
    ):
        findings.append(
            "the direction was right but the move was less than half the size "
            "predicted - a magnitude problem, not a directional one"
        )
    if autopsy.max_adverse_pct < -2.0:
        findings.append(
            f"drew down {autopsy.max_adverse_pct:.2f}% against the position "
            "during the horizon"
        )
    return replace(autopsy, findings=tuple(findings))


@dataclass(frozen=True, slots=True)
class ObjectionRecord:
    """How one kind of objection has fared when overruled (SPEC 26)."""

    accuser: str
    ground: str
    raised: int = 0
    #: Raised, overruled, and the call it opposed then lost.
    vindicated: int = 0

    @property
    def vindication_rate(self) -> float | None:
        return round(self.vindicated / self.raised, 4) if self.raised else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuser": self.accuser,
            "ground": self.ground,
            "raised_and_overruled": self.raised,
            "vindicated": self.vindicated,
            "vindication_rate": self.vindication_rate,
        }


def successful_objections(rows: list[dict[str, Any]]) -> list[ObjectionRecord]:
    """Objections that were overruled and turned out right (SPEC 26).

    Each row is one overruled objection against a resolved prediction, carrying
    ``accuser``, ``ground`` and ``direction_correct``. The objection was
    vindicated exactly when the call it opposed was wrong.

    Sorted by vindication count: the point is to find the blind spot that keeps
    being pointed out and keeps being dismissed.
    """
    tallies: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        key = (str(row.get("accuser")), str(row.get("ground")))
        correct = row.get("direction_correct")
        if correct is None:
            continue
        tally = tallies.setdefault(key, [0, 0])
        tally[0] += 1
        tally[1] += int(not bool(correct))

    records = [
        ObjectionRecord(accuser=accuser, ground=ground, raised=raised, vindicated=won)
        for (accuser, ground), (raised, won) in tallies.items()
    ]
    records.sort(key=lambda r: (r.vindicated, r.raised), reverse=True)
    return records


def _outcome_class(value: Any) -> OutcomeClass | None:
    try:
        return OutcomeClass(value)
    except (ValueError, TypeError):
        return None


def _number(value: Any) -> float | None:
    return None if value is None else float(value)


__all__ = [
    "FAILURE_HYPOTHESES",
    "LOSING_CLASSES",
    "Autopsy",
    "ObjectionRecord",
    "perform_autopsy",
    "successful_objections",
]
