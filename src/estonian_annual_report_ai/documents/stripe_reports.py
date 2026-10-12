"""Local, deterministic parsing for Stripe CSV reports."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from .common import (
    DocumentParseError,
    DocumentValidationError,
    decode_text,
    normalize_currency,
    normalize_label,
    parse_money,
    validate_document,
)
from .models import ImportIssue, SourceReference

StripeReportKind = Literal["balance_summary", "fees_summary", "balance_transactions", "unknown"]
_MAX_CSV_ROWS = 250_000


@dataclass(frozen=True, slots=True)
class StripeRow:
    """One preserved CSV row with parsed values and a stable duplicate identity."""

    source: SourceReference
    fields: tuple[tuple[str, str | None], ...]
    decimal_fields: tuple[tuple[str, Decimal | None], ...]
    date_fields: tuple[tuple[str, date], ...]
    identity_sha256: str
    duplicate_of: SourceReference | None = None

    def get(self, column: str) -> str | None:
        """Return a raw field by case- and punctuation-insensitive column name."""
        wanted = normalize_label(column)
        for name, value in self.fields:
            if normalize_label(name) == wanted:
                return value
        return None

    def decimal(self, column: str) -> Decimal | None:
        """Return a parsed amount by column name, preserving absent or invalid values."""
        wanted = normalize_label(column)
        for name, value in self.decimal_fields:
            if normalize_label(name) == wanted:
                return value
        return None


@dataclass(frozen=True, slots=True)
class StripePreambleRow:
    """A non-empty row before the detected table header, retained verbatim."""

    source: SourceReference
    cells: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StripeReport:
    """Parsed Stripe CSV table; unknown columns and incomplete rows are retained."""

    source: SourceReference
    kind: StripeReportKind
    headers: tuple[str, ...]
    rows: tuple[StripeRow, ...]
    currencies: tuple[str, ...]
    period_start: date | None
    period_end: date | None
    preamble_rows: tuple[StripePreambleRow, ...] = ()
    issues: tuple[ImportIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class StripeReconciliation:
    """Arithmetic-only comparison of Stripe balance and fee summary totals."""

    opening_balance: Decimal | None
    gross_activity: Decimal | None
    fees: Decimal | None
    payouts: Decimal | None
    closing_balance: Decimal | None
    fee_report_total: Decimal | None
    calculated_closing_balance: Decimal | None
    balance_difference: Decimal | None
    balance_reconciled: bool | None
    fee_difference: Decimal | None
    fees_reconciled: bool | None


def parse_stripe_report(path: str | Path, *, max_bytes: int | None = None) -> StripeReport:
    """Parse a local Stripe Balance Summary, All Fees Summary, or activity CSV.

    CSV fields stay available as raw strings. Only money-shaped columns become
    Decimals, dates are parsed only from date-shaped columns, and every data row
    keeps its source document and physical row number. No financial classification
    is performed.
    """
    file_path = Path(path)
    if file_path.suffix.lower() != ".csv":
        raise DocumentValidationError("Stripe reports must be CSV files.")
    kwargs = {} if max_bytes is None else {"max_bytes": max_bytes}
    document = validate_document(file_path, **kwargs)
    text = decode_text(document.data)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    try:
        parsed_rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error as exc:
        raise DocumentParseError("Stripe CSV could not be parsed safely.") from exc
    if len(parsed_rows) > _MAX_CSV_ROWS:
        raise DocumentValidationError("Stripe CSV exceeds the row limit.")

    nonempty_rows = [
        (index, tuple(row))
        for index, row in enumerate(parsed_rows, 1)
        if any(cell.strip() for cell in row)
    ]
    header_position = next(
        (position for position, (_, cells) in enumerate(nonempty_rows) if _is_header(cells)),
        None,
    )
    if header_position is None:
        return StripeReport(
            source=document.source,
            kind="unknown",
            headers=(),
            rows=(),
            currencies=(),
            period_start=None,
            period_end=None,
            preamble_rows=tuple(
                StripePreambleRow(document.source.at(row_number=index), cells)
                for index, cells in nonempty_rows
            ),
            issues=(
                ImportIssue(
                    "missing_header",
                    "No recognizable Stripe CSV table header was found.",
                    document.source,
                ),
            ),
        )

    _, raw_headers = nonempty_rows[header_position]
    headers = _unique_headers(raw_headers)
    body = nonempty_rows[header_position + 1 :]
    extra_column_count = max((len(cells) - len(headers) for _, cells in body), default=0)
    if extra_column_count:
        headers += tuple(f"__extra_{index}" for index in range(1, extra_column_count + 1))
    preamble = tuple(
        StripePreambleRow(document.source.at(row_number=index), cells)
        for index, cells in nonempty_rows[:header_position]
    )
    issues: list[ImportIssue] = []
    if not body:
        issues.append(ImportIssue("empty_report", "Stripe CSV contains a header but no data rows.", document.source))

    rows: list[StripeRow] = []
    first_by_identity: dict[str, SourceReference] = {}
    currencies: set[str] = set()
    all_dates: list[date] = []
    explicit_start: list[date] = []
    explicit_end: list[date] = []
    for row_number, cells in body:
        values: list[str | None] = [cell if cell.strip() else None for cell in cells[: len(headers)]]
        if len(values) < len(headers):
            values.extend([None] * (len(headers) - len(values)))
        fields = tuple(zip(headers, values, strict=True))
        source = document.source.at(row_number=row_number)
        decimal_fields: list[tuple[str, Decimal | None]] = []
        date_fields: list[tuple[str, date]] = []
        for column, value in fields:
            normalized_column = normalize_label(column)
            header_currency = _currency_in_header(column)
            if header_currency:
                currencies.add(header_currency)
            if _is_money_column(normalized_column):
                amount, currency = parse_money(value)
                decimal_fields.append((column, amount))
                if currency:
                    currencies.add(currency)
            elif _is_date_column(normalized_column) and value is not None:
                parsed_date = _parse_date(value)
                if parsed_date:
                    date_fields.append((column, parsed_date))
                    all_dates.append(parsed_date)
                    if normalized_column in {"period start", "start date", "from date"}:
                        explicit_start.append(parsed_date)
                    if normalized_column in {"period end", "end date", "to date"}:
                        explicit_end.append(parsed_date)
            if normalized_column in {"currency", "settlement currency", "presentment currency"} and value:
                currency = normalize_currency(value)
                if currency:
                    currencies.add(currency)

        identity = _row_identity(fields)
        duplicate_of = first_by_identity.get(identity)
        first_by_identity.setdefault(identity, source)
        rows.append(
            StripeRow(
                source=source,
                fields=fields,
                decimal_fields=tuple(decimal_fields),
                date_fields=tuple(date_fields),
                identity_sha256=identity,
                duplicate_of=duplicate_of,
            )
        )

    kind = _detect_kind(headers, tuple(rows))
    _apply_preamble_period(preamble, all_dates, explicit_start, explicit_end)
    _apply_summary_period(rows, all_dates, explicit_start, explicit_end)
    if kind == "unknown":
        issues.append(
            ImportIssue(
                "unrecognized_report",
                "Stripe CSV table type could not be identified.",
                document.source,
            )
        )
    return StripeReport(
        source=document.source,
        kind=kind,
        headers=headers,
        rows=tuple(rows),
        currencies=tuple(sorted(currencies)),
        # Transaction dates describe observed activity, not the export's selected
        # date range. Only explicit report metadata or summary boundaries prove coverage.
        period_start=min(explicit_start, default=None),
        period_end=max(explicit_end, default=None),
        preamble_rows=preamble,
        issues=tuple(issues),
    )


def reconcile_stripe_summary(
    opening_balance: Decimal | None,
    gross_activity: Decimal | None,
    fees: Decimal | None,
    payouts: Decimal | None,
    closing_balance: Decimal | None,
    fee_report_total: Decimal | None = None,
) -> StripeReconciliation:
    """Compare signed summary values without assigning accounting meanings.

    Balance values use the signs printed in the report. Fee totals are compared
    by absolute value because fee summaries may show charges as positive while a
    balance summary shows the same fees as a negative balance movement.
    """
    values = (opening_balance, gross_activity, fees, payouts)
    calculated = sum(values, Decimal(0)) if all(value is not None for value in values) else None
    balance_difference = (
        calculated - closing_balance
        if calculated is not None and closing_balance is not None
        else None
    )
    fee_difference = abs(fees) - abs(fee_report_total) if fees is not None and fee_report_total is not None else None
    return StripeReconciliation(
        opening_balance=opening_balance,
        gross_activity=gross_activity,
        fees=fees,
        payouts=payouts,
        closing_balance=closing_balance,
        fee_report_total=fee_report_total,
        calculated_closing_balance=calculated,
        balance_difference=balance_difference,
        balance_reconciled=balance_difference == 0 if balance_difference is not None else None,
        fee_difference=fee_difference,
        fees_reconciled=fee_difference == 0 if fee_difference is not None else None,
    )


def _is_header(cells: tuple[str, ...]) -> bool:
    nonempty = [normalize_label(cell) for cell in cells if cell.strip()]
    if len(nonempty) < 2:
        return False
    known = {
        "amount", "gross", "gross amount", "gross activity", "fee", "fees", "fee amount", "net", "net amount",
        "net activity", "currency", "created", "created utc", "date", "type", "reporting category",
        "opening balance", "closing balance", "payout", "payouts", "description", "fee type", "fee description",
        "balance transaction id", "transaction id", "id", "period start", "period end", "balance",
    }
    return sum(
        value in known
        or (not re.search(r"\d", value) and (_is_money_column(value) or _is_date_column(value)))
        for value in nonempty
    ) >= 2


def _detect_kind(headers: tuple[str, ...], rows: tuple[StripeRow, ...]) -> StripeReportKind:
    normalized = {_canonical_header(header) for header in headers}
    categories = {normalize_label(row.get("category") or "").replace(" ", "_") for row in rows}
    summary_categories = {
        "starting_balance", "opening_balance", "activity_gross", "activity_fee", "activity",
        "payouts_gross", "payouts_fee", "payouts", "ending_balance", "closing_balance",
    }
    if {"category", "description", "net amount", "currency"}.issubset(normalized) and categories & summary_categories:
        return "balance_summary"
    if {"feature name", "feature description", "amount", "tax", "currency", "billing status"}.issubset(normalized):
        return "fees_summary"
    if {"opening balance", "closing balance"}.issubset(normalized) or {
        "gross activity", "net activity", "payouts"
    }.issubset(normalized):
        return "balance_summary"
    has_fee_label = bool(normalized & {"fee type", "fee description", "fee category", "fee name"})
    has_net = bool(normalized & {"net", "net amount", "net activity"})
    has_gross = bool(normalized & {"gross", "gross amount", "gross activity"})
    has_id = bool(normalized & {"id", "transaction id", "balance transaction id"})
    has_date = any(_is_date_column(value) for value in normalized)
    if has_fee_label and not (has_gross and has_net):
        return "fees_summary"
    if has_id and (has_date or has_net or has_gross):
        return "balance_transactions"
    if has_gross and has_net and "fee" in normalized:
        return "balance_transactions"
    return "unknown"


def _is_money_column(normalized: str) -> bool:
    tokens = set(normalized.split())
    return bool(tokens & {
        "amount", "gross", "fee", "fees", "net", "balance", "payout", "payouts", "total", "volume",
        "tax", "refund", "dispute", "charge", "exchange", "conversion", "adjustment", "reserve",
        "credit", "debit", "funding", "principal", "interest", "activity",
    })


def _is_date_column(normalized: str) -> bool:
    tokens = set(normalized.split())
    return bool(tokens & {"date", "created", "available", "occurred", "period", "from", "to"})


def _parse_date(value: str) -> date | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        return date.fromisoformat(candidate[:10])
    except ValueError:
        pass
    for pattern in ("%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(candidate, pattern).date()
        except ValueError:
            continue
    return None


def _currency_in_header(header: str) -> str | None:
    known_codes = {
        "AUD", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "HUF", "IDR", "ILS", "INR",
        "JPY", "KRW", "MXN", "MYR", "NOK", "NZD", "PHP", "PLN", "RON", "RUB", "SEK", "SGD", "THB",
        "TRY", "TWD", "UAH", "USD", "VND", "ZAR",
    }
    match = re.search(r"\b([A-Z]{3})\b", header)
    return normalize_currency(match.group(1)) if match and match.group(1) in known_codes else None


def _apply_summary_period(
    rows: list[StripeRow],
    all_dates: list[date],
    explicit_start: list[date],
    explicit_end: list[date],
) -> None:
    """Use explicit opening/closing summary descriptions to recover report bounds."""
    for row in rows:
        category = normalize_label(row.get("category") or "").replace(" ", "_")
        description = row.get("description") or ""
        dates = _dates_in_text(description)
        if category in {"starting_balance", "opening_balance"}:
            explicit_start.extend(dates)
        elif category in {"ending_balance", "closing_balance"}:
            explicit_end.extend(dates)
        all_dates.extend(dates)


def _apply_preamble_period(
    preamble: tuple[StripePreambleRow, ...],
    all_dates: list[date],
    explicit_start: list[date],
    explicit_end: list[date],
) -> None:
    """Recover an explicitly labeled date range from Stripe report metadata."""
    for row in preamble:
        text = " ".join(row.cells)
        normalized = normalize_label(text)
        dates = _dates_in_text(text)
        if not dates:
            continue
        all_dates.extend(dates)
        if len(dates) > 1:
            explicit_start.append(dates[0])
            explicit_end.append(dates[-1])
        elif any(token in normalized.split() for token in ("start", "from", "beginning")):
            explicit_start.append(dates[0])
        elif any(token in normalized.split() for token in ("end", "to", "through")):
            explicit_end.append(dates[0])


def _dates_in_text(value: str) -> list[date]:
    patterns = (
        r"\b20\d{2}-\d{1,2}-\d{1,2}\b",
        r"\b\d{1,2}[./]\d{1,2}[./]20\d{2}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},\s*20\d{2}\b",
    )
    parsed: list[date] = []
    for pattern in patterns:
        for match in re.finditer(pattern, value):
            item = _parse_date(match.group(0))
            if item and item not in parsed:
                parsed.append(item)
    return parsed


def _canonical_header(header: str) -> str:
    normalized = normalize_label(header)
    return re.sub(r"\s+(?:eur|usd|gbp|sek|nok|dkk|cad|aud)$", "", normalized)


def _unique_headers(raw_headers: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(raw_headers, 1):
        name = raw.strip() or f"__unnamed_{index}"
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        result.append(name if seen[key] == 1 else f"{name}__{seen[key]}")
    return tuple(result)


def _row_identity(fields: tuple[tuple[str, str | None], ...]) -> str:
    payload = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
