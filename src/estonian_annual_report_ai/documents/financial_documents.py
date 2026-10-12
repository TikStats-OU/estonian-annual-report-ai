"""Conservative extraction from invoices and historic annual reports."""

from __future__ import annotations

import io
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .common import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_PDF_PAGES,
    DocumentParseError,
    DocumentValidationError,
    decode_text,
    extract_money_tokens,
    normalize_currency,
    normalize_label,
    parse_money,
    validate_document,
)
from .models import (
    AnnualReportExtraction,
    ExtractedCandidate,
    FinancialDiscrepancy,
    ImportIssue,
    InvoiceExtraction,
    SourceReference,
)

_INVOICE_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("invoice_number", ("invoice number", "invoice no", "arve number", "arve nr", "arve no")),
    ("issue_date", ("issue date", "date of issue", "invoice date", "arve kuupaev", "kuupaev")),
    ("due_date", ("due date", "payment due", "maksetahtaeg", "tasumise tahtaeg")),
    ("seller", ("seller", "supplier", "vendor", "müüja", "teenuse osutaja")),
    ("buyer", ("buyer", "customer", "client", "ostja", "arve saaja")),
    ("net_amount", ("net amount", "subtotal", "amount excl vat", "total excl vat", "summa kaibemaksuta", "kaibemaksuta")),
    ("tax_amount", ("vat", "vat amount", "tax amount", "sales tax", "kaibemaks")),
    ("total_amount", ("grand total", "total amount", "amount due", "total", "kokku", "tasuda kokku", "summa kokku")),
    ("description", ("description", "item", "service", "goods", "selgitus", "teenus", "kaup")),
)

_REPORT_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("retained_earnings", (
        "retained earnings", "retained profit", "jaotamata kasum", "eelmiste perioodide jaotamata kasum",
        "jaotamata kasum kahjum", "previous periods retained earnings",
    )),
    ("profit_loss", (
        "profit loss for the financial year", "profit loss for financial year", "profit for the year",
        "loss for the year", "net profit", "net loss", "aruandeaasta kasum", "aruandeaasta kahjum",
        "finantsaasta kasum", "finantsaasta kahjum", "aruandeaasta tulem",
    )),
    ("revenue", ("revenue", "net sales", "sales revenue", "muugitulu", "müügitulu")),
    ("expenses", ("total expenses", "expenses total", "kulud kokku", "kogukulud", "total costs")),
    ("liabilities", ("total liabilities", "liabilities total", "kohustised kokku", "kohustused kokku")),
    ("equity", ("total equity", "equity total", "omakapital kokku")),
    ("assets", ("total assets", "assets total", "varad kokku", "aktiva kokku")),
)

_FIXED_ASSET_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("accumulated_depreciation", (
        "accumulated depreciation", "accumulated depreciation and impairment",
        "computers and computer systems - accumulated depreciation",
        "põhivara akumuleeritud kulum", "akumuleeritud kulum", "akumuleeritud amortisatsioon",
    )),
    ("depreciation_expense", (
        "depreciation and impairment loss", "depreciation of non-current assets",
        "depreciation expense", "põhivarade kulum ja väärtuse langus",
        "põhivara kulum", "amortisatsioonikulu",
    )),
    ("original_cost", (
        "original acquisition cost", "acquisition cost", "soetusmaksumus", "soetusmaksumuses",
        "computers and computer systems",
    )),
    ("net_book_value", (
        "net book value", "carrying amount", "total fixed assets", "total property plant and equipment",
        "tangible assets", "machinery and equipment", "materiaalsed põhivarad", "materiaalne põhivara",
    )),
)

_FIXED_ASSET_POLICY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("depreciation_method", ("depreciation is calculated", "depreciation method", "amortisatsiooni arvestatakse", "amortisatsioonimeetod")),
    ("useful_life", ("useful life", "kasulik eluiga", "kasulikud eluead", "eeldatavatest kasulikest eluigadest")),
    ("capitalization_threshold", ("cost of at least", "maksumusega alates", "capitalization threshold")),
    ("depreciation_start", ("depreciation starts", "amortisatsiooni arvestamist alustatakse")),
    ("residual_value_review", ("residual value", "lõppväärtuse põhjendatust")),
)

