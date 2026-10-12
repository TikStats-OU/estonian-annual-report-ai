"""Immutable, traceable values produced by local document parsers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Stable local-document identity and optional location within it."""

    document_id: str
    filename: str
    sha256: str
    file_type: str
    page_number: int | None = None
    row_number: int | None = None
    sheet_name: str | None = None

    def at(
        self,
        *,
        page_number: int | None = None,
        row_number: int | None = None,
        sheet_name: str | None = None,
    ) -> SourceReference:
        """Return this source with a row, page, or worksheet location."""
        from dataclasses import replace

        return replace(
            self,
            page_number=page_number if page_number is not None else self.page_number,
            row_number=row_number if row_number is not None else self.row_number,
            sheet_name=sheet_name if sheet_name is not None else self.sheet_name,
        )


@dataclass(frozen=True, slots=True)
class ImportIssue:
    code: str
    message: str
    source: SourceReference


@dataclass(frozen=True, slots=True)
class BankTransaction:
    """A transaction as extracted; missing or ambiguous fields remain None."""

    transaction_date: date | None
    amount: Decimal | None
    currency: str | None
    source: SourceReference
    bank_account_id: str | None = None
    counterparty: str | None = None
    description: str | None = None
    reference: str | None = None
    # credit/debit is populated when the document states flow separately from amount.
    direction: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedBankStatement:
    source: SourceReference
    transactions: tuple[BankTransaction, ...]
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    currency: str | None = None
    bank_account_id: str | None = None
    issues: tuple[ImportIssue, ...] = ()


type ExtractedScalar = str | date | Decimal


@dataclass(frozen=True, slots=True)
class ExtractedCandidate:
    field: str
    value: ExtractedScalar
    source: SourceReference
    currency: str | None = None
    raw_label: str | None = None
    fiscal_year: int | None = None


@dataclass(frozen=True, slots=True)
class InvoiceExtraction:
    source: SourceReference
    candidates: tuple[ExtractedCandidate, ...]
    issues: tuple[ImportIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class AnnualReportExtraction:
    source: SourceReference
    candidates: tuple[ExtractedCandidate, ...]
    issues: tuple[ImportIssue, ...] = ()
    fixed_asset_candidates: tuple[ExtractedCandidate, ...] = ()
    fixed_asset_policies: tuple[ExtractedCandidate, ...] = ()

    def for_field(self, field: str) -> tuple[ExtractedCandidate, ...]:
        """Return every source candidate; parsers never select over conflicts."""
        return tuple(candidate for candidate in self.candidates if candidate.field == field)


@dataclass(frozen=True, slots=True)
class FinancialDiscrepancy:
    field: str
    fiscal_year: int | None
    values: tuple[Decimal, ...]
    currencies: tuple[str | None, ...]
    sources: tuple[SourceReference, ...]
