"""Generate sample statements from synthetic, fully balanced journal entries."""

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from enum import Enum

from estonian_annual_report_ai.accounting import (
    Account,
    AccountType,
    JournalEntry,
    JournalLine,
    Ledger,
    balance_sheet,
    income_statement,
    trial_balance,
    validate_annual_report,
)


def json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Cannot serialize {type(value).__name__}.")


def build_synthetic_ledger() -> Ledger:
    accounts = (
        Account("1000", "Bank", AccountType.ASSET),
        Account("1500", "Equipment", AccountType.ASSET),
        Account("3000", "Share capital", AccountType.EQUITY),
        Account("3100", "Retained earnings", AccountType.EQUITY),
        Account("4000", "Service revenue", AccountType.REVENUE),
        Account("6000", "Operating expenses", AccountType.EXPENSE),
        Account("6100", "Bank fees", AccountType.EXPENSE),
    )
    ledger = Ledger(accounts)
    ledger.add_entry(
        JournalEntry(
            entry_id="SYN-OPEN-001",
            entry_date=date(2025, 1, 1),
            description="Synthetic opening balances",
            source_reference="synthetic:opening",
            lines=(
                JournalLine("1000", debit=Decimal("5000.00")),
                JournalLine("3000", credit=Decimal("4000.00")),
                JournalLine("3100", credit=Decimal("1000.00")),
            ),
        )
    )
    ledger.add_entry(
        JournalEntry(
            entry_id="SYN-REV-001",
            entry_date=date(2025, 3, 15),
            description="Synthetic service receipt",
            source_reference="synthetic:receipt-001",
            lines=(
                JournalLine("1000", debit=Decimal("2500.00")),
                JournalLine("4000", credit=Decimal("2500.00")),
            ),
        )
    )
    ledger.add_entry(
        JournalEntry(
            entry_id="SYN-EXP-001",
            entry_date=date(2025, 6, 30),
            description="Synthetic operating expense",
            source_reference="synthetic:expense-001",
            lines=(
                JournalLine("6000", debit=Decimal("800.00")),
                JournalLine("1000", credit=Decimal("800.00")),
            ),
        )
    )
    ledger.add_entry(
        JournalEntry(
            entry_id="SYN-FEE-001",
            entry_date=date(2025, 12, 30),
            description="Synthetic bank fee",
            source_reference="synthetic:bank-fee-001",
            lines=(
                JournalLine("6100", debit=Decimal("15.00")),
                JournalLine("1000", credit=Decimal("15.00")),
            ),
        )
    )
    ledger.add_entry(
        JournalEntry(
            entry_id="SYN-ASSET-001",
            entry_date=date(2025, 12, 31),
            description="Synthetic equipment purchase",
            source_reference="synthetic:asset-001",
            lines=(
                JournalLine("1500", debit=Decimal("400.00")),
                JournalLine("1000", credit=Decimal("400.00")),
            ),
        )
    )
    return ledger


def main() -> None:
    ledger = build_synthetic_ledger()
    start = date(2025, 1, 1)
    end = date(2025, 12, 31)
    payload = {
        "synthetic_data": True,
        "trial_balance": asdict(trial_balance(ledger, end)),
        "balance_sheet": asdict(balance_sheet(ledger, end, start)),
        "income_statement": asdict(income_statement(ledger, start, end)),
        "validation": asdict(validate_annual_report(ledger, start, end)),
    }
    print(json.dumps(payload, default=json_value, indent=2))


if __name__ == "__main__":
    main()
