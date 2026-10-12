"""Deterministic accounting issue evaluation; never posts ledger entries."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum


class DecisionStage(StrEnum):
    DETECT = "DETECT"
    INVESTIGATE = "INVESTIGATE"
    RESEARCH = "RESEARCH"
    EVALUATE = "EVALUATE"
    RECOMMEND = "RECOMMEND"
    RESOLVE = "RESOLVE"
    ESCALATE = "ESCALATE"


class Materiality(StrEnum):
    CRITICAL = "CRITICAL"
    REVIEW = "REVIEW"
    INFORMATIONAL = "INFORMATIONAL"


@dataclass(frozen=True, slots=True)
class MaterialityPolicy:
    """Caller-configured thresholds; qualitative flags can override amounts."""

    review_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None
    critical_flags: tuple[str, ...] = ()
    review_flags: tuple[str, ...] = ()
    informational_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.review_threshold is None) != (self.critical_threshold is None):
            raise ValueError("Configure both quantitative thresholds or neither.")
        if self.review_threshold is not None and (
            self.review_threshold < 0 or self.critical_threshold < self.review_threshold
        ):
            raise ValueError("Materiality thresholds must be non-negative and ordered.")


@dataclass(frozen=True, slots=True)
class AccountingIssue:
    issue_id: str
    description: str
    amount: Decimal | None = None
    evidence_refs: tuple[str, ...] = ()
    qualitative_flags: tuple[str, ...] = ()
    evidence_complete: bool = False
    recommended_treatment: str | None = None
    confidence: Decimal | None = None
    requires_estimate_or_judgment: bool = False


@dataclass(frozen=True, slots=True)
class DecisionRecommendation:
    issue_id: str
    stage: DecisionStage
    materiality: Materiality
    action: str
    recommended_treatment: str | None
    evidence_refs: tuple[str, ...]
    confidence: Decimal | None
    approval_required: bool
    question: str | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["stage"] = self.stage.value
        result["materiality"] = self.materiality.value
        result["confidence"] = str(self.confidence) if self.confidence is not None else None
        return result


def evaluate_issue(issue: AccountingIssue, policy: MaterialityPolicy) -> DecisionRecommendation:
    """Select the next workflow action without changing accounting data."""
    if not issue.issue_id.strip() or not issue.description.strip():
        raise ValueError("An issue ID and description are required.")
    if issue.confidence is not None and not Decimal(0) <= issue.confidence <= Decimal(1):
        raise ValueError("Confidence must be between 0 and 1.")

    flags = set(issue.qualitative_flags)
    amount = abs(issue.amount) if issue.amount is not None else None
    if flags.intersection(policy.critical_flags) or (
        amount is not None
        and policy.critical_threshold is not None
        and amount >= policy.critical_threshold
    ):
        materiality = Materiality.CRITICAL
    elif flags.intersection(policy.review_flags):
        materiality = Materiality.REVIEW
    elif flags.intersection(policy.informational_flags):
        materiality = Materiality.INFORMATIONAL
    elif (
        amount is not None
        and (
            policy.review_threshold is None
            or amount >= policy.review_threshold
        )
    ):
        materiality = Materiality.REVIEW
    else:
        materiality = Materiality.INFORMATIONAL

    approval_required = issue.requires_estimate_or_judgment
    if not issue.evidence_complete or not issue.evidence_refs:
        action = "ESCALATE" if materiality == Materiality.CRITICAL else "INVESTIGATE"
        stage = DecisionStage.ESCALATE if action == "ESCALATE" else DecisionStage.INVESTIGATE
        question = f"Provide evidence needed to resolve: {issue.description}"
    elif not issue.recommended_treatment:
        action = "RESEARCH"
        stage = DecisionStage.RESEARCH
        question = None
    elif approval_required:
        action = "REQUEST_APPROVAL"
        stage = DecisionStage.RECOMMEND
        question = f"Approve the proposed accounting estimate/treatment: {issue.recommended_treatment}?"
    else:
        action = "RESOLVE"
        stage = DecisionStage.RESOLVE
        question = None

    return DecisionRecommendation(
        issue_id=issue.issue_id,
        stage=stage,
        materiality=materiality,
        action=action,
        recommended_treatment=issue.recommended_treatment,
        evidence_refs=issue.evidence_refs,
        confidence=issue.confidence,
        approval_required=approval_required,
        question=question,
    )