_YEAR = re.compile(r"\b(20\d{2})\b")


def parse_invoice(path: str | Path, *, max_bytes: int | None = None) -> InvoiceExtraction:
    """Extract labeled invoice candidates from a local PDF or text file."""
    kwargs = {} if max_bytes is None else {"max_bytes": max_bytes}
    document = validate_document(path, **kwargs)
    extension = Path(path).suffix.lower()
    if extension == ".txt":
        pages = [(1, decode_text(document.data))]
        tables_by_page: dict[int, list[list[list[object]]]] = {}
    elif extension == ".pdf":
        pages, tables_by_page = _extract_pdf_pages_and_tables(document.data)
    else:
        raise DocumentValidationError("Invoices must be PDF or text files.")

    candidates: list[ExtractedCandidate] = []
    text_fields_by_page: set[tuple[int, str]] = set()
    for page_number, text in pages:
        for line_number, line in enumerate(text.splitlines(), 1):
            match = _match_label(line, _INVOICE_LABELS)
            if match is None:
                continue
            field, label, value_text = match
            source = document.source.at(page_number=page_number if extension == ".pdf" else None, row_number=line_number)
            line_candidates = _invoice_candidates(field, label, value_text, source)
            candidates.extend(line_candidates)
            if extension == ".pdf" and line_candidates:
                text_fields_by_page.add((page_number, field))
    for page_number, tables in tables_by_page.items():
        for table in tables:
            for row_number, row in enumerate(table, 1):
                line = " ".join(str(cell) for cell in row if cell is not None)
                match = _match_label(line, _INVOICE_LABELS)
                if match is None:
                    continue
                field, label, value_text = match
                if (page_number, field) in text_fields_by_page:
                    continue
                source = document.source.at(page_number=page_number, row_number=row_number)
                candidates.extend(_invoice_candidates(field, label, value_text, source))
    issues: list[ImportIssue] = []
    if not candidates:
        code = "no_text_content" if not any(text.strip() for _, text in pages) and not tables_by_page else "no_invoice_fields"
        message = "Document has no extractable text." if code == "no_text_content" else "No labeled invoice fields were recognized."
        issues.append(ImportIssue(code, message, document.source))
    return InvoiceExtraction(document.source, tuple(_deduplicate_candidates(candidates)), tuple(issues))


def extract_annual_report(path: str | Path, *, max_bytes: int | None = None) -> AnnualReportExtraction:
    """Extract historical financial-value candidates without choosing among them."""
    kwargs = {} if max_bytes is None else {"max_bytes": max_bytes}
    document = validate_document(path, **kwargs)
    extension = Path(path).suffix.lower()
    if extension == ".txt":
        pages = [(1, decode_text(document.data))]
        tables_by_page: dict[int, list[list[list[object]]]] = {}
    elif extension == ".pdf":
        pages, tables_by_page = _extract_pdf_pages_and_tables(document.data)
    else:
        raise DocumentValidationError("Annual reports must be PDF or text files.")

    candidates: list[ExtractedCandidate] = []
    fixed_asset_candidates: list[ExtractedCandidate] = []
    fixed_asset_policies: list[ExtractedCandidate] = []
    for page_number, tables in tables_by_page.items():
        for table_index, table in enumerate(tables, 1):
            candidates.extend(_report_table_candidates(table, document.source, page_number, table_index))
            fixed_asset_candidates.extend(
                _fixed_asset_table_candidates(table, document.source, page_number)
            )
    table_fields_by_page = {
        (candidate.source.page_number, candidate.field)
        for candidate in candidates
        if candidate.source.page_number is not None
    }
    for page_number, text in pages:
        column_years = _report_column_years(text)
        for line_number, line in enumerate(text.splitlines(), 1):
            source = document.source.at(page_number=page_number if extension == ".pdf" else None, row_number=line_number)
            line_candidates = _report_line_candidates(
                line,
                source,
            )
            candidates.extend(
                candidate
                for candidate in line_candidates
                if extension != ".pdf" or (page_number, candidate.field) not in table_fields_by_page
            )
            fixed_asset_candidates.extend(_fixed_asset_line_candidates(line, source, column_years))
            policy = _fixed_asset_policy_candidate(line, source)
            if policy:
                fixed_asset_policies.append(policy)
    candidates = _deduplicate_candidates(candidates)
    fixed_asset_candidates = _deduplicate_fixed_asset_candidates(fixed_asset_candidates)
    fixed_asset_policies = _deduplicate_candidates(fixed_asset_policies)
    issues: list[ImportIssue] = []
    if not candidates:
        code = "no_text_content" if not any(text.strip() for _, text in pages) and not tables_by_page else "no_financial_values"
        message = "Document has no extractable text." if code == "no_text_content" else "No labeled financial values were recognized."
        issues.append(ImportIssue(code, message, document.source))
    if not fixed_asset_candidates and not fixed_asset_policies:
        issues.append(ImportIssue("no_fixed_asset_details", "No fixed-asset schedule or depreciation policy was extracted.", document.source))
    return AnnualReportExtraction(
        document.source,
        tuple(candidates),
        tuple(issues),
        tuple(fixed_asset_candidates),
        tuple(fixed_asset_policies),
    )


