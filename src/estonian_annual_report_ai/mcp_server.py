"""Local stdio MCP adapter over the document and accounting workflow."""

import os
import re
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field

from estonian_annual_report_ai.accounting import (
    Account,
    AccountingIssue,
    AccountType,
    JournalEntry,
    JournalLine,
    Ledger,
    MaterialityPolicy,
    balance_sheet,
    evaluate_issue,
    income_statement,
    trial_balance,
)
from estonian_annual_report_ai.accounting import (
    validate_annual_report as validate_ledger_annual_report,
)
from estonian_annual_report_ai.application import (
    AccountingWorkflow,
    DocumentInput,
    DocumentKind,
    validate_pre_filing_profile,
)
from estonian_annual_report_ai.evidence_registry import EvidenceRegistry
from estonian_annual_report_ai.reporting import (
    DisclosureRequirement,
    EvidenceRequirement,
    FieldRequirement,
    FilingQuestion,
    FormRequirement,
    PreFilingContext,
)
from estonian_annual_report_ai.transactions import (
    AIClassificationOptions,
    ClassificationCategory,
    OpenAIClassificationProvider,
    classify_with_ai,
)
from mcp.server import MCPServer

mcp = MCPServer("estonian-annual-report-ai")
_LOCK = threading.RLock()
_DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "estonian-annual-report-ai" / "data"
_workflow_instance: AccountingWorkflow | None = None


