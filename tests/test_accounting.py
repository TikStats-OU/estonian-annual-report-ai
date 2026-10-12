"""Focused tests for the deterministic accounting MVP."""

import unittest
from datetime import date
from decimal import Decimal, localcontext

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
from examples.synthetic_demo import build_synthetic_ledger


class JournalEntryTests(unittest.TestCase):
    def test_rejects_unbalanced_entry(self) -> None:
        with self.assertRaisesRegex(ValueError, "not balanced"):
            JournalEntry(
                "bad",
                date(2025, 1, 1),
                "Unbalanced synthetic entry",
                (
                    JournalLine("bank", debit=Decimal(10)),
                    JournalLine("revenue", credit=Decimal(9)),
                ),
            )

    def test_rejects_float_amount(self) -> None:
        with self.assertRaisesRegex(TypeError, "must be a Decimal"):
            JournalLine("bank", debit=10.25)  # type: ignore[arg-type]

    def test_rejects_line_with_both_sides(self) -> None:
        with self.assertRaisesRegex(ValueError, "not both"):
            JournalLine(
                "bank", debit=Decimal(1), credit=Decimal(1)
            )

    def test_rejects_amount_with_sub_cent_precision(self) -> None:
        with self.assertRaisesRegex(ValueError, "two decimal places"):
            JournalLine("bank", debit=Decimal("1.001"))

    def test_entry_balance_is_independent_of_decimal_context(self) -> None:
        with localcontext() as context:
            context.prec = 2
            with self.assertRaisesRegex(ValueError, "not balanced"):
                JournalEntry(
                    "context-rounding",
                    date(2025, 1, 1),
                    "Must compare exact cents",
                    (
                        JournalLine("bank", debit=Decimal("100.00")),
                        JournalLine("capital", credit=Decimal("99.99")),
                    ),
                )


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = Ledger(
            (
                Account("bank", "Bank", AccountType.ASSET),
                Account("capital", "Capital", AccountType.EQUITY),
            )
        )
        self.entry = JournalEntry(
            "opening",
            date(2025, 1, 1),
            "Synthetic opening balances",
            (
                JournalLine("bank", debit=Decimal(100)),
                JournalLine("capital", credit=Decimal(100)),
            ),
        )

    def test_rejects_unknown_account(self) -> None:
        bad_entry = JournalEntry(
            "unknown",
            date(2025, 1, 1),
            "Unknown account",
            (
                JournalLine("bank", debit=Decimal(100)),
                JournalLine("missing", credit=Decimal(100)),
            ),
        )
        with self.assertRaisesRegex(ValueError, "Unknown account"):
            self.ledger.add_entry(bad_entry)

    def test_rejects_duplicate_entry_id(self) -> None:
        self.ledger.add_entry(self.entry)
        with self.assertRaisesRegex(ValueError, "Duplicate journal entry ID"):
            self.ledger.add_entry(self.entry)


class StatementTests(unittest.TestCase):
    def test_synthetic_statements_are_deterministic_and_balanced(self) -> None:
        ledger = build_synthetic_ledger()
        period_start = date(2025, 1, 1)
        period_end = date(2025, 12, 31)

        balance = balance_sheet(ledger, period_end, period_start)
        income = income_statement(ledger, period_start, period_end)
        trial = trial_balance(ledger, period_end)
        validation = validate_annual_report(ledger, period_start, period_end)

        self.assertEqual(income.total_revenue, Decimal("2500.00"))
        self.assertEqual(income.total_expenses, Decimal("815.00"))
        self.assertEqual(income.net_profit, Decimal("1685.00"))
        self.assertEqual(balance.total_assets, Decimal("6685.00"))
        self.assertEqual(balance.total_liabilities, Decimal(0))
        self.assertEqual(balance.total_equity, Decimal("6685.00"))
        self.assertTrue(balance.is_balanced)
        self.assertEqual(trial.total_debits, trial.total_credits)
        self.assertTrue(validation.is_valid)
        self.assertEqual(validation.issues, ())

    def test_trial_balance_respects_cutoff_date(self) -> None:
        ledger = build_synthetic_ledger()
        trial = trial_balance(ledger, date(2025, 3, 15))
        bank = next(line for line in trial.lines if line.account_code == "1000")
        self.assertEqual(bank.debit_balance, Decimal("7500.00"))

    def test_balance_sheet_detects_unclosed_prior_period_result(self) -> None:
        ledger = Ledger(
            (
                Account("bank", "Bank", AccountType.ASSET),
                Account("revenue", "Revenue", AccountType.REVENUE),
            )
        )
        ledger.add_entry(
            JournalEntry(
                "prior-revenue",
                date(2024, 12, 31),
                "Prior period revenue not closed",
                (
                    JournalLine("bank", debit=Decimal(20)),
                    JournalLine("revenue", credit=Decimal(20)),
                ),
            )
        )
        report = validate_annual_report(ledger, date(2025, 1, 1), date(2025, 12, 31))
        self.assertFalse(report.is_valid)
        self.assertEqual(report.issues[0].code, "balance_sheet_out_of_balance")

    def test_closing_entry_does_not_erase_current_period_profit(self) -> None:
        ledger = Ledger(
            (
                Account("bank", "Bank", AccountType.ASSET),
                Account("revenue", "Revenue", AccountType.REVENUE),
                Account("retained", "Retained earnings", AccountType.EQUITY),
            )
        )
        ledger.add_entry(
            JournalEntry(
                "sale", date(2025, 6, 1), "Synthetic sale",
                (JournalLine("bank", debit=Decimal("100.00")),
                 JournalLine("revenue", credit=Decimal("100.00"))),
            )
        )
        ledger.add_entry(
            JournalEntry(
                "close", date(2025, 12, 31), "Close current-year revenue",
                (JournalLine("revenue", debit=Decimal("100.00")),
                 JournalLine("retained", credit=Decimal("100.00"))),
                is_closing_entry=True,
            )
        )
        income = income_statement(ledger, date(2025, 1, 1), date(2025, 12, 31))
        balance = balance_sheet(ledger, date(2025, 12, 31), date(2025, 1, 1))
        self.assertEqual(income.total_revenue, Decimal("100.00"))
        self.assertEqual(income.net_profit, Decimal("100.00"))
        self.assertEqual(balance.total_equity, Decimal("100.00"))
        self.assertTrue(balance.is_balanced)

    def test_rejects_reversed_reporting_period(self) -> None:
        with self.assertRaisesRegex(ValueError, "earlier than"):
            income_statement(
                build_synthetic_ledger(), date(2025, 12, 31), date(2025, 1, 1)
            )

        report = validate_annual_report(
            build_synthetic_ledger(), date(2025, 12, 31), date(2025, 1, 1)
        )
        self.assertFalse(report.is_valid)
        self.assertEqual(report.issues[0].code, "invalid_reporting_period")


if __name__ == "__main__":
    unittest.main()
