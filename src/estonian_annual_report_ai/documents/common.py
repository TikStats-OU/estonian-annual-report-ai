"""Shared validation and conservative parsing helpers."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import SourceReference

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_XLSX_ENTRY_BYTES = 50 * 1024 * 1024
MAX_XLSX_ENTRIES = 2_000
MAX_PDF_PAGES = 500
MAX_EXTRACTED_TEXT_CHARS = 5_000_000
SUPPORTED_EXTENSIONS = {".pdf", ".csv", ".xlsx", ".txt"}


class DocumentValidationError(ValueError):
    """The document is unsupported, too large, or structurally unsafe."""


class DocumentParseError(ValueError):
    """The document could not be parsed safely."""


@dataclass(frozen=True, slots=True)
class ValidatedDocument:
    data: bytes
    source: SourceReference


def validate_document(path: str | Path, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> ValidatedDocument:
    """Read a supported local file after enforcing size and container checks."""
    file_path = Path(path)
    extension = file_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentValidationError("Unsupported document type.")
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive.")
    try:
        with file_path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except OSError as exc:
        raise DocumentValidationError("The local document could not be read.") from exc
    if len(data) > max_bytes:
        raise DocumentValidationError("Document exceeds the configured size limit.")
    _validate_content(extension, data)
    digest = hashlib.sha256(data).hexdigest()
    source = SourceReference(
        document_id=digest,
        filename=file_path.name,
        sha256=digest,
        file_type=extension[1:],
    )
    return ValidatedDocument(data=data, source=source)


def _validate_content(extension: str, data: bytes) -> None:
    if not data:
        raise DocumentValidationError("Document is empty.")
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise DocumentValidationError("File extension does not match PDF content.")
    if extension == ".xlsx":
        if not data.startswith(b"PK") or not zipfile.is_zipfile(io.BytesIO(data)):
            raise DocumentValidationError("File extension does not match XLSX content.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                total_size = sum(info.file_size for info in entries)
                if len(entries) > MAX_XLSX_ENTRIES or total_size > MAX_XLSX_UNCOMPRESSED_BYTES:
                    raise DocumentValidationError("XLSX container exceeds safety limits.")
                for info in entries:
                    if info.flag_bits & 0x1:
                        raise DocumentValidationError("Encrypted XLSX files are not supported.")
                    if info.file_size > MAX_XLSX_ENTRY_BYTES:
                        raise DocumentValidationError("XLSX entry exceeds the size limit.")
                    if info.file_size and info.compress_size == 0:
                        raise DocumentValidationError("XLSX container has an invalid entry.")
                    if info.compress_size and info.file_size / info.compress_size > 200:
                        raise DocumentValidationError("XLSX container exceeds compression limits.")
                if "[Content_Types].xml" not in archive.namelist():
                    raise DocumentValidationError("File is not a valid XLSX workbook.")
        except zipfile.BadZipFile as exc:
            raise DocumentValidationError("File is not a valid XLSX container.") from exc
    if extension in {".csv", ".txt"} and b"\x00" in data:
        raise DocumentValidationError("Text document contains binary data.")


def decode_text(data: bytes) -> str:
    """Decode common local text encodings without replacing invalid bytes."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError("Text document encoding is not supported.")


def normalize_label(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_currency(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text or text in {"-", "—"}:
        return None
    symbols = {"€": "EUR", "£": "GBP", "¥": "JPY"}
    if text in symbols:
        return symbols[text]
    match = re.search(r"\b([A-Z]{3})\b", text)
    return match.group(1) if match else None


_CURRENCY_TOKEN = re.compile(r"(?i)(?:\b([A-Z]{3})\b|([€£¥$]))")


def parse_money(value: object) -> tuple[Decimal | None, str | None]:
    """Parse explicit monetary text; return None for ambiguous or absent values."""
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, Decimal):
        return (value, None) if value.is_finite() else (None, None)
    if isinstance(value, int):
        return Decimal(value), None
    if isinstance(value, float):
        # Spreadsheet numeric cells are decimal values; str avoids binary artifacts.
        amount = Decimal(str(value))
        return (amount, None) if amount.is_finite() else (None, None)

    text = str(value).strip().replace("\u00a0", " ")
    if not text or text in {"-", "—", "–"}:
        return None, normalize_currency(text)
    currency_match = _CURRENCY_TOKEN.search(text)
    currency = normalize_currency(currency_match.group(0)) if currency_match else None
    text = _CURRENCY_TOKEN.sub("", text).strip()
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    text = text.replace(" ", "").replace("'", "")
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    elif text.startswith("-"):
        negative = True
        text = text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", text):
        return None, currency

    # The last separator is decimal only when it has one or two trailing digits.
    separators = [index for index, char in enumerate(text) if char in ",."]
    if separators:
        last = separators[-1]
        trailing = len(text) - last - 1
        fraction = text[last + 1 :]
        if trailing in {1, 2}:
            integer = re.sub(r"[.,]", "", text[:last])
            normalized = integer + "." + fraction
        elif trailing > 3 and not fraction[2:].strip("0") and _is_grouped_integer(text[:last], text[last]):
            # Exports such as Stripe pad cent amounts to many decimals ("66.280000").
            # Accept them only when the extra digits are zeros, so no precision is lost.
            integer = re.sub(r"[.,]", "", text[:last])
            normalized = integer + "." + fraction[:2]
        elif all(len(text) - index - 1 == 3 for index in separators):
            normalized = text.replace(",", "").replace(".", "")
        else:
            return None, currency
    else:
        normalized = text
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None, currency
    return (-amount if negative else amount), currency


def _is_grouped_integer(integer: str, decimal_separator: str) -> bool:
    """True when the integer part uses only the other separator in groups of three."""
    if decimal_separator in integer:
        return False
    groups = re.split(r"[.,]", integer)
    return all(groups) and all(len(group) == 3 for group in groups[1:])


def extract_money_tokens(text: str) -> tuple[tuple[Decimal, str | None, str], ...]:
    """Extract unambiguous money tokens from a label/value string."""
    number = r"(?:\d{1,3}(?:[ .,'\u00a0]\d{3})+(?:[,.]\d{1,2})?|\d+(?:[,.]\d{1,2})?)"
    currency = r"(?:[A-Z]{3}|[€£¥$])"
    pattern = re.compile(
        rf"(?<![\w])(?P<token>\(?\s*[+-]?\s*(?:{currency}\s*)?{number}\s*\)?\s*(?:{currency})?)(?![\w])",
        re.IGNORECASE,
    )
    results: list[tuple[Decimal, str | None, str]] = []
    for match in pattern.finditer(text):
        raw = match.group("token").strip()
        amount, currency = parse_money(raw)
        if amount is not None:
            results.append((amount, currency, raw))
    return tuple(results)
