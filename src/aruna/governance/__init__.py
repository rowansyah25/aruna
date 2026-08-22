"""Model governance (PHASE 10).

Research questions, model proposals, shadow comparison, drift detection, and the
human approval gate.

Nothing here changes how ARUNA decides. It produces questions, evidence and a
decision record; the decision itself belongs to a person.
"""

from aruna.governance.approval import approve, reject, submit_for_approval
from aruna.governance.drift import DriftReport, Window, detect
from aruna.governance.proposal import (
    ApprovalError,
    Arm,
    ModelProposal,
    ProposalStatus,
    Validation,
    Verdict,
    ready_for_approval,
    required_sigma,
    validate,
)
from aruna.governance.research import (
    QuestionSource,
    QuestionStatus,
    ResearchQuestion,
    questions_from_autopsies,
    questions_from_backtest,
    questions_from_calibration,
    questions_from_objections,
    rank,
)
from aruna.governance.shadow import ShadowComparison, ShadowDecision, compare

__all__ = [
    "ApprovalError",
    "Arm",
    "DriftReport",
    "ModelProposal",
    "ProposalStatus",
    "QuestionSource",
    "QuestionStatus",
    "ResearchQuestion",
    "ShadowComparison",
    "ShadowDecision",
    "Validation",
    "Verdict",
    "Window",
    "approve",
    "compare",
    "detect",
    "questions_from_autopsies",
    "questions_from_backtest",
    "questions_from_calibration",
    "questions_from_objections",
    "rank",
    "ready_for_approval",
    "reject",
    "required_sigma",
    "submit_for_approval",
    "validate",
]
