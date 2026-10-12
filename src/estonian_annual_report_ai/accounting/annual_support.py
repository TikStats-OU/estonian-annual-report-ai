"""Small deterministic helpers for annual-report accounting workpapers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from estonian_annual_report_ai.transactions.workflow import NormalizedTransaction

from .models import JournalEntry, JournalLine


def recognize_subscription_revenue(
    amount: Decimal,
    service_start: date,
    service_end: date,
    period_start: date,
    period_end: date,
) -> dict[str, Decimal]:
    """Allocate by overlapping days.

    Both ranges are start-inclusive and end-exclusive: pass the day after the
    last day, e.g. ``date(2026, 1, 1)`` as ``period_end`` for calendar 2025.
    """
    if amount < 0:
        raise ValueError("Subscription amount cannot be negative.")
    if service_end <= service_start or period_end <= period_start:
        raise ValueError("Date ranges must be ordered.")
    total_days = (service_end - service_start).days
    covered_start = max(service_start, period_start)
    covered_end = min(service_end, period_end)
    covered_days = max(0, (covered_end - covered_start).days)
    recognized = (amount * covered_days / total_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"recognized_revenue": recognized, "deferred_revenue": amount - recognized}


def depreciation_scenarios(
    opening_nbv: Decimal,
    remaining_useful_lives: Iterable[int],
) -> tuple[dict[str, Decimal | int], ...]:
    """Return sensitivities only; callers must not treat them as posted entries."""
    if opening_nbv < 0:
        raise ValueError("Opening net book value cannot be negative.")
    scenarios = []
    for years in remaining_useful_lives:
        if years <= 0:
            raise ValueError("Remaining useful life must be positive.")
        annual = (opening_nbv / years).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        expense = min(opening_nbv, annual)
        scenarios.append({"remaining_useful_life_years": years, "depreciation_expense": expense,
                          "closing_nbv": opening_nbv - expense})
    return tuple(scenarios)


def owner_funded_expense_entry(
    *, entry_id: str, entry_date: date, amount_eur: Decimal, description: str,
    source_reference: str, expense_account: str = "6000", liability_account: str = "2200",
) -> JournalEntry:
    """Book a verified personal payment as expense payable to owner, never equity."""
    if amount_eur <= 0 or not source_reference.strip():
        raise ValueError("A positive EUR amount and source reference are required.")
    return JournalEntry(
        entry_id=entry_id, entry_date=entry_date, description=description,
        source_reference=source_reference,
        lines=(JournalLine(expense_account, debit=amount_eur),
               JournalLine(liability_account, credit=amount_eur)),
    )


def cross_currency_reference_groups(
    transactions: Iterable[NormalizedTransaction],
) -> tuple[tuple[NormalizedTransaction, ...], ...]:
    """Group same-date rows sharing a source reference across currencies, without dropping rows."""
    grouped: dict[tuple[date | None, str], list[NormalizedTransaction]] = defaultdict(list)
    for row in transactions:
        if row.reference and row.currency:
            grouped[(row.transaction_date, row.reference)].append(row)
    return tuple(
        tuple(rows) for rows in grouped.values()
        if len({row.currency for row in rows}) > 1
    )
