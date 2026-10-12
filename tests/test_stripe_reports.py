"""Synthetic-only tests for deterministic Stripe CSV parsing."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from estonian_annual_report_ai.documents.common import DocumentValidationError
from estonian_annual_report_ai.documents.stripe_reports import (
    parse_stripe_report,
    reconcile_stripe_summary,
)


class StripeReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_balance_summary_parses_decimal_values_period_and_preamble(self) -> None:
        path = self.directory / "synthetic-balance-summary.csv"
        path.write_text(
            '"Stripe Balance Summary","2025-01-01 to 2025-12-31"\n'
            'Period Start,Period End,Opening Balance (EUR),Gross Activity (EUR),Fees (EUR),'
            'Net Activity (EUR),Payouts (EUR),Closing Balance (EUR),Currency\n'
            "2025-01-01,2025-12-31,10.00,257.00,-12.00,245.00,-245.00,10.00,EUR\n",
            encoding="utf-8",
        )

        report = parse_stripe_report(path)

        self.assertEqual(report.kind, "balance_summary")
        self.assertEqual(report.period_start, date(2025, 1, 1))
        self.assertEqual(report.period_end, date(2025, 12, 31))
        self.assertEqual(report.currencies, ("EUR",))
        self.assertEqual(len(report.preamble_rows), 1)
        row = report.rows[0]
        self.assertEqual(row.decimal("Opening Balance (EUR)"), Decimal("10.00"))
        self.assertEqual(row.decimal("Gross Activity (EUR)"), Decimal("257.00"))
        self.assertEqual(row.get("Currency"), "EUR")
        self.assertEqual(row.source.row_number, 3)

    def test_stripe_category_balance_summary_layout_is_recognized(self) -> None:
        path = self.directory / "synthetic-category-balance-summary.csv"
        path.write_text(
            "category,description,net_amount,currency\n"
            "starting_balance,As of 2025-01-01,10.00,EUR\n"
            "activity_gross,Activity for 2025-01-01 to 2025-12-31,257.00,EUR\n"
            "activity_fee,Fees for period,-12.00,EUR\n"
            "activity,Net activity for period,245.00,EUR\n"
            "payouts_gross,Payouts,-245.00,EUR\n"
            "payouts_fee,Fees,0.00,EUR\n"
            "payouts,Net payouts,-245.00,EUR\n"
            "ending_balance,As of 2025-12-31,10.00,EUR\n",
            encoding="utf-8",
        )

        report = parse_stripe_report(path)

        self.assertEqual(report.kind, "balance_summary")
        self.assertEqual(report.period_start, date(2025, 1, 1))
        self.assertEqual(report.period_end, date(2025, 12, 31))
        self.assertEqual(report.currencies, ("EUR",))
        self.assertEqual(report.rows[0].get("category"), "starting_balance")
        self.assertEqual(report.rows[0].decimal("net_amount"), Decimal("10.00"))
        self.assertEqual(report.rows[-1].decimal("net_amount"), Decimal("10.00"))

    def test_fees_summary_preserves_unknown_missing_fields_and_sha_duplicates(self) -> None:
        path = self.directory / "synthetic-all-fees.csv"
        path.write_text(
            "Fee Type,Fee Amount (EUR),Currency,Invoice Status,Unmapped Note\n"
            "Card processing,5.00,EUR,NOT_INVOICED,provider fee\n"
            "Dispute evidence,2.00,EUR,NOT_INVOICED,\n"
            "Card processing,5.00,EUR,NOT_INVOICED,provider fee\n",
            encoding="utf-8",
        )

        report = parse_stripe_report(path)

        self.assertEqual(report.kind, "fees_summary")
        self.assertEqual(report.rows[0].decimal("Fee Amount (EUR)"), Decimal("5.00"))
        self.assertEqual(report.rows[1].get("Unmapped Note"), None)
        self.assertEqual(report.rows[2].identity_sha256, report.rows[0].identity_sha256)
        self.assertEqual(report.rows[2].duplicate_of.row_number, 2)
        self.assertEqual(report.rows[0].source.row_number, 2)

    def test_stripe_feature_fee_summary_layout_parses_amount_and_tax(self) -> None:
        path = self.directory / "synthetic-feature-fees.csv"
        path.write_text(
            "feature_name,feature_description,amount,tax,currency,billing_status\n"
            "Payments,Card processing,5.00,0.00,EUR,NOT_INVOICED\n"
            "Billing,Monthly subscription billing,1.00,0.00,EUR,NOT_INVOICED\n"
            "Disputes,Evidence submission,2.00,0.00,EUR,NOT_INVOICED\n",
            encoding="utf-8",
        )

        report = parse_stripe_report(path)

        self.assertEqual(report.kind, "fees_summary")
        self.assertEqual(report.currencies, ("EUR",))
        self.assertEqual(report.rows[0].get("feature_name"), "Payments")
        self.assertEqual(report.rows[0].get("feature_description"), "Card processing")
        self.assertEqual(report.rows[0].decimal("amount"), Decimal("5.00"))
        self.assertEqual(report.rows[0].decimal("tax"), Decimal("0.00"))
        self.assertTrue(all(row.duplicate_of is None for row in report.rows))

    def test_itemized_balance_activity_parses_dates_money_and_extra_cells(self) -> None:
        path = self.directory / "synthetic-balance-activity.csv"
        path.write_text(
            "id,created,type,gross,fee,net,currency,Reporting Category\n"
            "txn_1,2025-11-27T10:15:00Z,payment,100.00,-3.20,96.80,EUR,charge,retained extra\n"
            "txn_2,2025-12-15T12:00:00Z,payout,-96.80,0.00,-96.80,EUR,payout\n",
            encoding="utf-8",
        )

        report = parse_stripe_report(path)

        self.assertEqual(report.kind, "balance_transactions")
        self.assertIsNone(report.period_start)
        self.assertIsNone(report.period_end)
        first, second = report.rows
        self.assertEqual(first.decimal("gross"), Decimal("100.00"))
        self.assertEqual(first.date_fields[0][1], date(2025, 11, 27))
        self.assertEqual(first.get("__extra_1"), "retained extra")
        self.assertIsNone(second.get("__extra_1"))
        self.assertEqual(first.get("Reporting Category"), "charge")
        self.assertEqual(first.source.row_number, 2)

    def test_reconciliation_checks_balance_equation_and_fee_magnitude(self) -> None:
        result = reconcile_stripe_summary(
            Decimal("10.00"),
            Decimal("257.00"),
            Decimal("-12.00"),
            Decimal("-245.00"),
            Decimal("10.00"),
            Decimal("12.00"),
        )

        self.assertEqual(result.calculated_closing_balance, Decimal("10.00"))
        self.assertEqual(result.balance_difference, Decimal("0.00"))
        self.assertTrue(result.balance_reconciled)
        self.assertEqual(result.fee_difference, Decimal("0.00"))
        self.assertTrue(result.fees_reconciled)

    def test_missing_values_remain_unresolved_and_balance_is_not_inferred(self) -> None:
        result = reconcile_stripe_summary(
            Decimal("10.00"), None, Decimal("-1.00"), None, Decimal("4.52"), Decimal("1.00")
        )

        self.assertIsNone(result.calculated_closing_balance)
        self.assertIsNone(result.balance_reconciled)
        self.assertTrue(result.fees_reconciled)

    def test_non_csv_documents_are_rejected(self) -> None:
        path = self.directory / "synthetic-report.txt"
        path.write_text("synthetic", encoding="utf-8")
        with self.assertRaises(DocumentValidationError):
            parse_stripe_report(path)


if __name__ == "__main__":
    unittest.main()
