"""Synthetic checks for the annual-report workpaper helpers."""

import unittest
from datetime import date
from decimal import Decimal

from estonian_annual_report_ai.accounting.annual_support import (
    cross_currency_reference_groups,
    depreciation_scenarios,
    owner_funded_expense_entry,
    recognize_subscription_revenue,
)
from estonian_annual_report_ai.transactions.workflow import NormalizedTransaction


class AnnualSupportTests(unittest.TestCase):
    def test_cross_currency_shared_reference_is_grouped_without_dropping_rows(self) -> None:
        shared = [
            NormalizedTransaction(f"id-{currency}", date(2025, 5, 1), Decimal(-1), currency,
                                  None, None, "merchant", "purchase", "card-1", None, None)
            for currency in ("EUR", "USD")
        ]
        groups = cross_currency_reference_groups(shared)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_subscription_revenue_allocates_only_service_days_in_period(self) -> None:
        result = recognize_subscription_revenue(
            Decimal("30.00"), date(2025, 12, 16), date(2026, 1, 15),
            date(2025, 1, 1), date(2026, 1, 1),
        )
        self.assertEqual(
            result, {"recognized_revenue": Decimal("16.00"), "deferred_revenue": Decimal("14.00")}
        )

    def test_depreciation_scenarios_do_not_exceed_opening_value(self) -> None:
        rows = depreciation_scenarios(Decimal("240.00"), (1, 2, 3))
        self.assertEqual(
            [row["depreciation_expense"] for row in rows],
            [Decimal("240.00"), Decimal("120.00"), Decimal("80.00")],
        )
        self.assertEqual(rows[-1]["closing_nbv"], Decimal("160.00"))

    def test_personally_paid_expense_creates_owner_payable_not_equity(self) -> None:
        entry = owner_funded_expense_entry(
            entry_id="owner-expense-1", entry_date=date(2025, 1, 2),
            amount_eur=Decimal("12.34"), description="Business software subscription",
            source_reference="paypal-row-1",
        )
        self.assertEqual(entry.lines[0].account_code, "6000")
        self.assertEqual(entry.lines[0].debit, Decimal("12.34"))
        self.assertEqual(entry.lines[1].account_code, "2200")
        self.assertEqual(entry.lines[1].credit, Decimal("12.34"))


if __name__ == "__main__":
    unittest.main()
