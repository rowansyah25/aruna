"""Research questions ARUNA raises about itself (SPEC 31, 44).

A question here is *derived from a measurement*, never invented. Each carries
the numbers that provoked it, so a reader can check whether the question is
worth asking rather than taking the system's word for it.

The distinction that matters: this module asks questions, it does not answer
them and it does not change anything. A system that noticed a problem and
quietly adjusted itself would be untestable - the change would arrive with no
hypothesis, no comparison, and nobody accountable for it. Answers go through
:mod:`aruna.governance.proposal`, and from there past a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class QuestionSource(StrEnum):
    BACKTEST = "BACKTEST"
    CALIBRATION = "CALIBRATION"
    AUTOPSY = "AUTOPSY"
    OBJECTIONS = "OBJECTIONS"
    DRIFT = "DRIFT"


class QuestionStatus(StrEnum):
    OPEN = "OPEN"
    #: A proposal has been raised against it.
    PROPOSED = "PROPOSED"
    #: Answered, or overtaken by events.
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class ResearchQuestion:
    """One thing the record says is worth investigating."""

    key: str
    question: str
    source: QuestionSource
    #: The measurements that provoked it. Without these it is an opinion.
    evidence: tuple[str, ...] = field(default_factory=tuple)
    #: How much of the system's behaviour this touches, 0..1. Used to order the
    #: list, never to justify a change on its own.
    severity: float = 0.5
    status: QuestionStatus = QuestionStatus.OPEN

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "question": self.question,
            "source": self.source.value,
            "evidence": list(self.evidence),
            "severity": round(self.severity, 3),
            "status": self.status.value,
            "note": (
                "a question, not a finding: it says the record is worth "
                "investigating, not that a change is warranted"
            ),
        }


#: Below this a strategy pays more to trade than it makes. Not a preference -
#: at a cost ratio above 1 the gross edge does not cover the fees, and every
#: additional trade loses money on average.
COST_RATIO_ALARM = 1.0

#: A calibration gap this wide is worth a question rather than a shrug.
CALIBRATION_GAP_ALARM = 0.10

#: An objection ground vindicated this often, having been overruled, is
#: pointing at something the council keeps refusing to hear.
VINDICATION_ALARM = 0.6


def _provenance(run: dict[str, Any]) -> str:
    """Which world the figures below were measured in.

    PnL is a number in the market's own quote currency, produced by one fee
    schedule against one notional, and none of that is visible in the figure
    itself. Drop this line and "net PnL -2953814.17" reads as dollars when it
    was rupiah, and as a 0.10% cost regime when it was 0.30% - which is what
    four OPEN questions did until migration 0021. A question that misstates its
    own units points a proposal at the wrong cause (SPEC 49).

    ``cost_basis`` comes from :func:`aruna.db.repositories.backtest.cost_basis`
    via ``recent_runs``. When a caller passes a run without it, this says so
    rather than inventing a schedule (SPEC 4).
    """
    market = str(run.get("market") or "").strip()
    basis = str(run.get("cost_basis") or "").strip()
    if market and basis:
        return (
            f"figures are {market} paper trades in that market's quote "
            f"currency, on cost basis {basis}"
        )
    if market:
        return (
            f"figures are {market} paper trades in that market's quote "
            "currency; the cost basis behind them was not recorded, so they "
            "are not comparable across fee schedules"
        )
    return (
        "neither market nor cost basis was recorded with this run, so the "
        "figures above carry no currency and no fee schedule"
    )


def questions_from_backtest(run: dict[str, Any]) -> list[ResearchQuestion]:
    """What a backtest result says about the rules that produced it.

    A known limit, written here rather than left to be discovered: the keys
    below carry the interval but not the market, and :func:`rank` deduplicates
    by key. Two markets backtested at the same interval therefore collapse into
    one question, and the survivor is whichever had the higher severity. The
    evidence line from :func:`_provenance` at least names which market's figures
    the survivor is quoting.
    """
    out: list[ResearchQuestion] = []
    combined = run.get("combined") or {}
    trades = combined.get("paper_trades") or {}
    resolved = int(combined.get("resolved") or 0)
    if not resolved:
        return out

    cost_ratio = trades.get("cost_ratio")
    interval = run.get("interval", "?")
    if cost_ratio is not None and float(cost_ratio) > COST_RATIO_ALARM:
        out.append(
            ResearchQuestion(
                key=f"costs_exceed_edge_{interval}",
                question=(
                    f"Di horizon {interval}, ongkos transaksi memakan "
                    f"{float(cost_ratio):.2f} kali seluruh keuntungan kotor. "
                    "Artinya seakurat apa pun tebakan arahnya, hasil bersihnya "
                    "tetap negatif. Apakah horizon ini memang terlalu pendek "
                    "untuk menutup biayanya, atau ongkosnya yang bisa ditekan?"
                ),
                source=QuestionSource.BACKTEST,
                evidence=(
                    f"rasio ongkos {float(cost_ratio):.2f} - ongkos sebesar itu "
                    "dibanding keuntungan kotarnya, sebelum tanda plus-minus",
                    f"PnL bersih {trades.get('net_pnl')} dari "
                    f"{trades.get('trades')} paper trade yang sudah ditutup",
                    f"PnL kotor {trades.get('gross_pnl')} - selisihnya dengan "
                    "yang bersih adalah fee, spread, dan slippage",
                    # Last, immediately under the two figures it qualifies:
                    # this is the only line that says what currency they are in
                    # and which fee schedule produced the ratio above.
                    _provenance(run),
                ),
                severity=min(1.0, 0.5 + float(cost_ratio) / 20),
            )
        )

    accuracy = combined.get("direction_accuracy")
    if accuracy is not None and resolved >= 100:
        value = float(accuracy)
        if value < 0.45:
            out.append(
                ResearchQuestion(
                    key=f"accuracy_below_chance_{interval}",
                    question=(
                        f"Di horizon {interval}, tebakan arah benar hanya "
                        f"{value * 100:.0f}% dari {resolved} prediksi - lebih "
                        "buruk daripada melempar koin. Apakah pembacaannya "
                        "terbalik secara sistematis, atau sampelnya saling "
                        "tumpang tindih sehingga angka ini menipu?"
                    ),
                    source=QuestionSource.BACKTEST,
                    evidence=(
                        f"{combined.get('direction_correct')} benar dari "
                        f"{resolved} prediksi",
                        "prediksi berurutan pada satu aset saling tumpang tindih "
                        "waktunya, jadi sampel efektifnya lebih kecil daripada "
                        "jumlahnya - angka ini belum tentu seburuk kelihatannya",
                        _provenance(run),
                    ),
                    severity=0.9,
                )
            )
        elif 0.47 <= value <= 0.53:
            out.append(
                ResearchQuestion(
                    key=f"no_measurable_edge_{interval}",
                    question=(
                        f"Di horizon {interval}, tebakan arah benar "
                        f"{value * 100:.0f}% dari {resolved} prediksi - tidak "
                        "bisa dibedakan dari menebak acak. Apakah council punya "
                        "kemampuan membaca arah sama sekali di horizon ini?"
                    ),
                    source=QuestionSource.BACKTEST,
                    evidence=(
                        f"{combined.get('direction_correct')} benar dari "
                        f"{resolved} prediksi",
                        "kalau edge-nya memang nol, tidak ada penghematan ongkos "
                        "atau perbaikan eksekusi yang bisa menolongnya",
                        _provenance(run),
                    ),
                    severity=1.0,
                )
            )
    return out


def questions_from_calibration(report: dict[str, Any]) -> list[ResearchQuestion]:
    """Where stated confidence and realised accuracy disagree (SPEC 29)."""
    out: list[ResearchQuestion] = []
    for bucket in report.get("buckets") or []:
        gap = bucket.get("gap")
        if gap is None or abs(float(gap)) <= CALIBRATION_GAP_ALARM:
            continue
        direction = "terlalu percaya diri" if float(gap) > 0 else "terlalu ragu"
        out.append(
            ResearchQuestion(
                key=f"calibration_{bucket['bucket'].replace('%', '')}",
                question=(
                    f"Pada pita confidence {bucket['bucket']}, council "
                    f"{direction} sebesar {abs(float(gap)) * 100:.0f} poin - "
                    "angka keyakinan yang dicetak tidak cocok dengan seberapa "
                    "sering ia ternyata benar. Faktor pembobot mana yang "
                    "membuatnya meleset?"
                ),
                source=QuestionSource.CALIBRATION,
                evidence=(
                    f"menyatakan {float(bucket['mean_confidence']) * 100:.0f}%, "
                    f"kenyataannya {float(bucket['accuracy']) * 100:.0f}%",
                    f"dari {bucket['predictions']} prediksi yang sudah selesai",
                ),
                severity=min(1.0, abs(float(gap)) * 3),
            )
        )
    return out


def questions_from_objections(records: list[dict[str, Any]]) -> list[ResearchQuestion]:
    """Objections the council keeps overruling and the market keeps proving
    right (SPEC 26)."""
    out: list[ResearchQuestion] = []
    for record in records:
        raised = int(record.get("raised_and_overruled") or 0)
        rate = record.get("vindication_rate")
        if raised < 10 or rate is None or float(rate) < VINDICATION_ALARM:
            continue
        out.append(
            ResearchQuestion(
                key=f"objection_{record['accuser']}_{record['ground']}".lower(),
                question=(
                    f"Agent {record['accuser']} mengajukan keberatan atas dasar "
                    f"'{record['ground']}', keberatannya ditolak council, dan "
                    f"ternyata ia benar {float(rate) * 100:.0f}% dari waktu. "
                    "Apakah dasar keberatan itu layak diberi bobot lebih besar, "
                    "atau bahkan hak veto?"
                ),
                source=QuestionSource.OBJECTIONS,
                evidence=(
                    f"{record['vindicated']} kali terbukti benar dari {raised} "
                    "keberatan yang ditolak",
                ),
                severity=min(1.0, float(rate)),
            )
        )
    return out


def questions_from_autopsies(autopsies: list[dict[str, Any]]) -> list[ResearchQuestion]:
    """Patterns across losses, not the losses themselves (SPEC 25)."""
    if len(autopsies) < 10:
        # A pattern needs more than a handful of examples; below this the
        # "pattern" is whatever happened twice.
        return []

    classes: dict[str, int] = {}
    for autopsy in autopsies:
        key = str(autopsy.get("outcome_class"))
        classes[key] = classes.get(key, 0) + 1

    out: list[ResearchQuestion] = []
    total = len(autopsies)
    dominant, count = max(classes.items(), key=lambda kv: kv[1])
    if count / total >= 0.5:
        out.append(
            ResearchQuestion(
                key=f"dominant_failure_{dominant.lower()}",
                question=(
                    f"{count} dari {total} kekalahan berbentuk {dominant} - "
                    "satu pola yang sama berulang, bukan sebaran acak. Asumsi "
                    "tunggal apa di dalam ARUNA yang menghasilkan bentuk "
                    "kegagalan itu?"
                ),
                source=QuestionSource.AUTOPSY,
                evidence=tuple(f"{name}: {n}" for name, n in sorted(classes.items())),
                severity=round(count / total, 3),
            )
        )
    return out


def rank(questions: list[ResearchQuestion]) -> list[ResearchQuestion]:
    """Most severe first, deduplicated by key."""
    seen: dict[str, ResearchQuestion] = {}
    for question in questions:
        if question.key not in seen or question.severity > seen[question.key].severity:
            seen[question.key] = question
    return sorted(seen.values(), key=lambda q: q.severity, reverse=True)


__all__ = [
    "CALIBRATION_GAP_ALARM",
    "COST_RATIO_ALARM",
    "VINDICATION_ALARM",
    "QuestionSource",
    "QuestionStatus",
    "ResearchQuestion",
    "questions_from_autopsies",
    "questions_from_backtest",
    "questions_from_calibration",
    "questions_from_objections",
    "rank",
]
