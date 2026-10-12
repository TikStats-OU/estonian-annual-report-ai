"""Conservative transaction processing over parsed source documents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from pathlib import Path

from estonian_annual_report_ai.accounting import (
    Account,
    AccountType,
    JournalEntry,
    JournalLine,
    Ledger,
)
from estonian_annual_report_ai.documents import BankTransaction, ParsedBankStatement


class ClassificationCategory(StrEnum):
    REVENUE = "revenue"
    OPERATING_EXPENSE = "operating_expense"
    BANK_FEE = "bank_fee"
    LOAN = "loan"
    OWNER_CONTRIBUTION = "owner_contribution"
    TAX = "tax"
    INTERNAL_TRANSFER = "internal_transfer"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class Classification:
    category: ClassificationCategory
    confidence: Decimal
    status: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedTransaction:
    transaction_id: str
    transaction_date: date | None
    amount: Decimal | None
    currency: str | None
    amount_eur: Decimal | None
    fx_rate_source: str | None
    counterparty: str | None
    description: str | None
    reference: str | None
    bank_account_id: str | None
    source: object
    duplicate_of: str | None = None
    possible_duplicate_of: str | None = None
    internal_transfer_id: str | None = None
    is_refund: bool = False
    classification: Classification = Classification(
        ClassificationCategory.UNCLASSIFIED, Decimal(0), "pending"
    )


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    bank_account_id: str | None
    currency: str | None
    opening_balance: Decimal | None
    calculated_closing_balance: Decimal | None
    reported_closing_balance: Decimal | None
    difference: Decimal | None
    status: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredRule:
    match_field: str
    match_value: str
    category: ClassificationCategory


class ClassificationRuleStore:
    """Small local-only exact-match rule store; writes owner-only JSON atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._rules: list[StoredRule] = []
        if path.exists():
            try:
                values = json.loads(path.read_text(encoding="utf-8"))
                self._rules = [
                    StoredRule(item["match_field"], item["match_value"], ClassificationCategory(item["category"]))
                    for item in values
                ]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ValueError(
                    "Classification rules file is unreadable; preserve and inspect it before replacing."
                ) from exc

    def approve(self, transaction: NormalizedTransaction, category: ClassificationCategory) -> None:
        field, value = _rule_key(transaction)
        rule = StoredRule(field, value, category)
        self._rules = [item for item in self._rules if (item.match_field, item.match_value) != (field, value)]
        self._rules.append(rule)
        self._persist()

    def find(self, transaction: NormalizedTransaction) -> ClassificationCategory | None:
        field, value = _rule_key(transaction)
        return next(
            (rule.category for rule in reversed(self._rules)
             if rule.match_field == field and rule.match_value == value),
            None,
        )

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="classification-rules-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    [{**asdict(rule), "category": rule.category.value} for rule in self._rules],
                    stream,
                    ensure_ascii=False,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def normalize_transactions(
    statements: Iterable[ParsedBankStatement],
    *,
    fx_rates: dict[tuple[str, date], tuple[Decimal, str]] | None = None,
) -> tuple[NormalizedTransaction, ...]:
    """Normalize parsed rows, preserve refs, mark duplicates and matched transfers.

    FX rates must be supplied as (EUR per unit, rate-source reference). Rates
    are never fetched or inferred by this module.
    """
    rates = {(currency.upper(), rate_date): value for (currency, rate_date), value in (fx_rates or {}).items()}
    result: list[NormalizedTransaction] = []
    fingerprints: dict[str, tuple[str, str | None, str | None]] = {}
    for statement in statements:
        for row in statement.transactions:
            amount = _signed_amount(row)
            currency = (row.currency or statement.currency or "").upper() or None
            amount_eur = amount if currency == "EUR" else None
            fx_rate_source = None
            rate_key = (currency.upper(), row.transaction_date) if currency and row.transaction_date else None
            if amount is not None and currency and currency != "EUR" and rate_key in rates:
                rate, rate_source = rates[rate_key]
                if rate > 0 and rate_source.strip():
                    with localcontext() as context:
                        context.prec = 50
                        amount_eur = (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    fx_rate_source = rate_source
                else:
                    amount_eur = None
            fingerprint_data = "|".join(
                (
                    row.transaction_date.isoformat() if row.transaction_date else "",
                    str(amount) if amount is not None else "",
                    currency or "",
                    _normalize_text(row.reference),
                    _normalize_text(row.counterparty),
                    _normalize_text(row.description),
                )
            )
            fingerprint = hashlib.sha256(fingerprint_data.encode("utf-8")).hexdigest()
            possible_match = fingerprints.get(fingerprint)
            duplicate_of = None
            possible_duplicate_of = None
            transaction_id = fingerprint
            if possible_match:
                prior_id, _, _ = possible_match
                possible_duplicate_of = prior_id
                transaction_id = hashlib.sha256(f"{fingerprint}:{row.source.document_id}:{row.source.page_number}:{row.source.row_number}:{row.source.sheet_name}".encode()).hexdigest()
            else:
                fingerprints[fingerprint] = (fingerprint, row.bank_account_id or statement.bank_account_id, row.reference)
            text = _normalize_text(f"{row.description or ''} {row.counterparty or ''}")
            result.append(
                NormalizedTransaction(
                    transaction_id=transaction_id,
                    transaction_date=row.transaction_date,
                    amount=amount,
                    currency=currency,
                    amount_eur=amount_eur,
                    fx_rate_source=fx_rate_source,
                    counterparty=row.counterparty,
                    description=row.description,
                    reference=row.reference,
                    bank_account_id=row.bank_account_id or statement.bank_account_id,
                    source=row.source,
                    duplicate_of=duplicate_of,
                    possible_duplicate_of=possible_duplicate_of,
                    is_refund=bool(re.search(r"\b(refund|refunded|chargeback|reversal|reversed)\b", text)),
                )
            )
    return _identify_internal_transfers(tuple(result))


def classify_transactions(
    transactions: Iterable[NormalizedTransaction],
    *,
    rule_store: ClassificationRuleStore | None = None,
) -> tuple[NormalizedTransaction, ...]:
    rows = tuple(transactions)
    result: list[NormalizedTransaction] = []
    for row in rows:
        if row.duplicate_of:
            result.append(replace(row, classification=Classification(
                ClassificationCategory.UNCLASSIFIED, Decimal("1.00"), "duplicate", ("Duplicate fingerprint",)
            )))
            continue
        if row.possible_duplicate_of:
            result.append(replace(row, classification=Classification(
                ClassificationCategory.UNCLASSIFIED, Decimal("0.50"), "needs_review", ("Possible duplicate; original retained pending review",)
            )))
            continue
        if row.internal_transfer_id:
            saved = rule_store.find(row) if rule_store else None
            if saved == ClassificationCategory.INTERNAL_TRANSFER:
                result.append(replace(row, classification=Classification(
                    ClassificationCategory.INTERNAL_TRANSFER, Decimal("1.00"), "user_confirmed", ("User confirmed matched internal transfer",)
                )))
            else:
                result.append(replace(row, classification=Classification(
                    ClassificationCategory.UNCLASSIFIED, Decimal("0.50"), "needs_review", ("Possible internal transfer; user confirmation required",)
                )))
            continue
        saved = rule_store.find(row) if rule_store else None
        if saved:
            result.append(replace(row, classification=Classification(saved, Decimal("1.00"), "user_confirmed")))
            continue
        result.append(replace(row, classification=_classify_rule(row)))
    return tuple(result)


def build_ledger(
    transactions: Iterable[NormalizedTransaction],
    *,
    period_start: date,
    period_end: date,
) -> tuple[Ledger, tuple[str, ...]]:
    """Create double-entry ledger rows only for approved, EUR-valued classifications."""
    accounts = (
        Account("1000", "Bank", AccountType.ASSET),
        Account("2000", "Loans payable", AccountType.LIABILITY),
        Account("2200", "Payables to owner and related parties", AccountType.LIABILITY),
        Account("3000", "Share capital and owner contributions", AccountType.EQUITY),
        Account("4000", "Revenue", AccountType.REVENUE),
        Account("6000", "Operating expenses", AccountType.EXPENSE),
        Account("6100", "Bank fees", AccountType.EXPENSE),
        Account("6200", "Taxes and statutory charges", AccountType.EXPENSE),
    )
    ledger = Ledger(accounts)
    issues: list[str] = []
    used_ids: set[str] = set()
    for row in transactions:
        if row.duplicate_of or row.transaction_id in used_ids:
            continue
        if row.transaction_date is None or not period_start <= row.transaction_date <= period_end:
            continue
        used_ids.add(row.transaction_id)
        if row.classification.category == ClassificationCategory.UNCLASSIFIED:
            issues.append(f"Unclassified transaction requires review: {row.transaction_id}.")
            continue
        if row.classification.category == ClassificationCategory.INTERNAL_TRANSFER:
            continue
        if row.classification.status not in ("rule", "user_confirmed"):
            issues.append(f"Transaction classification requires confirmation: {row.transaction_id}.")
            continue
        if row.amount_eur is None:
            issues.append(f"Transaction needs an approved EUR conversion rate: {row.transaction_id}.")
            continue
        if row.transaction_date is None or row.amount is None:
            issues.append(f"Transaction is missing date or amount: {row.transaction_id}.")
            continue
        category = row.classification.category
        signed_amount = row.amount_eur
        if (
            category in (
                ClassificationCategory.REVENUE,
                ClassificationCategory.BANK_FEE,
                ClassificationCategory.OPERATING_EXPENSE,
                ClassificationCategory.TAX,
                ClassificationCategory.OWNER_CONTRIBUTION,
                ClassificationCategory.LOAN,
            )
            and signed_amount == 0
        ):
            issues.append(f"Zero-amount transaction omitted: {row.transaction_id}.")
            continue
        if category == ClassificationCategory.REVENUE and signed_amount > 0:
            debit, credit = "1000", "4000"
        elif category == ClassificationCategory.OPERATING_EXPENSE and signed_amount < 0:
            debit, credit = "6000", "1000"
        elif category == ClassificationCategory.BANK_FEE and signed_amount < 0:
            debit, credit = "6100", "1000"
        elif category == ClassificationCategory.TAX and signed_amount < 0:
            debit, credit = "6200", "1000"
        elif category == ClassificationCategory.OWNER_CONTRIBUTION and signed_amount > 0:
            debit, credit = "1000", "3000"
        elif category == ClassificationCategory.LOAN:
            debit, credit = (("1000", "2000") if signed_amount > 0 else ("2000", "1000"))
        else:
            issues.append(f"Classification and bank direction conflict: {row.transaction_id}.")
            continue
        amount = abs(signed_amount)
        source = row.source
        reference = f"{source.document_id}:{source.page_number or ''}:{source.row_number or ''}:{source.sheet_name or ''}"
        ledger.add_entry(JournalEntry(
            entry_id=row.transaction_id,
            entry_date=row.transaction_date,
            description=row.description or row.counterparty or category.value,
            source_reference=reference,
            lines=(
                JournalLine(debit, debit=amount),
                JournalLine(credit, credit=amount),
            ),
        ))
    return ledger, tuple(issues)


def reconcile_statements(
    statements: Iterable[ParsedBankStatement],
) -> tuple[ReconciliationResult, ...]:
    """Compare each statement's opening + signed movements against closing."""
    results = []
    for statement in statements:
        signed: list[Decimal] = []
        issues: list[str] = []
        for row in statement.transactions:
            amount = _signed_amount(row)
            if amount is None:
                issues.append(f"Ambiguous amount or direction at {row.source.filename}.")
            else:
                signed.append(amount)
        if statement.opening_balance is None:
            issues.append("Opening balance is missing.")
        elif any(issue.code == "derived_opening_balance" for issue in statement.issues):
            issues.append("Opening balance is derived from transaction running balances and needs confirmation.")
        if statement.closing_balance is None:
            issues.append("Closing balance is missing.")
        if not statement.currency:
            issues.append("Statement currency is missing or conflicting.")
        if not statement.bank_account_id:
            issues.append("Bank account identifier is missing.")
        calculated = None
        difference = None
        if statement.opening_balance is not None and not any("Ambiguous amount" in item for item in issues):
            calculated = _exact_sum((statement.opening_balance, *signed))
            if statement.closing_balance is not None:
                difference = _exact_sum((calculated, -statement.closing_balance))
                if difference != 0:
                    issues.append("Calculated closing balance differs from the statement closing balance.")
        status = "reconciled" if not issues else "unresolved"
        results.append(ReconciliationResult(
            bank_account_id=statement.bank_account_id,
            currency=statement.currency,
            opening_balance=statement.opening_balance,
            calculated_closing_balance=calculated,
            reported_closing_balance=statement.closing_balance,
            difference=difference,
            status=status,
            issues=tuple(issues),
        ))
    return tuple(results)


def _signed_amount(row: BankTransaction) -> Decimal | None:
    if row.amount is None:
        return None
    if row.direction == "debit":
        return -abs(row.amount)
    if row.direction == "credit":
        return abs(row.amount)
    if row.amount < 0:
        return row.amount
    return None


def _classify_rule(row: NormalizedTransaction) -> Classification:
    text = _normalize_text(f"{row.description or ''} {row.counterparty or ''}")
    amount = row.amount
    if amount is None or row.currency is None:
        return Classification(ClassificationCategory.UNCLASSIFIED, Decimal(0), "needs_review", ("Missing amount or currency",))
    if row.is_refund:
        return Classification(ClassificationCategory.UNCLASSIFIED, Decimal("0.80"), "needs_review", ("Refund/reversal detected; original accounting treatment required",))
    if re.search(r"\b(bank fee|bank charge|service fee|monthly fee|commission|teenustasu|panga teenustasu)\b", text):
        return Classification(ClassificationCategory.BANK_FEE, Decimal("0.98"), "rule", ("Explicit bank fee wording",))
    if re.search(r"\b(vat|tax|maksuamet|emta|tax authority|social tax|income tax|kaibemaks)\b", text):
        return Classification(ClassificationCategory.UNCLASSIFIED, Decimal("0.90"), "needs_review", ("Tax payment requires a liability/expense breakdown",))
    if re.search(r"\b(interest|intress|loan repayment|laenu tagastus|loan payment|laenumakse)\b", text):
        return Classification(ClassificationCategory.UNCLASSIFIED, Decimal("0.88"), "needs_review", ("Loan principal and interest need separate accounting treatment",))
    if re.search(r"\b(loan|laen)\b", text):
        return Classification(ClassificationCategory.UNCLASSIFIED, Decimal("0.80"), "needs_review", ("Loan proceeds or repayment need a user-confirmed direction",))
    if re.search(r"\b(owner contribution|share capital|capital contribution|osaniku sissemakse|osakapital)\b", text):
        return Classification(ClassificationCategory.OWNER_CONTRIBUTION, Decimal("0.92"), "rule", ("Explicit owner/capital wording",))
    if amount > 0 and re.search(r"\b(invoice|sale|subscription|service revenue|customer payment|müügiarve|müük)\b", text):
        return Classification(ClassificationCategory.REVENUE, Decimal("0.82"), "rule", ("Incoming transaction with explicit sales wording",))
    if amount < 0 and re.search(r"\b(invoice|supplier|hosting|software|rent|utilities|office|arve|tarnija)\b", text):
        return Classification(ClassificationCategory.OPERATING_EXPENSE, Decimal("0.78"), "rule", ("Outgoing transaction with explicit expense wording",))
    return Classification(ClassificationCategory.UNCLASSIFIED, Decimal("0.20"), "needs_review", ("No sufficiently specific local rule matched",))


def _identify_internal_transfers(rows: tuple[NormalizedTransaction, ...]) -> tuple[NormalizedTransaction, ...]:
    mutable = list(rows)
    matched: set[int] = set()
    for left_index, left in enumerate(rows):
        if left.duplicate_of or left.amount is None or left.amount == 0 or left.internal_transfer_id:
            continue
        if left_index in matched:
            continue
        for right_index in range(left_index + 1, len(mutable)):
            right = mutable[right_index]
            if right.duplicate_of or right.amount is None or right.amount == 0 or right.internal_transfer_id:
                continue
            if right_index in matched:
                continue
            if left.bank_account_id == right.bank_account_id or left.currency != right.currency:
                continue
            if left.amount != -right.amount:
                continue
            left_text = _normalize_text(left.description)
            right_text = _normalize_text(right.description)
            explicit_transfer = bool(re.search(r"\b(transfer|internal transfer|own account|between accounts|account transfer|overkanne|konto(devaheline)? ylekanne)\b", left_text)) and bool(
                re.search(r"\b(transfer|internal transfer|own account|between accounts|account transfer|overkanne|konto(devaheline)? ylekanne)\b", right_text)
            )
            same_reference = bool(left.reference and right.reference and _normalize_text(left.reference) == _normalize_text(right.reference))
            if not (explicit_transfer or same_reference):
                continue
            if left.transaction_date and right.transaction_date:
                if abs((left.transaction_date - right.transaction_date).days) > 3:
                    continue
            elif not same_reference:
                continue
            transfer_id = hashlib.sha256(f"{left.transaction_id}:{right.transaction_id}".encode()).hexdigest()
            mutable[left_index] = replace(
                mutable[left_index],
                internal_transfer_id=transfer_id,
                classification=Classification(
                    ClassificationCategory.UNCLASSIFIED,
                    Decimal("0.50"),
                    "needs_review",
                    ("Possible internal transfer; user confirmation required",),
                ),
            )
            mutable[right_index] = replace(
                mutable[right_index],
                internal_transfer_id=transfer_id,
                classification=Classification(
                    ClassificationCategory.UNCLASSIFIED,
                    Decimal("0.50"),
                    "needs_review",
                    ("Possible internal transfer; user confirmation required",),
                ),
            )
            matched.update((left_index, right_index))
            break
    return tuple(mutable)


def _rule_key(row: NormalizedTransaction) -> tuple[str, str]:
    match_field = "counterparty" if row.counterparty else "description"
    match_value = _normalize_text(row.counterparty or row.description)
    exact_amount = str(row.amount) if row.amount is not None else "unknown"
    currency = row.currency or "unknown"
    return match_field, f"{match_value}|{exact_amount}|{currency}"


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def _exact_sum(values: Iterable[Decimal]) -> Decimal:
    """Sum arbitrary-currency Decimal values without ambient-context rounding."""
    items = tuple(values)
    exponent = min((value.as_tuple().exponent for value in items), default=0)
    total = 0
    for value in items:
        sign, digits, value_exponent = value.as_tuple()
        coefficient = int("".join(map(str, digits)) or "0")
        scaled = coefficient * 10 ** (value_exponent - exponent)
        total += -scaled if sign else scaled
    sign = int(total < 0)
    digits = tuple(int(char) for char in str(abs(total))) or (0,)
    return Decimal((sign, digits, exponent))
