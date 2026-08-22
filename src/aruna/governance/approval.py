"""The human approval gate (SPEC 44).

No change to how ARUNA decides becomes active without a person saying so, by
name, with the decision recorded.

This module is deliberately small and deliberately unhelpful in one specific
way: **there is no code path that approves a proposal on the system's own
authority.** :func:`approve` requires an actor and refuses an empty one. There
is no `auto_approve`, no confidence threshold above which approval is implied,
and no configuration key that could add one. A governance layer whose gate could
be opened by a sufficiently good number is not a gate.

The asymmetry between approval and rejection is intentional. Approval requires
validated evidence *and* a person; rejection requires only a person. Stopping a
change must never be harder than making one.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from aruna.core.clock import now_utc
from aruna.core.logging import get_logger
from aruna.governance.proposal import (
    ApprovalError,
    ModelProposal,
    ProposalStatus,
    ready_for_approval,
)

log = get_logger("aruna.governance.approval")

#: Recorded against every decision. An approval that cannot name a person is
#: not an approval, and "system" is not a person.
FORBIDDEN_ACTORS = frozenset({"", "system", "aruna", "auto", "automatic", "none"})


def _check_actor(actor: str) -> str:
    cleaned = (actor or "").strip()
    if cleaned.lower() in FORBIDDEN_ACTORS:
        raise ApprovalError(
            f"{actor!r} is not a person. A model change must be approved by a "
            "named human being who can be asked afterwards why they approved "
            "it (SPEC 44)."
        )
    return cleaned


def approve(
    proposal: ModelProposal,
    *,
    actor: str,
    note: str = "",
    at: datetime | None = None,
) -> ModelProposal:
    """Activate a proposal on a named person's authority.

    Refuses when the evidence has not cleared validation. That check is not
    protecting the human from a bad decision - they may still make one - it is
    making sure the decision is taken knowingly rather than on a number the
    system has already been told is indistinguishable from noise.
    """
    who = _check_actor(actor)
    allowed, reason = ready_for_approval(proposal)
    if not allowed:
        raise ApprovalError(
            f"cannot approve {proposal.key}: {reason}. Approving it anyway would "
            "mean acting on evidence the system has already reported as "
            "insufficient."
        )

    decided = replace(
        proposal,
        status=ProposalStatus.APPROVED,
        decided_at=at or now_utc(),
        decided_by=who,
        decision_note=note or None,
    )
    log.warning(
        "governance.proposal_approved",
        proposal=proposal.key,
        actor=who,
        verdict=(
            proposal.validation.verdict.value if proposal.validation else "NONE"
        ),
        detail="a human approved a change to how ARUNA decides",
    )
    return decided


def reject(
    proposal: ModelProposal,
    *,
    actor: str,
    note: str = "",
    at: datetime | None = None,
) -> ModelProposal:
    """Refuse a proposal. Needs a person; needs nothing else.

    No validation check: stopping a change must never be harder than making one,
    and a person who wants a proposal dead does not owe the system a statistic.
    """
    who = _check_actor(actor)
    if proposal.status is ProposalStatus.APPROVED:
        raise ApprovalError(
            f"{proposal.key} is already approved. Reversing an active change is "
            "a new proposal, so that the reversal is recorded and reviewed like "
            "any other change rather than quietly undone."
        )

    decided = replace(
        proposal,
        status=ProposalStatus.REJECTED,
        decided_at=at or now_utc(),
        decided_by=who,
        decision_note=note or None,
    )
    log.info("governance.proposal_rejected", proposal=proposal.key, actor=who)
    return decided


def submit_for_approval(proposal: ModelProposal) -> ModelProposal:
    """Move a validated proposal in front of a person.

    The system may do this much on its own: putting a question to a human is not
    a decision. Everything past this point requires one.
    """
    allowed, reason = ready_for_approval(proposal)
    if not allowed:
        raise ApprovalError(f"cannot submit {proposal.key}: {reason}")
    return replace(proposal, status=ProposalStatus.AWAITING_APPROVAL)


__all__ = ["FORBIDDEN_ACTORS", "approve", "reject", "submit_for_approval"]
