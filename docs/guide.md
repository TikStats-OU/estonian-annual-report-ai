# User and Developer Guide

This guide describes the current local draft workflow. It is not accounting advice, a statutory report generator, or a filing service.

## Getting started

1. Install Python 3.12 or later and create a virtual environment.
2. Install the local package: `python -m pip install -e .`.
3. Configure one company and reporting period using the environment variables in the README. Use a dedicated private data directory.
4. Start `estonian-annual-report-ai-mcp` from the MCP client using stdio.
5. Begin with synthetic files and confirm the MCP tools appear before importing real documents.
6. Keep original source files and generated evidence/reports in protected local storage. The application is not a backup system.

## For business owners

The importers accept CSV/XLSX/text-based PDF bank statements, text PDF/TXT invoices, and text PDF/TXT prior reports. Scanned PDFs need OCR outside the current application. Keep separate exports for payment providers and currencies when applicable.

The software extracts candidate rows and source references, flags duplicates and possible transfers, and applies explicit deterministic rules. It does not decide that every deposit is revenue or every withdrawal is an expense. Review unclassified transactions, missing evidence, and reconciliation differences before approving a posting or generating a draft.

AI classification is optional, disabled by default, and can send selected data to an external API only after the workflow's explicit opt-in/confirmation. Do not enable it for confidential records without authorizing that processing.

## For accountants

The ledger uses double-entry journal entries and decimal arithmetic. The workflow expects approved, balanced entries; classifications and estimates remain separate from ledger postings until approved. Bank and Stripe reconciliation checks arithmetic and matching independently of economic classification. Retain supporting invoices and explain period cut-offs, foreign-exchange rates, fixed-asset policies, taxes, accruals, and related-party decisions.

The generated statements are review drafts. The application does not automatically book invoice accruals, VAT, payroll, fixed-asset depreciation, receivables/payables, or every period-end adjustment. Missing account-level opening data can block report totals even when transaction imports succeed.

## For developers

The main layers are:

- `documents`: local parsing and source references.
- `transactions`: normalized rows, deterministic rules, optional AI proposals, duplicate/transfer matching, and reconciliation.
- `accounting`: deterministic double-entry ledger and statements.
- `application.py`: orchestration across imports, approvals, evidence, and draft generation.
- `reporting`: review drafts, exports, and pre-filing readiness.
- `mcp_server.py`: thin local stdio tool adapter.

Add parsers under `documents`, keep accounting arithmetic in `accounting`, and have MCP handlers call orchestration services rather than implement parallel rules. Use synthetic fixtures, preserve source references, and test unresolved and malformed inputs. Development and contribution instructions are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## MCP surface

The server currently exposes tools only; it has no MCP resources or prompts. The 33 tools are grouped by responsibility:

- Import and transaction workflow: `import_documents`, `parse_bank_statements`, `normalize_transactions`, `classify_transactions`, `get_unclassified_transactions`, `approve_classification`, `reconcile_accounts`.
- Statements and readiness: `generate_financial_statements`, `generate_annual_report`, `validate_annual_report`, `validate_pre_filing`, `get_missing_information`.
- Prior reports and fixed assets: `get_previous_report_candidates`, `approve_previous_report_value`, `get_fixed_asset_history`.
- Evidence and decisions: `get_evidence_registry`, `get_pending_evidence_questions`, `record_user_confirmation`, `mark_evidence_question_asked`, `mark_evidence_unavailable`, `record_evidence_issue`, `resolve_evidence_issue`, `evaluate_accounting_issue`, `approve_accounting_recommendation`.
- Stripe: `detect_stripe_reports`, `get_missing_stripe_reports`, `generate_stripe_download_instructions`, `import_stripe_report`, `reconcile_stripe_balances`, `reconcile_stripe_fees`, `reconcile_stripe_activity`, `match_stripe_bank_transfers`, `get_stripe_reconciliation_status`.

## Deployment and backups

Only local stdio is implemented. The included Docker Compose file also runs local stdio and publishes no ports. No remote HTTP service, user authentication, tenant isolation, health endpoint, or production backup/restore implementation exists. Back up source documents, company workspace data, and generated reports using an encrypted storage system with tested restore procedures. Do not put financial records into GitHub, CI, or shared artifacts.