def _company_workspace(registry_code: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", registry_code):
        raise ValueError("Company registry code must contain only letters, digits, underscores, or hyphens.")
    data_dir = Path(os.environ.get("ESTONIAN_REPORT_DATA_DIR", _DEFAULT_DATA_DIR)).expanduser()
    return data_dir / "companies" / registry_code


def _configured_workflow() -> AccountingWorkflow:
    """Create one local-company workflow lazily from this process's environment."""
    global _workflow_instance
    with _LOCK:
        if _workflow_instance is None:
            name = os.environ.get("ESTONIAN_REPORT_COMPANY_NAME", "Example Company OÜ")
            registry_code = os.environ.get("ESTONIAN_REPORT_REGISTRY_CODE", "EXAMPLE")
            year = int(os.environ.get("ESTONIAN_REPORT_YEAR", str(date.today().year)))
            period_start = date.fromisoformat(os.environ.get("ESTONIAN_REPORT_PERIOD_START", f"{year}-01-01"))
            period_end = date.fromisoformat(os.environ.get("ESTONIAN_REPORT_PERIOD_END", f"{year}-12-31"))
            if period_end < period_start:
                raise ValueError("Reporting period end must not precede its start.")
            workspace = _company_workspace(registry_code)
            workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(workspace, 0o700)
            except OSError:
                pass
            _workflow_instance = AccountingWorkflow(
                company_name=name,
                registry_code=registry_code,
                period_start=period_start,
                period_end=period_end,
                rules_path=workspace / "classification-rules.json",
                evidence_registry_path=workspace / "evidence-registry.json",
            )
        return _workflow_instance


class DocumentInputModel(BaseModel):
    path: str
    kind: DocumentKind


class FXRateInput(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    rate_date: date
    eur_per_unit: str = Field(pattern=r"^\d+(?:\.\d+)?$")
    source_reference: str = Field(min_length=1, max_length=500)


class ClassificationApprovalInput(BaseModel):
    transaction_id: str
    category: ClassificationCategory


class PreviousReportApprovalInput(BaseModel):
    candidate_id: str


class AccountInput(BaseModel):
    code: str
    name: str
    type: AccountType


class JournalLineInput(BaseModel):
    account_code: str
    debit: str = Field(default="0", pattern=r"^\d+(?:\.\d{1,2})?$")
    credit: str = Field(default="0", pattern=r"^\d+(?:\.\d{1,2})?$")


class JournalEntryInput(BaseModel):
    entry_id: str
    entry_date: date
    description: str
    lines: list[JournalLineInput] = Field(min_length=2)
    source_reference: str | None = None
    is_closing_entry: bool = False


class EvidenceConfirmationInput(BaseModel):
    fact_id: str = Field(min_length=1, max_length=120)
    value: str | bool | int | float
    source: str = Field(default="user-confirmed", min_length=1, max_length=200)


class UnavailableEvidenceInput(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    alternative: str = Field(min_length=1, max_length=1000)


class EvidenceIssueInput(BaseModel):
    issue_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    material: bool = True
    required_evidence: str | None = Field(default=None, max_length=1000)
    alternative: str | None = Field(default=None, max_length=1000)
    question: str | None = Field(default=None, max_length=1000)


class FilingFormInput(BaseModel):
    form_id: str = Field(min_length=1, max_length=200)
    mandatory: bool = True
    available: bool = True


class FilingFieldInput(BaseModel):
    field_id: str = Field(min_length=1, max_length=200)
    form_id: str = Field(min_length=1, max_length=200)
    value: str | bool | int | float | None = None
    mandatory: bool = True
    material: bool = True


class FilingDisclosureInput(BaseModel):
    disclosure_id: str = Field(min_length=1, max_length=200)
    completed: bool
    mandatory: bool = True
    material: bool = True


class FilingEvidenceInput(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    status: str = Field(min_length=1, max_length=40)
    required: bool = True
    material: bool = True


class FilingQuestionInput(BaseModel):
    issue_id: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=1000)
    material: bool = True


class PreFilingValidationInput(BaseModel):
    company_registry_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    jurisdiction: str | None = Field(default=None, max_length=100)
    reporting_category: str | None = Field(default=None, max_length=200)
    profile_complete: bool = False
    profile_source: str | None = Field(default=None, max_length=1000)
    forms: list[FilingFormInput] = Field(default_factory=list, max_length=100)
    fields: list[FilingFieldInput] = Field(default_factory=list, max_length=1000)
    disclosures: list[FilingDisclosureInput] = Field(default_factory=list, max_length=500)
    evidence: list[FilingEvidenceInput] = Field(default_factory=list, max_length=1000)
    accounting_reconciled: bool = False
    material_accounting_issues: list[str] = Field(default_factory=list, max_length=500)
    questions: list[FilingQuestionInput] = Field(default_factory=list, max_length=100)


class ResolveEvidenceIssueInput(BaseModel):
    issue_id: str = Field(min_length=1, max_length=120)
    resolution: str = Field(min_length=1, max_length=1000)


class AccountingDecisionInput(BaseModel):
    company_registry_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    issue_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    amount: str | None = Field(default=None, pattern=r"^-?\d+(?:\.\d{1,6})?$")
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    qualitative_flags: list[str] = Field(default_factory=list, max_length=100)
    evidence_complete: bool = False
    recommended_treatment: str | None = Field(default=None, max_length=1000)
    confidence: str | None = Field(default=None, pattern=r"^(?:0(?:\.\d+)?|1(?:\.0+)?)$")
    requires_estimate_or_judgment: bool = False
    review_threshold: str | None = Field(default=None, pattern=r"^\d+(?:\.\d{1,6})?$")
    critical_threshold: str | None = Field(default=None, pattern=r"^\d+(?:\.\d{1,6})?$")
    critical_flags: list[str] = Field(default_factory=list, max_length=100)
    review_flags: list[str] = Field(default_factory=list, max_length=100)
    informational_flags: list[str] = Field(default_factory=list, max_length=100)


class AccountingDecisionApprovalInput(BaseModel):
    company_registry_code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    issue_id: str = Field(min_length=1, max_length=120)
    approved_by: str = Field(min_length=1, max_length=200)


def _registry_for_company(registry_code: str) -> EvidenceRegistry:
    """Use a private evidence registry scoped to the company identifier."""
    workflow = _configured_workflow()
    if registry_code == workflow.registry_code:
        return workflow.evidence_registry
    workspace = _company_workspace(registry_code)
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    return EvidenceRegistry(workspace / "evidence-registry.json")


@mcp.tool()
def generate_financial_statements(
    accounts: list[AccountInput],
    entries: list[JournalEntryInput],
    period_start: date,
    period_end: date,
) -> dict[str, object]:
    """Build trial balance, Balance Sheet, P&L, and validation from approved journal entries.

    Amounts are decimal strings in EUR. This tool performs no document parsing
    or transaction classification and keeps no data after the call.
    """
    ledger = Ledger(
        Account(item.code, item.name, item.type)
        for item in accounts
    )
    for entry in entries:
        ledger.add_entry(
            JournalEntry(
                entry_id=entry.entry_id,
                entry_date=entry.entry_date,
                description=entry.description,
                source_reference=entry.source_reference,
                is_closing_entry=entry.is_closing_entry,
                lines=tuple(
                    JournalLine(
                        account_code=line.account_code,
                        debit=Decimal(line.debit),
                        credit=Decimal(line.credit),
                    )
                    for line in entry.lines
                ),
            )
        )

    as_of = period_end
    trial = trial_balance(ledger, as_of)
    balance = balance_sheet(ledger, as_of, period_start)
    income = income_statement(ledger, period_start, period_end)
    validation = validate_ledger_annual_report(ledger, period_start, period_end)

    def statement_lines(lines: tuple[object, ...]) -> list[dict[str, str]]:
        return [
            {"account_code": line.account_code, "name": line.name, "amount": str(line.amount)}
            for line in lines
        ]

    def issue_lines(issues: tuple[object, ...]) -> list[dict[str, str]]:
        return [
            {"code": issue.code, "message": issue.message, "severity": issue.severity}
            for issue in issues
        ]

    return {
        "currency": "EUR",
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "trial_balance": {
            "total_debits": str(trial.total_debits),
            "total_credits": str(trial.total_credits),
        },
        "balance_sheet": {
            "assets": statement_lines(balance.assets),
            "liabilities": statement_lines(balance.liabilities),
            "equity": statement_lines(balance.equity),
            "total_assets": str(balance.total_assets),
            "total_liabilities": str(balance.total_liabilities),
            "total_equity": str(balance.total_equity),
            "is_balanced": balance.is_balanced,
        },
        "income_statement": {
            "revenue": statement_lines(income.revenue),
            "expenses": statement_lines(income.expenses),
            "total_revenue": str(income.total_revenue),
            "total_expenses": str(income.total_expenses),
            "net_profit": str(income.net_profit),
        },
        "validation": {
            "is_valid": validation.is_valid,
            "issues": issue_lines(validation.issues),
        },
        "warning": "This output is not an Estonian statutory annual report and is not ready to file.",
    }


@mcp.tool()
def import_documents(documents: list[DocumentInputModel]) -> dict[str, object]:
    """Import local bank statements, invoices, and prior annual reports by path."""
    with _LOCK:
        return _configured_workflow().import_documents(
            DocumentInput(Path(item.path), item.kind) for item in documents
        )


@mcp.tool()
def get_evidence_registry() -> dict[str, object]:
    """Read the local evidence inventory, confirmations, and unresolved issues."""
    with _LOCK:
        return _configured_workflow().get_evidence_registry()


@mcp.tool()
def record_user_confirmation(fact: EvidenceConfirmationInput) -> dict[str, object]:
    """Persist a user-confirmed accounting fact locally to avoid asking again."""
    with _LOCK:
        return _configured_workflow().record_user_confirmation(fact.fact_id, fact.value, source=fact.source)


@mcp.tool()
def mark_evidence_unavailable(evidence: UnavailableEvidenceInput) -> dict[str, object]:
    """Record that an export cannot be obtained and store a practical alternative."""
    with _LOCK:
        return _configured_workflow().mark_evidence_unavailable(
            evidence.evidence_id,
            description=evidence.description,
            alternative=evidence.alternative,
        )


@mcp.tool()
def get_pending_evidence_questions() -> dict[str, object]:
    """Return only open questions not already asked in an earlier session."""
    with _LOCK:
        return {"items": _configured_workflow().get_pending_evidence_questions()}


@mcp.tool()
def record_evidence_issue(issue: EvidenceIssueInput) -> dict[str, object]:
    """Persist an open material or non-material evidence issue locally."""
    with _LOCK:
        return _configured_workflow().record_evidence_issue(
            issue.issue_id,
            description=issue.description,
            material=issue.material,
            required_evidence=issue.required_evidence,
            alternative=issue.alternative,
            question=issue.question,
        )


@mcp.tool()
def mark_evidence_question_asked(issue_id: str) -> dict[str, str]:
    """Remember that an open evidence question has already been shown to the user."""
    with _LOCK:
        _configured_workflow().mark_evidence_question_asked(issue_id)
        return {"issue_id": issue_id, "status": "asked"}


@mcp.tool()
def resolve_evidence_issue(issue: ResolveEvidenceIssueInput) -> dict[str, object]:
    """Close a persisted evidence issue without deleting its history."""
    with _LOCK:
        return _configured_workflow().resolve_evidence_issue(issue.issue_id, resolution=issue.resolution)


@mcp.tool()
def evaluate_accounting_issue(issue: AccountingDecisionInput) -> dict[str, object]:
    """Evaluate materiality and recommend an action; never create journal entries."""
    with _LOCK:
        decision = evaluate_issue(
            AccountingIssue(
                issue_id=f"{issue.company_registry_code}:{issue.issue_id}",
                description=issue.description,
                amount=Decimal(issue.amount) if issue.amount is not None else None,
                evidence_refs=tuple(issue.evidence_refs),
                qualitative_flags=tuple(issue.qualitative_flags),
                evidence_complete=issue.evidence_complete,
                recommended_treatment=issue.recommended_treatment,
                confidence=Decimal(issue.confidence) if issue.confidence is not None else None,
                requires_estimate_or_judgment=issue.requires_estimate_or_judgment,
            ),
            MaterialityPolicy(
                review_threshold=Decimal(issue.review_threshold) if issue.review_threshold is not None else None,
                critical_threshold=Decimal(issue.critical_threshold) if issue.critical_threshold is not None else None,
                critical_flags=tuple(issue.critical_flags),
                review_flags=tuple(issue.review_flags),
                informational_flags=tuple(issue.informational_flags),
            ),
        )
        _registry_for_company(issue.company_registry_code).record_decision(
            decision.issue_id, decision.to_dict()
        )
        return decision.to_dict()


@mcp.tool()
def approve_accounting_recommendation(approval: AccountingDecisionApprovalInput) -> dict[str, object]:
    """Record approval for a judgmental recommendation; posting remains separate."""
    with _LOCK:
        decision_id = f"{approval.company_registry_code}:{approval.issue_id}"
        return _registry_for_company(approval.company_registry_code).approve_decision(
            decision_id, approved_by=approval.approved_by
        )


@mcp.tool()
def detect_stripe_reports(path: str | None = None) -> dict[str, object]:
    """Identify local Stripe report types without exposing row-level financial data."""
    with _LOCK:
        return _configured_workflow().detect_stripe_reports(Path(path) if path else None)


@mcp.tool()
def get_missing_stripe_reports() -> dict[str, object]:
    """Check imported Stripe reports for required report types, period, and currency."""
    with _LOCK:
        return _configured_workflow().get_missing_stripe_reports()


@mcp.tool()
def generate_stripe_download_instructions(report_type: str | None = None) -> dict[str, object]:
    """Give Dashboard export steps for each missing or unverified Stripe report."""
    with _LOCK:
        return _configured_workflow().generate_stripe_download_instructions(report_type)


@mcp.tool()
def import_stripe_report(path: str = Field(min_length=1, max_length=4096)) -> dict[str, object]:
    """Import one local Stripe CSV, auto-detect its report type, and skip exact file duplicates."""
    with _LOCK:
        return _configured_workflow().import_stripe_report(Path(path))


@mcp.tool()
def reconcile_stripe_balances() -> dict[str, object]:
    """Check the Stripe balance roll-forward and compare fee totals without classifying revenue."""
    with _LOCK:
        return _configured_workflow().reconcile_stripe_balances()


@mcp.tool()
def reconcile_stripe_fees() -> dict[str, object]:
    """Compare the imported Fees report with Balance Summary fees exactly once."""
    with _LOCK:
        return _configured_workflow().reconcile_stripe_fees()


@mcp.tool()
def reconcile_stripe_activity() -> dict[str, object]:
    """Reconcile itemized charges, refunds, disputes, and payouts against summary totals."""
    with _LOCK:
        return _configured_workflow().reconcile_stripe_activity()


@mcp.tool()
def match_stripe_bank_transfers() -> dict[str, object]:
    """Separate bank receipts from Stripe and payments to Stripe; expose net and directional matches."""
    with _LOCK:
        return _configured_workflow().match_stripe_bank_transfers()


@mcp.tool()
def get_stripe_reconciliation_status() -> dict[str, object]:
    """Return report coverage, balance/fee checks, and Stripe-to-bank matching status."""
    with _LOCK:
        return _configured_workflow().get_stripe_reconciliation_status()


@mcp.tool()
def parse_bank_statements() -> dict[str, object]:
    """Return local statement import summaries and parser issue codes."""
    with _LOCK:
        return {
            "statements": [
                {
                    "source": statement.source.filename,
                    "source_sha256": statement.source.sha256,
                    "transaction_count": len(statement.transactions),
                    "currency": statement.currency,
                    "bank_account_id": statement.bank_account_id,
                    "opening_balance": str(statement.opening_balance) if statement.opening_balance is not None else None,
                    "closing_balance": str(statement.closing_balance) if statement.closing_balance is not None else None,
                    "issues": [issue.code for issue in statement.issues],
                }
                for statement in _configured_workflow().parse_bank_statements()
            ]
        }


@mcp.tool()
def normalize_transactions(rates: list[FXRateInput] | None = None) -> dict[str, object]:
    """Normalize imported bank rows; foreign currency needs a dated, sourced FX rate."""
    fx_rates = {}
    for item in rates or []:
        rate = Decimal(item.eur_per_unit)
        if not rate.is_finite() or rate <= 0:
            raise ValueError("FX rates must be finite positive EUR-per-unit values.")
        key = (item.currency.upper(), item.rate_date)
        if key in fx_rates:
            raise ValueError("Provide at most one FX rate per currency and date.")
        fx_rates[key] = (rate, item.source_reference)
    with _LOCK:
        transactions = _configured_workflow().normalize_transactions(fx_rates=fx_rates)
        return {"transaction_count": len(transactions), "normalized": True}


@mcp.tool()
def classify_transactions(
    use_ai: bool = False,
    confirm_external_processing: bool = False,
    ai_model: str | None = None,
) -> dict[str, object]:
    """Classify locally first; AI proposals require explicit external-processing consent."""
    with _LOCK:
        transactions = _configured_workflow().classify_transactions()
        if use_ai:
            if not confirm_external_processing:
                raise ValueError("Confirm external processing before enabling AI classification.")
            if not ai_model:
                raise ValueError("Set ai_model explicitly to enable AI classification.")
            provider = OpenAIClassificationProvider(model=ai_model)
            transactions = classify_with_ai(
                transactions,
                provider=provider,
                options=AIClassificationOptions(
                    enabled=True,
                    confirm_external_processing=True,
                ),
            )
            _configured_workflow().transactions = transactions
        return {
            "transactions": [
                {
                    "transaction_id": row.transaction_id,
                    "date": row.transaction_date.isoformat() if row.transaction_date else None,
                    "amount": str(row.amount) if row.amount is not None else None,
                    "currency": row.currency,
                    "counterparty": row.counterparty,
                    "description": row.description,
                    "category": row.classification.category.value,
                    "confidence": str(row.classification.confidence),
                    "status": row.classification.status,
                    "reasons": list(row.classification.reasons),
                    "possible_duplicate_of": row.possible_duplicate_of,
                    "possible_transfer_id": row.internal_transfer_id,
                    "source": {
                        "filename": row.source.filename,
                        "sha256": row.source.sha256,
                        "page": row.source.page_number,
                        "row": row.source.row_number,
                        "sheet": row.source.sheet_name,
                    },
                }
                for row in transactions
            ]
        }


@mcp.tool()
def get_unclassified_transactions() -> dict[str, object]:
    """List pending or ambiguous transactions with source references."""
    with _LOCK:
        return {
            "transactions": [
                {
                    "transaction_id": row.transaction_id,
                    "date": row.transaction_date.isoformat() if row.transaction_date else None,
                    "amount": str(row.amount) if row.amount is not None else None,
                    "currency": row.currency,
                    "counterparty": row.counterparty,
                    "description": row.description,
                    "status": row.classification.status,
                    "possible_duplicate_of": row.possible_duplicate_of,
                    "possible_transfer_id": row.internal_transfer_id,
                    "source": {"filename": row.source.filename, "sha256": row.source.sha256,
                               "page": row.source.page_number, "row": row.source.row_number,
                               "sheet": row.source.sheet_name},
                }
                for row in _configured_workflow().unclassified_transactions()
            ]
        }


@mcp.tool()
def approve_classification(approval: ClassificationApprovalInput) -> dict[str, str]:
    """Approve a category and persist its exact local matching rule."""
    with _LOCK:
        row = _configured_workflow().approve_classification(approval.transaction_id, approval.category)
        return {"transaction_id": row.transaction_id, "category": row.classification.category.value,
                "status": row.classification.status}


@mcp.tool()
def get_previous_report_candidates() -> dict[str, object]:
    """List prior-report numeric candidates with source locations; values are unverified."""
    with _LOCK:
        return {
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "field": item.field,
                    "value": str(item.value),
                    "currency": item.currency,
                    "fiscal_year": item.fiscal_year,
                    "raw_label": item.raw_label,
                    "filename": item.source.filename,
                    "source_sha256": item.source.sha256,
                    "page": item.source.page_number,
                    "row": item.source.row_number,
                }
                for candidate_id, item in _configured_workflow().report_candidates()
            ],
            "approval_required": True,
        }


@mcp.tool()
def approve_previous_report_value(approval: PreviousReportApprovalInput) -> dict[str, object]:
    """User-confirm one extracted prior-report value without deleting alternatives."""
    with _LOCK:
        result = _configured_workflow().approve_report_candidate(approval.candidate_id)
        return {"field": result.field, "fiscal_year": result.fiscal_year,
                "value": str(result.value), "source_id": result.source_id,
                "candidate_id": result.candidate_id, "status": "user_confirmed"}


@mcp.tool()
def get_fixed_asset_history() -> dict[str, object]:
    """Read extracted fixed-asset figures and depreciation policies from imported reports."""
    with _LOCK:
        return _configured_workflow().fixed_asset_history()


@mcp.tool()
def reconcile_accounts() -> dict[str, object]:
    """Reconcile imported statements using opening balance plus signed movements."""
    with _LOCK:
        return {
            "accounts": [
                {
                    "bank_account_id": item.bank_account_id,
                    "currency": item.currency,
                    "opening_balance": str(item.opening_balance) if item.opening_balance is not None else None,
                    "calculated_closing_balance": str(item.calculated_closing_balance) if item.calculated_closing_balance is not None else None,
                    "reported_closing_balance": str(item.reported_closing_balance) if item.reported_closing_balance is not None else None,
                    "difference": str(item.difference) if item.difference is not None else None,
                    "status": item.status,
                    "issues": list(item.issues),
                }
                for item in _configured_workflow().reconcile_accounts()
            ]
        }


@mcp.tool()
def validate_annual_report() -> dict[str, object]:
    """Validate accounting, opening balances, source completeness, and bank reconciliation."""
    with _LOCK:
        return _configured_workflow().validate_annual_report()


@mcp.tool()
def validate_pre_filing(profile: PreFilingValidationInput) -> dict[str, object]:
    """Check configured filing requirements before opening a government portal."""
    with _LOCK:
        context = PreFilingContext(
            jurisdiction=profile.jurisdiction,
            reporting_category=profile.reporting_category,
            profile_complete=profile.profile_complete,
            profile_source=profile.profile_source,
            forms=tuple(FormRequirement(**item.model_dump()) for item in profile.forms),
            fields=tuple(FieldRequirement(**item.model_dump()) for item in profile.fields),
            disclosures=tuple(DisclosureRequirement(**item.model_dump()) for item in profile.disclosures),
            evidence=tuple(EvidenceRequirement(**item.model_dump()) for item in profile.evidence),
            accounting_reconciled=profile.accounting_reconciled,
            material_accounting_issues=tuple(profile.material_accounting_issues),
            questions=tuple(FilingQuestion(**item.model_dump()) for item in profile.questions),
        )
        if profile.company_registry_code == _configured_workflow().registry_code:
            registry = _configured_workflow().evidence_registry
        else:
            registry = _registry_for_company(profile.company_registry_code)
        return validate_pre_filing_profile(context, registry)


@mcp.tool()
def get_missing_information() -> dict[str, object]:
    """List unresolved documents, prior-year values, classifications, and reconciliation items."""
    with _LOCK:
        return {"items": list(_configured_workflow().get_missing_information())}


@mcp.tool()
def generate_annual_report() -> dict[str, object]:
    """Generate review-only local JSON/XLSX/PDF drafts; critical issues block statements."""
    with _LOCK:
        workflow = _configured_workflow()
        report_output_dir = _company_workspace(workflow.registry_code) / "reports" / str(workflow.period_end.year)
        report_output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(report_output_dir.parent, 0o700)
        os.chmod(report_output_dir, 0o700)
        draft, paths = workflow.generate_annual_report(report_output_dir)
        for path in (paths or {}).values():
            os.chmod(path, 0o600)
        return {
            "status": draft.status,
            "figure_status": draft.figure_status,
            "files": paths,
            "missing_information": list(draft.missing_information),
            "critical_issues": [
                {"code": item.code, "message": item.message}
                for item in draft.reconciliation_issues
                if item.severity in ("critical", "error")
            ],
        }


def main() -> None:
    """Run the configured local stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
