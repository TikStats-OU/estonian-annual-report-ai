"""Local document-to-report workflow built over the deterministic ledger."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from estonian_annual_report_ai.accounting import (
    Account,
    AccountingIssue,
    AccountType,
    DecisionRecommendation,
    JournalEntry,
    JournalLine,
    Ledger,
    MaterialityPolicy,
    evaluate_issue,
)
from estonian_annual_report_ai.accounting.money import to_minor_units
from estonian_annual_report_ai.documents import (
    AnnualReportExtraction,
    ExtractedCandidate,
    InvoiceExtraction,
    ParsedBankStatement,
    extract_annual_report,
    find_financial_discrepancies,
    parse_bank_statement,
    parse_invoice,
)
from estonian_annual_report_ai.evidence_registry import EvidenceRegistry
from estonian_annual_report_ai.reporting import (
    DEFAULT_MISSING_INFORMATION,
    AnnualReportDraft,
    DraftIssue,
    FilingQuestion,
    PreFilingContext,
    build_annual_report_draft,
    export_annual_report_draft,
)
from estonian_annual_report_ai.reporting import (
    validate_pre_filing as evaluate_pre_filing,
)
from estonian_annual_report_ai.stripe_assistant import StripeReportingAssistant
from estonian_annual_report_ai.transactions import (
    ClassificationCategory,
    ClassificationRuleStore,
    NormalizedTransaction,
    ReconciliationResult,
    build_ledger,
    classify_transactions,
    normalize_transactions,
    reconcile_statements,
)


class DocumentKind(StrEnum):
    BANK_STATEMENT = "bank_statement"
    INVOICE = "invoice"
    ANNUAL_REPORT = "annual_report"
    STRIPE_REPORT = "stripe_report"


@dataclass(frozen=True, slots=True)
class DocumentInput:
    path: Path
    kind: DocumentKind


@dataclass(frozen=True, slots=True)
class CandidateApproval:
    field: str
    fiscal_year: int
    value: Decimal
    source_id: str
    candidate_id: str


def validate_pre_filing_profile(
    context: PreFilingContext, evidence_registry: EvidenceRegistry
) -> dict[str, object]:
    """Evaluate readiness and persist unresolved questions for one company registry."""
    registry_issues = evidence_registry.snapshot().get("issues", {})
    questions = []
    for question in context.questions:
        existing = registry_issues.get(question.issue_id, {})
        if existing.get("status") == "resolved":
            continue
        evidence_registry.record_issue(
            question.issue_id,
            description=question.prompt,
            status="open",
            material=question.material,
            question=question.prompt,
        )
        questions.append(FilingQuestion(
            issue_id=question.issue_id,
            prompt=question.prompt,
            material=question.material,
            previously_asked=bool(existing.get("last_asked_at")),
        ))
    result = evaluate_pre_filing(PreFilingContext(
        jurisdiction=context.jurisdiction,
        reporting_category=context.reporting_category,
        forms=context.forms,
        fields=context.fields,
        profile_complete=context.profile_complete,
        profile_source=context.profile_source,
        disclosures=context.disclosures,
        evidence=context.evidence,
        accounting_reconciled=context.accounting_reconciled,
        material_accounting_issues=context.material_accounting_issues,
        questions=tuple(questions),
    ))
    for question in result.questions:
        evidence_registry.mark_question_asked(question["issue_id"])
    return result.to_dict()


class AccountingWorkflow:
    """One local session; source values remain traceable to their original files."""

    def __init__(
        self,
        *,
        company_name: str,
        registry_code: str,
        period_start: date,
        period_end: date,
        rules_path: Path,
        evidence_registry_path: Path | None = None,
    ) -> None:
        self.company_name = company_name
        self.registry_code = registry_code
        self.period_start = period_start
        self.period_end = period_end
        self.rule_store = ClassificationRuleStore(rules_path)
        self.evidence_registry = EvidenceRegistry(
            evidence_registry_path or rules_path.with_name("evidence-registry.json")
        )
        self.bank_statements: list[ParsedBankStatement] = []
        self.invoices: list[InvoiceExtraction] = []
        self.annual_reports: list[AnnualReportExtraction] = []
        self.import_errors: list[str] = []
        self.transactions: tuple[NormalizedTransaction, ...] = ()
        self.approved_report_values: dict[tuple[str, int], CandidateApproval] = {}
        self.latest_ledger: Ledger | None = None
        self.latest_ledger_issues: tuple[str, ...] = ()
        self.stripe_assistant = StripeReportingAssistant(
            period_start=period_start,
            period_end=period_end,
        )

    @property
    def reporting_year(self) -> int:
        return self.period_end.year

    @property
    def prior_year(self) -> int:
        """Year whose closing balances open this reporting period."""
        return self.period_start.year - 1

    def import_documents(self, documents: Iterable[DocumentInput]) -> dict[str, int | list[str]]:
        counts = {"bank_statements": 0, "invoices": 0, "annual_reports": 0, "stripe_reports": 0}
        seen = {
            item.source.sha256
            for collection in (self.bank_statements, self.invoices, self.annual_reports)
            for item in collection
        }
        seen.update(report.source.sha256 for report in self.stripe_assistant.reports)
        for document in documents:
            try:
                if document.kind == DocumentKind.BANK_STATEMENT:
                    parsed = parse_bank_statement(document.path)
                    if parsed.source.sha256 in seen:
                        self.import_errors.append(f"Duplicate source document skipped: {parsed.source.filename}.")
                        continue
                    seen.add(parsed.source.sha256)
                    self.bank_statements.append(parsed)
                    self.evidence_registry.record_document(
                        document_id=parsed.source.sha256,
                        filename=parsed.source.filename,
                        document_type=document.kind.value,
                        period=f"{self.period_start.isoformat()}/{self.period_end.isoformat()}",
                        status="provided",
                        sha256=parsed.source.sha256,
                        transaction_count=len(parsed.transactions),
                        processing_status="parsed",
                    )
                    counts["bank_statements"] += 1
                elif document.kind == DocumentKind.INVOICE:
                    parsed_invoice = parse_invoice(document.path)
                    if parsed_invoice.source.sha256 in seen:
                        self.import_errors.append(f"Duplicate source document skipped: {parsed_invoice.source.filename}.")
                        continue
                    seen.add(parsed_invoice.source.sha256)
                    self.invoices.append(parsed_invoice)
                    self.evidence_registry.record_document(
                        document_id=parsed_invoice.source.sha256,
                        filename=parsed_invoice.source.filename,
                        document_type=document.kind.value,
                        period=None,
                        status="provided",
                        sha256=parsed_invoice.source.sha256,
                        transaction_count=1,
                        processing_status="parsed",
                    )
                    counts["invoices"] += 1
                elif document.kind == DocumentKind.ANNUAL_REPORT:
                    parsed_report = extract_annual_report(document.path)
                    if parsed_report.source.sha256 in seen:
                        self.import_errors.append(f"Duplicate source document skipped: {parsed_report.source.filename}.")
                        continue
                    seen.add(parsed_report.source.sha256)
                    self.annual_reports.append(parsed_report)
                    self.evidence_registry.record_document(
                        document_id=parsed_report.source.sha256,
                        filename=parsed_report.source.filename,
                        document_type=document.kind.value,
                        period=None,
                        status="provided",
                        sha256=parsed_report.source.sha256,
                        transaction_count=len(parsed_report.candidates),
                        processing_status="parsed",
                        details={
                            "fixed_asset_values": [
                                {
                                    "field": item.field,
                                    "value": str(item.value),
                                    "currency": item.currency,
                                    "fiscal_year": item.fiscal_year,
                                    "page": item.source.page_number,
                                    "row": item.source.row_number,
                                }
                                for item in parsed_report.fixed_asset_candidates
                            ],
                            "fixed_asset_policies": [
                                {
                                    "field": item.field,
                                    "text": str(item.value),
                                    "page": item.source.page_number,
                                    "row": item.source.row_number,
                                }
                                for item in parsed_report.fixed_asset_policies
                            ],
                        },
                    )
                    counts["annual_reports"] += 1
                elif document.kind == DocumentKind.STRIPE_REPORT:
                    detected = self.stripe_assistant.detect_report(document.path)
                    if detected["sha256"] in seen:
                        self.import_errors.append(f"Duplicate source document skipped: {document.path.name}.")
                        continue
                    parsed_stripe = self.stripe_assistant.import_report(document.path)
                    if parsed_stripe["status"] == "imported":
                        counts["stripe_reports"] += 1
                        seen.add(parsed_stripe["sha256"])
                        self.evidence_registry.record_document(
                            document_id=parsed_stripe["sha256"],
                            filename=document.path.name,
                            document_type=document.kind.value,
                            period=f"{self.period_start.isoformat()}/{self.period_end.isoformat()}",
                            status="provided",
                            sha256=parsed_stripe["sha256"],
                            transaction_count=parsed_stripe.get("row_count"),
                            processing_status="parsed",
                        )
            except (OSError, ValueError, RuntimeError) as exc:
                # Parsers expose generic errors; never include extracted content in the error.
                self.import_errors.append(f"Could not import {document.path.name}: {type(exc).__name__}.")
        return {**counts, "errors": list(self.import_errors)}

    def record_user_confirmation(self, fact_id: str, value: object, *, source: str = "user-confirmed") -> dict[str, object]:
        return self.evidence_registry.record_fact(fact_id, value, status="user_confirmed", source=source)

    def mark_evidence_unavailable(self, evidence_id: str, *, description: str, alternative: str) -> dict[str, object]:
        return self.evidence_registry.mark_unavailable(
            evidence_id, description=description, alternative=alternative
        )

    def record_evidence_issue(self, issue_id: str, **details: object) -> dict[str, object]:
        return self.evidence_registry.record_issue(issue_id, **details)

    def mark_evidence_question_asked(self, issue_id: str) -> None:
        self.evidence_registry.mark_question_asked(issue_id)

    def resolve_evidence_issue(self, issue_id: str, *, resolution: str) -> dict[str, object]:
        return self.evidence_registry.resolve_issue(issue_id, resolution=resolution)

    def get_evidence_registry(self) -> dict[str, object]:
        return self.evidence_registry.snapshot()

    def get_pending_evidence_questions(self) -> list[dict[str, object]]:
        return self.evidence_registry.pending_questions()

    def evaluate_accounting_issue(
        self, issue: AccountingIssue, policy: MaterialityPolicy
    ) -> DecisionRecommendation:
        """Persist a sourced recommendation; this method never posts a journal entry."""
        recommendation = evaluate_issue(issue, policy)
        self.evidence_registry.record_decision(issue.issue_id, recommendation.to_dict())
        return recommendation

    def approve_accounting_recommendation(self, issue_id: str, *, approved_by: str) -> dict[str, object]:
        """Record approval for a judgmental recommendation without posting it."""
        return self.evidence_registry.approve_decision(issue_id, approved_by=approved_by)

    def validate_pre_filing(self, context: PreFilingContext) -> dict[str, object]:
        """Run the configured filing gate and persist questions to prevent repeats."""
        return validate_pre_filing_profile(context, self.evidence_registry)

    def parse_bank_statements(self) -> tuple[ParsedBankStatement, ...]:
        return tuple(self.bank_statements)

    def normalize_transactions(
        self,
        *,
        fx_rates: dict[tuple[str, date], tuple[Decimal, str]] | None = None,
    ) -> tuple[NormalizedTransaction, ...]:
        self.transactions = normalize_transactions(self.bank_statements, fx_rates=fx_rates)
        return self.transactions

    def classify_transactions(self) -> tuple[NormalizedTransaction, ...]:
        self.transactions = classify_transactions(self.transactions, rule_store=self.rule_store)
        return self.transactions

    def approve_classification(
        self, transaction_id: str, category: ClassificationCategory
    ) -> NormalizedTransaction:
        for index, transaction in enumerate(self.transactions):
            if transaction.transaction_id == transaction_id:
                self.rule_store.approve(transaction, category)
                approved = classify_transactions((transaction,), rule_store=self.rule_store)[0]
                self.transactions = (*self.transactions[:index], approved, *self.transactions[index + 1 :])
                return approved
        raise ValueError("Transaction ID was not found in the current local workflow.")

    def unclassified_transactions(self) -> tuple[NormalizedTransaction, ...]:
        return tuple(
            row for row in self.transactions
            if row.classification.category == ClassificationCategory.UNCLASSIFIED
            or row.classification.status == "needs_review"
            or row.classification.status == "ai_proposed"
        )

    def reconcile_accounts(self) -> tuple[ReconciliationResult, ...]:
        return reconcile_statements(self.bank_statements)

    def import_stripe_report(self, path: str | Path) -> dict[str, object]:
        """Import a local Stripe CSV and retain its original source references."""
        return self.stripe_assistant.import_report(path)

    def detect_stripe_reports(self, path: str | Path | None = None) -> dict[str, object]:
        """Detect report kinds in a local file or list previously imported reports."""
        return self.stripe_assistant.detect_report(path) if path is not None else self.stripe_assistant.detected_reports()

    def get_missing_stripe_reports(self) -> dict[str, object]:
        return self.stripe_assistant.missing_reports()

    def generate_stripe_download_instructions(self, report_type: str | None = None) -> dict[str, object]:
        return self.stripe_assistant.download_instructions(report_type)

    def reconcile_stripe_balances(self) -> dict[str, object]:
        return self.stripe_assistant.reconcile_balances()

    def reconcile_stripe_fees(self) -> dict[str, object]:
        return self.stripe_assistant.reconcile_fees()

    def reconcile_stripe_activity(self) -> dict[str, object]:
        return self.stripe_assistant.reconcile_activity()

    def match_stripe_bank_transfers(self) -> dict[str, object]:
        transactions = self.transactions or self.normalize_transactions()
        return self.stripe_assistant.match_bank_transfers(transactions)

    def get_stripe_reconciliation_status(self) -> dict[str, object]:
        transactions = self.transactions or self.normalize_transactions()
        return self.stripe_assistant.reconciliation_status(transactions)

    def report_candidates(self) -> tuple[tuple[str, ExtractedCandidate], ...]:
        candidates = []
        for report in self.annual_reports:
            for item in report.candidates:
                identifier = _candidate_id(item)
                candidates.append((identifier, item))
        return tuple(candidates)

    def fixed_asset_history(self) -> dict[str, object]:
        """Return extracted fixed-asset values and policy text with source locations."""
        return {
            "reports": [
                {
                    "source": {
                        "filename": report.source.filename,
                        "sha256": report.source.sha256,
                    },
                    "values": [
                        {
                            "field": item.field,
                            "value": str(item.value),
                            "currency": item.currency,
                            "fiscal_year": item.fiscal_year,
                            "label": item.raw_label,
                            "page": item.source.page_number,
                            "row": item.source.row_number,
                        }
                        for item in report.fixed_asset_candidates
                    ],
                    "policies": [
                        {
                            "field": item.field,
                            "text": str(item.value),
                            "page": item.source.page_number,
                            "row": item.source.row_number,
                        }
                        for item in report.fixed_asset_policies
                    ],
                }
                for report in self.annual_reports
            ]
        }

    def approve_report_candidate(self, candidate_id: str) -> CandidateApproval:
        for identifier, candidate in self.report_candidates():
            if identifier == candidate_id:
                if not isinstance(candidate.value, Decimal) or candidate.fiscal_year is None:
                    raise ValueError("Only numeric candidates with a detected fiscal year can be approved.")
                approval = CandidateApproval(
                    field=candidate.field,
                    fiscal_year=candidate.fiscal_year,
                    value=candidate.value,
                    source_id=candidate.source.sha256,
                    candidate_id=identifier,
                )
                self.approved_report_values[(candidate.field, candidate.fiscal_year)] = approval
                return approval
        raise ValueError("Extracted candidate ID was not found.")

    def historical_discrepancies(self):
        return find_financial_discrepancies(self.annual_reports)

    def get_missing_information(self) -> tuple[str, ...]:
        missing: list[str] = []
        year, prior_year = self.reporting_year, self.prior_year
        extracted_years = {
            candidate.fiscal_year for report in self.annual_reports for candidate in report.candidates
        }
        if not self.bank_statements:
            missing.append(f"Import complete bank statements for all bank accounts covering the {year} reporting period.")
        if not self.invoices:
            missing.append(f"Import {year} revenue and expense invoices, including unpaid and accrual documents.")
        if prior_year not in extracted_years:
            missing.append(f"Import the {prior_year} annual report to establish the {year} opening balances.")
        for field in ("assets", "liabilities", "equity"):
            if (field, prior_year) not in self.approved_report_values:
                missing.append(
                    f"Review and approve the {prior_year} closing {field} value before using it as the {year} opening balance."
                )
        unclassified = self.unclassified_transactions()
        if unclassified:
            missing.append(f"Classify or confirm {len(unclassified)} transaction(s) before ledger posting.")
        for result in self.reconcile_accounts():
            if result.status != "reconciled":
                missing.extend(f"Resolve bank reconciliation: {issue}" for issue in result.issues)
        stripe_used = bool(self.stripe_assistant.reports) or any(
            "stripe" in " ".join((row.counterparty or "", row.description or "")).casefold()
            for statement in self.bank_statements
            for row in statement.transactions
        )
        if stripe_used:
            missing.extend(
                f"Stripe report {item['report_type']}: {item['status']}"
                for item in self.stripe_assistant.missing_reports()["missing_or_unverified"]
            )
        missing.extend(self.import_errors)
        for discrepancy in self.historical_discrepancies():
            if (discrepancy.field, discrepancy.fiscal_year) not in self.approved_report_values:
                missing.append(
                    f"Resolve conflicting {discrepancy.field} values for {discrepancy.fiscal_year}; "
                    "all extracted source values are preserved."
                )
        return tuple(dict.fromkeys(missing))

    def get_advisory_information(self) -> tuple[str, ...]:
        """Non-blocking suggestions; a young company may have no earlier reports."""
        extracted_years = {
            candidate.fiscal_year for report in self.annual_reports for candidate in report.candidates
        }
        return tuple(
            f"If the company filed a {year} annual report, add it for historical consistency checks."
            for year in (self.prior_year - 2, self.prior_year - 1)
            if year not in extracted_years
        )

    def build_ledger(self) -> tuple[Ledger, tuple[str, ...]]:
        ledger, issues = build_ledger(
            self.transactions,
            period_start=self.period_start,
            period_end=self.period_end,
        )
        mutable_issues = list(issues)
        values = {
            field: self.approved_report_values.get((field, self.prior_year))
            for field in ("assets", "liabilities", "equity")
        }
        if all(values.values()):
            assets = values["assets"].value
            liabilities = values["liabilities"].value
            equity = values["equity"].value
            if to_minor_units(assets) != to_minor_units(liabilities) + to_minor_units(equity):
                mutable_issues.append(f"Approved {self.prior_year} closing assets do not equal liabilities plus equity.")
            elif assets < 0 or liabilities < 0:
                mutable_issues.append(f"{self.prior_year} opening balance totals contain unsupported negative asset/liability values.")
            elif not any((assets, liabilities, equity)):
                pass
            else:
                # The importer only yields aggregate totals. Keep them in explicitly named control accounts.
                opening_lines = []
                if assets > 0:
                    opening_lines.append(JournalLine("1100", debit=assets))
                if liabilities:
                    opening_lines.append(JournalLine("2100", credit=liabilities))
                if equity > 0:
                    opening_lines.append(JournalLine("3100", credit=equity))
                elif equity < 0:
                    opening_lines.append(JournalLine("3100", debit=abs(equity)))
                ledger_with_controls = Ledger((*ledger.accounts, *(account for account in (
                    Account("1100", f"{self.prior_year} closing assets — aggregate control", AccountType.ASSET),
                    Account("2100", f"{self.prior_year} closing liabilities — aggregate control", AccountType.LIABILITY),
                    Account("3100", f"{self.prior_year} closing equity — aggregate control", AccountType.EQUITY),
                ))))
                # Rebuild ledger to include only the known, already approved transactions.
                for entry in ledger.entries:
                    ledger_with_controls.add_entry(entry)
                ledger_with_controls.add_entry(JournalEntry(
                    entry_id=f"{self.prior_year}-closing-opening-{self.reporting_year}",
                    entry_date=self.period_start - timedelta(days=1),
                    description=f"User-approved aggregate {self.prior_year} closing balances",
                    source_reference=":".join(values[key].source_id for key in ("assets", "liabilities", "equity")),
                    lines=tuple(opening_lines),
                ))
                ledger = ledger_with_controls
                mutable_issues.append(
                    f"Prior-year values are aggregate control totals; account-level {self.prior_year} closing breakdown is still required."
                )
        else:
            mutable_issues.append(
                f"User-confirmed {self.prior_year} closing assets, liabilities, and equity are required for opening balances."
            )
        self.latest_ledger = ledger
        self.latest_ledger_issues = tuple(mutable_issues)
        return ledger, tuple(mutable_issues)

    def validate_annual_report(self) -> dict[str, object]:
        from estonian_annual_report_ai.accounting import validate_annual_report

        ledger, ledger_issues = self.build_ledger()
        reconciliation = self.reconcile_accounts()
        missing = self.get_missing_information()
        validation = validate_annual_report(ledger, self.period_start, self.period_end)
        stripe_issues = self._stripe_validation_issues()
        return {
            "valid": validation.is_valid and not ledger_issues and not missing and not stripe_issues,
            "ledger_issues": list(ledger_issues),
            "financial_issues": [
                {"code": issue.code, "message": issue.message} for issue in validation.issues
            ],
            "bank_reconciliation": [
                {
                    "bank_account_id": item.bank_account_id,
                    "currency": item.currency,
                    "difference": str(item.difference) if item.difference is not None else None,
                    "status": item.status,
                    "issues": list(item.issues),
                }
                for item in reconciliation
            ],
            "stripe_reconciliation": self.get_stripe_reconciliation_status(),
            "stripe_issues": [
                {"code": issue.code, "message": issue.message, "severity": issue.severity}
                for issue in stripe_issues
            ],
            "missing_information": list(missing),
        }

    def generate_annual_report(self, output_dir: Path | None = None) -> tuple[AnnualReportDraft, dict[str, str] | None]:
        ledger, ledger_issues = self.build_ledger()
        issues = [
            DraftIssue("ledger_incomplete", message, "critical")
            for message in ledger_issues
        ]
        issues.extend(self._stripe_validation_issues())
        issues.extend(
            DraftIssue("bank_reconciliation", message, "critical")
            for result in self.reconcile_accounts() if result.status != "reconciled"
            for message in result.issues
        )
        issues.extend(
            DraftIssue("missing_information", message, "critical")
            for message in self.get_missing_information()
            if any(word in message for word in ("Import", "Resolve", "Classify", "could not", "Review"))
        )
        issues.extend(
            DraftIssue("missing_information", message, "warning")
            for message in DEFAULT_MISSING_INFORMATION
        )
        draft = build_annual_report_draft(
            company_name=self.company_name,
            registry_code=self.registry_code,
            period_start=self.period_start,
            period_end=self.period_end,
            ledger=ledger,
            reconciliation_issues=tuple(issues),
            missing_information=(
                *self.get_missing_information(),
                *self.get_advisory_information(),
                *DEFAULT_MISSING_INFORMATION,
            ),
            confirmed_figure_references=tuple(
                item.source_id for item in self.approved_report_values.values()
            ),
        )
        paths = export_annual_report_draft(draft, output_dir) if output_dir else None
        return draft, paths

    def _stripe_validation_issues(self) -> list[DraftIssue]:
        """Keep absent Stripe data out of unrelated workflows; block only when Stripe is evidenced."""
        stripe_used = bool(self.stripe_assistant.reports) or any(
            "stripe" in " ".join((row.counterparty or "", row.description or "")).casefold()
            for statement in self.bank_statements
            for row in statement.transactions
        )
        if not stripe_used:
            return []
        issues: list[DraftIssue] = []
        for item in self.stripe_assistant.missing_reports()["missing_or_unverified"]:
            issues.append(DraftIssue(
                "stripe_report_" + str(item["report_type"]),
                "Stripe report is missing or does not verify the configured period/currency.",
                "critical" if item.get("severity") == "critical" else "warning",
            ))
        balance = self.stripe_assistant.reconcile_balances()
        if balance.get("status") not in ("reconciled", "missing_balance_summary"):
            issues.append(DraftIssue(
                "stripe_balance_reconciliation",
                "Stripe opening balance, activity, fees, payouts, and closing balance do not reconcile.",
                "critical",
            ))
        fees = self.stripe_assistant.reconcile_fees()
        if fees.get("status") in ("unresolved", "currency_unverified"):
            issues.append(DraftIssue(
                "stripe_fee_reconciliation",
                "Stripe fee detail does not reconcile with the Balance Summary.",
                "critical",
            ))
        activity = self.stripe_assistant.reconcile_activity()
        if activity.get("status") in ("unresolved", "duplicate_rows_require_review", "currency_unverified"):
            issues.append(DraftIssue(
                "stripe_itemized_activity",
                "Itemized Stripe activity does not fully reconcile with the Balance Summary.",
                "critical",
            ))
        elif activity.get("status") == "partially_reconciled":
            issues.append(DraftIssue(
                "stripe_itemized_activity_partial",
                "Some itemized Stripe totals cannot be compared because report fields are absent.",
                "warning",
            ))
        elif activity.get("status") == "missing_balance_transactions":
            issues.append(DraftIssue(
                "stripe_itemized_activity_missing",
                "Itemized Stripe activity is unavailable for transaction-level review.",
                "warning",
            ))
        transfers = self.match_stripe_bank_transfers()
        if transfers.get("net_amount_difference") not in (None, "0.00"):
            issues.append(DraftIssue(
                "stripe_bank_net_movement",
                "Stripe-related net bank movements do not equal Stripe payouts.",
                "critical",
            ))
        if transfers.get("directional_incoming_vs_payout_difference") not in (None, "0.00"):
            issues.append(DraftIssue(
                "stripe_bank_transfer_direction",
                "Stripe receipts and bank payments to Stripe must be separately explained; a net match alone is insufficient.",
                "critical",
            ))
        return issues


def _candidate_id(candidate: ExtractedCandidate) -> str:
    return "-".join((
        candidate.source.sha256[:12], candidate.field,
        str(candidate.fiscal_year or "na"), str(candidate.value),
    ))


def default_rules_path(company_id: str = "default", data_dir: Path | None = None) -> Path:
    """Return a company-scoped rules path under the local application data directory."""
    if not company_id or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in company_id):
        raise ValueError("Company ID must contain only letters, digits, underscores, or hyphens.")
    root = data_dir or Path.home() / ".local" / "share" / "estonian-annual-report-ai" / "data"
    return (
        root
        / "companies"
        / company_id
        / "classification-rules.json"
    )
