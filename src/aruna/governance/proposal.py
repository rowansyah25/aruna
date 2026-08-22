"""Model change proposals and their validation (SPEC 44).

A proposal is a written hypothesis with a comparison attached. It cannot become
active by being convincing; it becomes active when a human approves it, and only
after the evidence has cleared a bar that this module refuses to lower.

Three failure modes are designed against, because each one produces a change
that looks justified and is not:

* **Thin evidence.** A variant that beat the baseline over 30 predictions has
  shown nothing. :data:`MIN_VALIDATION_SAMPLE` is the floor, and below it the
  verdict is ``INSUFFICIENT_SAMPLE`` rather than a number.
* **Noise mistaken for improvement.** A few points of accuracy on a few hundred
  trials is well inside the range chance produces. The verdict compares the
  effect against its own standard error and says ``WITHIN_NOISE`` when it
  cannot be distinguished.
* **Multiple comparisons.** Test twenty variants and one will look good.
  :func:`validate` is told how many have already been tested and tightens the
  bar accordingly - the single most common way a backtest lies to the person
  running it.

None of this makes a proposal *right*. It makes an unjustified one visibly
unjustified, which is the most a governance layer can honestly offer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from aruna.core.clock import isoformat
from aruna.core.errors import ArunaError

#: Resolved predictions a variant needs before its result is reported at all.
MIN_VALIDATION_SAMPLE = 100

#: Improvement in direction accuracy, in points, that is worth acting on before
#: the noise test even runs. Below this the change is not worth the risk of
#: touching a system that people rely on.
MIN_EFFECT_POINTS = 0.02

#: Standard errors of separation required to call an effect real. Raised as
#: more variants are tested - see :func:`required_sigma`.
BASE_SIGMA = 2.0


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    #: Running alongside the live model, not acted on.
    SHADOWED = "SHADOWED"
    #: Compared against the baseline; verdict recorded.
    VALIDATED = "VALIDATED"
    #: Evidence cleared the bar; waiting for a person.
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    #: Withdrawn before a decision.
    ABANDONED = "ABANDONED"


class Verdict(StrEnum):
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    NO_IMPROVEMENT = "NO_IMPROVEMENT"
    WITHIN_NOISE = "WITHIN_NOISE"
    IMPROVED = "IMPROVED"


class ApprovalError(ArunaError):
    """An attempt to activate a change without a human, or out of order."""


@dataclass(frozen=True, slots=True)
class Arm:
    """One side of a comparison."""

    label: str
    resolved: int = 0
    correct: int = 0
    net_pnl: str = "0"

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.resolved if self.resolved else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "resolved": self.resolved,
            "correct": self.correct,
            "accuracy": round(self.accuracy, 4) if self.accuracy is not None else None,
            "net_pnl": self.net_pnl,
        }


@dataclass(frozen=True, slots=True)
class Validation:
    """What the comparison supports, and what it does not."""

    verdict: Verdict
    baseline: Arm
    variant: Arm
    effect_points: float = 0.0
    sigma: float = 0.0
    required_sigma: float = BASE_SIGMA
    variants_tested: int = 1
    #: True only when the variant was measured on data reserved from tuning.
    out_of_sample: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_approval(self) -> bool:
        """Whether a human may be asked to approve this at all.

        Not "whether to approve" - that is the human's judgement, and this
        deliberately cannot make it.
        """
        return self.verdict is Verdict.IMPROVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "baseline": self.baseline.to_dict(),
            "variant": self.variant.to_dict(),
            "effect_points": round(self.effect_points * 100, 2),
            "sigma": round(self.sigma, 2),
            "required_sigma": round(self.required_sigma, 2),
            "variants_tested": self.variants_tested,
            "out_of_sample": self.out_of_sample,
            "reasons": list(self.reasons),
            "supports_approval": self.supports_approval,
            "note": (
                "clearing this bar means the evidence is worth a human's "
                "attention; it is not a recommendation, and no change activates "
                "without an explicit human decision (SPEC 44)"
            ),
        }


def _pnl_note(baseline: Arm, variant: Arm) -> str | None:
    """Report a PnL difference the accuracy comparison cannot see.

    A change to *exit* rules moves money without moving direction accuracy by a
    single point, so the headline comparison reads "no effect" while the variant
    is materially better or worse. That happened on the first real proposal
    tested: an exit-at-target variant scored identically on accuracy and lost an
    extra 463,540 IDR.

    Reported rather than scored: PnL over a few hundred overlapping trades is
    far noisier than a hit rate, and promoting it to the verdict would let one
    lucky run carry a change through the gate.
    """
    try:
        base = float(baseline.net_pnl)
        var = float(variant.net_pnl)
    except (TypeError, ValueError):
        return None
    if base == var:
        return None
    direction = "better" if var > base else "WORSE"
    return (
        f"net PnL is {direction} by {abs(var - base):,.0f} "
        f"({base:,.0f} -> {var:,.0f}); direction accuracy cannot see this, and "
        "PnL is too noisy over this many overlapping trades to be scored"
    )


def required_sigma(variants_tested: int) -> float:
    """Bar for calling an effect real, given how many variants have been tried.

    Testing twenty variants and reporting the best one at two sigma is not
    evidence - at that count, one variant clearing two sigma is roughly what
    chance produces. The bar rises with the number of attempts, which is the
    cheapest honest correction available.
    """
    attempts = max(1, variants_tested)
    return round(BASE_SIGMA + math.log(attempts, 4), 3)


def validate(
    baseline: Arm,
    variant: Arm,
    *,
    variants_tested: int = 1,
    out_of_sample: bool = False,
) -> Validation:
    """Compare a variant against the current rules (SPEC 44)."""
    bar = required_sigma(variants_tested)
    reasons: list[str] = []

    if not out_of_sample:
        reasons.append(
            "measured on data that was not reserved from tuning - the result "
            "cannot rule out having been fitted to it"
        )

    smallest = min(baseline.resolved, variant.resolved)
    if smallest < MIN_VALIDATION_SAMPLE:
        reasons.append(
            f"{smallest} resolved prediction(s) on the smaller arm; "
            f"{MIN_VALIDATION_SAMPLE} needed before any comparison is reported"
        )
        return Validation(
            verdict=Verdict.INSUFFICIENT_SAMPLE,
            baseline=baseline,
            variant=variant,
            required_sigma=bar,
            variants_tested=variants_tested,
            out_of_sample=out_of_sample,
            reasons=tuple(reasons),
        )

    base_accuracy = baseline.accuracy or 0.0
    variant_accuracy = variant.accuracy or 0.0
    effect = variant_accuracy - base_accuracy

    # Standard error of the difference between two proportions.
    error = math.sqrt(
        base_accuracy * (1 - base_accuracy) / baseline.resolved
        + variant_accuracy * (1 - variant_accuracy) / variant.resolved
    )
    sigma = abs(effect) / error if error > 0 else 0.0

    pnl_note = _pnl_note(baseline, variant)
    if pnl_note:
        reasons.append(pnl_note)

    if effect < MIN_EFFECT_POINTS:
        reasons.append(
            f"variant is {effect * 100:+.1f} points on direction accuracy; "
            f"{MIN_EFFECT_POINTS * 100:.0f} points is the minimum worth the "
            "risk of changing a system in use"
        )
        verdict = Verdict.NO_IMPROVEMENT
    elif sigma < bar:
        reasons.append(
            f"{effect * 100:+.1f} points is {sigma:.1f} standard errors from "
            f"zero; {bar:.1f} required after {variants_tested} variant(s) "
            "tested. An effect this size arises by chance often enough that "
            "acting on it would be guessing"
        )
        verdict = Verdict.WITHIN_NOISE
    elif not out_of_sample:
        reasons.append(
            "the effect clears the noise bar, but in-sample only: re-run "
            "against the reserved holdout before asking for approval"
        )
        verdict = Verdict.WITHIN_NOISE
    else:
        reasons.append(
            f"{effect * 100:+.1f} points at {sigma:.1f} standard errors, "
            f"out of sample, against a {bar:.1f} bar"
        )
        verdict = Verdict.IMPROVED

    return Validation(
        verdict=verdict,
        baseline=baseline,
        variant=variant,
        effect_points=effect,
        sigma=sigma,
        required_sigma=bar,
        variants_tested=variants_tested,
        out_of_sample=out_of_sample,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class ModelProposal:
    """A proposed change to how ARUNA decides."""

    key: str
    title: str
    hypothesis: str
    change: str
    question_key: str | None = None
    #: Constant or rule changes, as ``name -> (current, proposed)``.
    parameters: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    status: ProposalStatus = ProposalStatus.DRAFT
    validation: Validation | None = None
    raised_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_note: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status is ProposalStatus.APPROVED

    def summary(self) -> str:
        verdict = self.validation.verdict.value if self.validation else "UNVALIDATED"
        return f"{self.key}  [{self.status.value}]  {verdict}  {self.title}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "change": self.change,
            "question_key": self.question_key,
            "parameters": [
                {"name": n, "current": c, "proposed": p} for n, c, p in self.parameters
            ],
            "status": self.status.value,
            "validation": self.validation.to_dict() if self.validation else None,
            "raised_at": isoformat(self.raised_at) if self.raised_at else None,
            "decided_at": isoformat(self.decided_at) if self.decided_at else None,
            "decided_by": self.decided_by,
            "decision_note": self.decision_note,
        }


#: Status yang tidak boleh mundur. Keputusan manusia adalah keputusan.
#:
#: Dieja SEKALI dan dipakai bersama :func:`ready_for_approval` dan
#: :meth:`aruna.governance.service.GovernanceService.validate_proposal` -
#: dua daftar yang harus tetap sepakat adalah dua yang suatu saat tidak.
#:
#: Terukur 2026-08-21, dan begitulah bentuk kegagalannya: ``exit-at-target``
#: disetujui 2026-08-15 11:39 oleh rowan, lalu validasi ulang 2026-08-17 15:29
#: menetapkan ``status=VALIDATED`` tanpa syarat dan memundurkannya. Tabel
#: keputusan tetap berkata APPROVED; keduanya berbohong satu sama lain, dan
#: penjaga "already approved" di bawah kalah - perubahan yang sama bisa
#: disetujui dua kali sebagai dua keputusan manusia yang terpisah.
STATUS_TERMINAL: frozenset[ProposalStatus] = frozenset(
    {ProposalStatus.APPROVED, ProposalStatus.REJECTED}
)


def ready_for_approval(proposal: ModelProposal) -> tuple[bool, str]:
    """Whether this may be put in front of a person (SPEC 44)."""
    if proposal.status in STATUS_TERMINAL:
        return False, f"already {proposal.status.value.lower()}"
    if proposal.validation is None:
        return False, "not validated: no comparison against the current rules"
    if not proposal.validation.supports_approval:
        return False, (
            f"validation says {proposal.validation.verdict.value} - "
            + (proposal.validation.reasons[0] if proposal.validation.reasons else "")
        )
    return True, "validated out of sample against the current rules"


__all__ = [
    "BASE_SIGMA",
    "MIN_EFFECT_POINTS",
    "MIN_VALIDATION_SAMPLE",
    "STATUS_TERMINAL",
    "ApprovalError",
    "Arm",
    "ModelProposal",
    "ProposalStatus",
    "Validation",
    "Verdict",
    "ready_for_approval",
    "required_sigma",
    "validate",
]
