"""Jurisdiction-neutral readiness checks before a statutory filing workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class ReadinessStatus(StrEnum):
    READY = "READY"
    PARTIALLY_READY = "PARTIALLY READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class FormRequirement:
    form_id: str
    mandatory: bool = True
    available: bool = True


@dataclass(frozen=True, slots=True)
class FieldRequirement:
    field_id: str
    form_id: str
    value: Any = None
    mandatory: bool = True
    material: bool = True


@dataclass(frozen=True, slots=True)
class DisclosureRequirement:
    disclosure_id: str
    completed: bool
    mandatory: bool = True
    material: bool = True


@dataclass(frozen=True, slots=True)
class EvidenceRequirement:
    evidence_id: str
    status: str
    required: bool = True
    material: bool = True


@dataclass(frozen=True, slots=True)
class FilingQuestion:
    issue_id: str
    prompt: str
    material: bool = True
    previously_asked: bool = False


@dataclass(frozen=True, slots=True)
class PreFilingContext:
    jurisdiction: str | None
    reporting_category: str | None
    forms: tuple[FormRequirement, ...]
    fields: tuple[FieldRequirement, ...]
    profile_complete: bool = False
    profile_source: str | None = None
    disclosures: tuple[DisclosureRequirement, ...] = ()
    evidence: tuple[EvidenceRequirement, ...] = ()
    accounting_reconciled: bool = False
    material_accounting_issues: tuple[str, ...] = ()
    questions: tuple[FilingQuestion, ...] = ()


@dataclass(frozen=True, slots=True)
class PreFilingResult:
    status: ReadinessStatus
    jurisdiction: str | None
    reporting_category: str | None
    mandatory_forms: tuple[str, ...]
    required_fields: tuple[str, ...]
    mandatory_disclosures: tuple[str, ...]
    verified_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    incomplete_items: tuple[str, ...]
    questions: tuple[dict[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result


_VERIFIED_EVIDENCE_STATUSES = {"verified", "user_confirmed", "provided"}


def validate_pre_filing(context: PreFilingContext) -> PreFilingResult:
    """Evaluate a caller-supplied filing profile; this function infers no local rules."""
    blockers: list[str] = []
    incomplete: list[str] = []
    forms = tuple(form.form_id for form in context.forms if form.mandatory)
    required_fields = tuple(field.field_id for field in context.fields if field.mandatory)
    disclosures = tuple(item.disclosure_id for item in context.disclosures if item.mandatory)
    verified_evidence: list[str] = []

    if not context.jurisdiction:
        blockers.append("Reporting jurisdiction is not identified.")
    if not context.reporting_category:
        blockers.append("Reporting category is not identified.")
    if not context.profile_complete or not context.profile_source:
        blockers.append("The mandatory filing profile is not confirmed complete from an authoritative source.")
    if not forms:
        blockers.append("Mandatory forms are not configured for this filing profile.")

    for form in context.forms:
        if form.mandatory and not form.available:
            blockers.append(f"Mandatory form is unavailable: {form.form_id}.")
    for field in context.fields:
        missing_value = field.value is None or (
            isinstance(field.value, str) and not field.value.strip()
        )
        if field.mandatory and missing_value:
            target = blockers if field.material else incomplete
            target.append(f"Required field is missing: {field.field_id}.")
    for item in context.disclosures:
        if item.mandatory and not item.completed:
            target = blockers if item.material else incomplete
            target.append(f"Mandatory disclosure is incomplete: {item.disclosure_id}.")
    for item in context.evidence:
        if item.status in _VERIFIED_EVIDENCE_STATUSES:
            verified_evidence.append(item.evidence_id)
        elif item.required:
            target = blockers if item.material else incomplete
            target.append(f"Required supporting evidence is {item.status}: {item.evidence_id}.")
    if not context.accounting_reconciled:
        blockers.append("Accounting reconciliation has not passed.")
    # Only explicitly material accounting issues block; informational differences
    # remain visible through the caller's incomplete items and evidence registry.
    blockers.extend(context.material_accounting_issues)

    questions = tuple(
        {"issue_id": item.issue_id, "question": item.prompt}
        for item in context.questions
        if not item.previously_asked
    )
    for item in context.questions:
        target = blockers if item.material else incomplete
        target.append(f"Unresolved question: {item.issue_id}.")

    if blockers:
        status = ReadinessStatus.BLOCKED
    elif incomplete:
        status = ReadinessStatus.PARTIALLY_READY
    else:
        status = ReadinessStatus.READY
    return PreFilingResult(
        status=status,
        jurisdiction=context.jurisdiction,
        reporting_category=context.reporting_category,
        mandatory_forms=forms,
        required_fields=required_fields,
        mandatory_disclosures=disclosures,
        verified_evidence=tuple(verified_evidence),
        blockers=tuple(blockers),
        incomplete_items=tuple(incomplete),
        questions=questions,
    )