def find_financial_discrepancies(
    extractions: Iterable[AnnualReportExtraction],
) -> tuple[FinancialDiscrepancy, ...]:
    """Report differing extracted values while retaining all source candidates."""
    grouped: dict[tuple[str, int | None], list[ExtractedCandidate]] = defaultdict(list)
    for extraction in extractions:
        for candidate in extraction.candidates:
            if isinstance(candidate.value, Decimal):
                grouped[(candidate.field, candidate.fiscal_year)].append(candidate)
    discrepancies: list[FinancialDiscrepancy] = []
    for (field, fiscal_year), candidates in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1] or 0)):
        values = tuple(sorted({candidate.value for candidate in candidates if isinstance(candidate.value, Decimal)}))
        value_currencies = {
            (candidate.value, candidate.currency)
            for candidate in candidates
            if isinstance(candidate.value, Decimal)
        }
        if len(value_currencies) > 1:
            discrepancies.append(
                FinancialDiscrepancy(
                    field=field,
                    fiscal_year=fiscal_year,
                    values=values,
                    currencies=tuple(candidate.currency for candidate in candidates),
                    sources=tuple(candidate.source for candidate in candidates),
                )
            )
    return tuple(discrepancies)


def _extract_pdf_pages_and_tables(
    data: bytes,
) -> tuple[list[tuple[int, str]], dict[int, list[list[list[object]]]]]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise DocumentParseError("PDF parsing requires the pdfplumber package.") from exc
    pages: list[tuple[int, str]] = []
    tables: dict[int, list[list[list[object]]]] = {}
    extracted_chars = 0
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PDF_PAGES:
                raise DocumentValidationError("PDF exceeds the page limit.")
            for page_number, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text() or ""
                extracted_chars += len(page_text)
                if extracted_chars > MAX_EXTRACTED_TEXT_CHARS:
                    raise DocumentValidationError("PDF extracted text exceeds the safety limit.")
                pages.append((page_number, page_text))
                extracted = page.extract_tables() or []
                if extracted:
                    extracted_chars += sum(
                        len(str(cell)) for table in extracted for row in table for cell in row if cell is not None
                    )
                    if extracted_chars > MAX_EXTRACTED_TEXT_CHARS:
                        raise DocumentValidationError("PDF extracted tables exceed the safety limit.")
                    tables[page_number] = extracted
    except DocumentValidationError:
        raise
    except Exception as exc:
        raise DocumentParseError("PDF document could not be parsed safely.") from exc
    return pages, tables


def _match_label(
    line: str,
    labels: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[str, str, str] | None:
    normalized_line = normalize_label(line)
    for field, aliases in labels:
        for alias in aliases:
            normalized_alias = normalize_label(alias)
            if normalized_line == normalized_alias or normalized_line.startswith(normalized_alias + " "):
                # Split at the visible label to retain exact source text for the candidate.
                pattern = re.compile(re.escape(alias).replace(r"\ ", r"\s+"), re.IGNORECASE)
                match = pattern.search(line)
                if match:
                    label = match.group(0)
                    value_text = line[match.end() :].lstrip(" :#\t-–")
                else:
                    label = alias
                    value_text = line
                return field, label, value_text
    return None


def _invoice_candidates(
    field: str,
    label: str,
    value_text: str,
    source: SourceReference,
) -> list[ExtractedCandidate]:
    if not value_text:
        return []
    if field in {"issue_date", "due_date"}:
        value = _parse_date(value_text)
        return [ExtractedCandidate(field, value, source, raw_label=label)] if value else []
    if field in {"net_amount", "tax_amount", "total_amount"}:
        return [
            ExtractedCandidate(field, amount, source, currency=currency or normalize_currency(value_text), raw_label=label)
            for amount, currency, _ in extract_money_tokens(value_text)
        ]
    value = value_text.strip()
    return [ExtractedCandidate(field, value, source, raw_label=label)] if value else []


def _report_table_candidates(
    table: list[list[object]],
    document_source: SourceReference,
    page_number: int,
    table_index: int,
) -> list[ExtractedCandidate]:
    year_by_column: dict[int, int] = {}
    results: list[ExtractedCandidate] = []
    for row_number, row in enumerate(table, 1):
        cells = [str(value).strip() if value is not None else "" for value in row]
        year_cells = {
            index: int(match.group(1))
            for index, value in enumerate(cells)
            if (match := re.fullmatch(r"\s*(20\d{2})\s*", value))
        }
        if year_cells:
            year_by_column = year_cells
            continue
        label_text = " ".join(value for value in cells[:2] if value)
        field = _report_field(label_text)
        if not field:
            continue
        for column_index, raw_value in enumerate(cells[1:], 1):
            if not raw_value:
                continue
            amount, currency = parse_money(raw_value)
            if amount is None:
                continue
            if raw_value.strip().isdigit() and 1900 <= int(raw_value.strip()) <= 2100:
                continue
            results.append(
                ExtractedCandidate(
                    field=field,
                    value=amount,
                    source=document_source.at(page_number=page_number, row_number=row_number),
                    currency=currency,
                    raw_label=label_text or None,
                    fiscal_year=year_by_column.get(column_index),
                )
            )
    return results


def _report_line_candidates(line: str, source: SourceReference) -> list[ExtractedCandidate]:
    match = _match_label(line, _REPORT_LABELS)
    if match is None:
        return []
    field, label, value_text = match
    years = [int(value) for value in _YEAR.findall(value_text)]
    without_years = _YEAR.sub(" ", value_text)
    tokens = extract_money_tokens(without_years)
    fiscal_year = years[0] if len(years) == 1 else None
    return [
        ExtractedCandidate(
            field=field,
            value=amount,
            source=source,
            currency=currency or normalize_currency(without_years),
            raw_label=label,
            fiscal_year=fiscal_year,
        )
        for amount, currency, _ in tokens
    ]


def _fixed_asset_field(text: str) -> str | None:
    normalized = normalize_label(text)
    for field, aliases in _FIXED_ASSET_LABELS:
        for alias in aliases:
            normalized_alias = normalize_label(alias)
            if normalized == normalized_alias or normalized.startswith(normalized_alias + " "):
                return field
    return None


def _fixed_asset_line_candidates(
    line: str,
    source: SourceReference,
    column_years: tuple[int, ...],
) -> list[ExtractedCandidate]:
    normalized = normalize_label(line)
    matched: tuple[str, str] | None = None
    for field, aliases in _FIXED_ASSET_LABELS:
        for alias in aliases:
            normalized_alias = normalize_label(alias)
            if normalized == normalized_alias or normalized.startswith(normalized_alias + " ") or f" {normalized_alias} " in f" {normalized} ":
                matched = (field, alias)
                break
        if matched:
            break
    if matched is None:
        return []
    field, label = matched
    raw_match = re.search(re.escape(label).replace(r"\ ", r"\s+"), line, re.IGNORECASE)
    value_text = line[raw_match.end():] if raw_match else line
    value_text = value_text.lstrip(" :#\t")
    tokens = extract_money_tokens(value_text)
    if not tokens:
        return []
    years = [int(value) for value in _YEAR.findall(value_text)]
    return [
        ExtractedCandidate(
            field=field,
            value=amount,
            source=source,
            currency=currency or normalize_currency(value_text),
            raw_label=label,
            fiscal_year=(years[index] if len(years) == len(tokens) else column_years[index] if index < len(column_years) else None),
        )
        for index, (amount, currency, _) in enumerate(tokens)
    ]


def _report_column_years(text: str) -> tuple[int, ...]:
    """Infer current/comparative years from statement date headings near the top."""
    heading = "\n".join(text.splitlines()[:12])
    years: list[int] = []
    for match in re.finditer(r"(?:01\.01\.|31\.12\.|\b)(20\d{2})", heading):
        year = int(match.group(1))
        if year not in years:
            years.append(year)
    return tuple(years[:2])


def _deduplicate_fixed_asset_candidates(
    candidates: Sequence[ExtractedCandidate],
) -> list[ExtractedCandidate]:
    unique: dict[tuple[object, ...], ExtractedCandidate] = {}
    for item in candidates:
        key = (item.field, item.value, item.currency, item.fiscal_year, item.source.page_number)
        unique.setdefault(key, item)
    return list(unique.values())


def _fixed_asset_table_candidates(
    table: list[list[object]],
    document_source: SourceReference,
    page_number: int,
) -> list[ExtractedCandidate]:
    results: list[ExtractedCandidate] = []
    year_by_column: dict[int, int] = {}
    for row_number, row in enumerate(table, 1):
        cells = [str(value).strip() if value is not None else "" for value in row]
        for column_index, cell in enumerate(cells):
            if match := re.fullmatch(r"\s*(20\d{2})\s*", cell):
                year_by_column[column_index] = int(match.group(1))
        field = _fixed_asset_field(" ".join(cells[:2]))
        if field is None:
            continue
        for column_index, raw_value in enumerate(cells[1:], 1):
            amount, currency = parse_money(raw_value)
            if amount is None:
                continue
            results.append(ExtractedCandidate(
                field=field,
                value=amount,
                source=document_source.at(page_number=page_number, row_number=row_number),
                currency=currency or "EUR",
                raw_label=" ".join(cells[:2]),
                fiscal_year=year_by_column.get(column_index),
            ))
    return results


def _fixed_asset_policy_candidate(line: str, source: SourceReference) -> ExtractedCandidate | None:
    normalized = normalize_label(line)
    for field, aliases in _FIXED_ASSET_POLICY_TERMS:
        if any(normalize_label(alias) in normalized for alias in aliases):
            value = line.strip()
            return ExtractedCandidate(
                field=field,
                value=value,
                source=source,
                raw_label=field,
                fiscal_year=next((int(year) for year in _YEAR.findall(value)), None),
            )
    return None


def _report_field(text: str) -> str | None:
    normalized = normalize_label(text)
    for field, aliases in _REPORT_LABELS:
        for alias in aliases:
            normalized_alias = normalize_label(alias)
            if normalized == normalized_alias or normalized.startswith(normalized_alias + " "):
                return field
    return None


def _parse_date(value: str) -> date | None:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _deduplicate_candidates(candidates: Sequence[ExtractedCandidate]) -> list[ExtractedCandidate]:
    unique: dict[tuple[object, ...], ExtractedCandidate] = {}
    for candidate in candidates:
        source = candidate.source
        key = (
            candidate.field,
            candidate.value,
            candidate.currency,
            candidate.fiscal_year,
            source.page_number,
            source.row_number,
        )
        unique.setdefault(key, candidate)
    return list(unique.values())
