"""Synthetic-only Stripe report checklist and reconciliation workflow tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from estonian_annual_report_ai.application import (
    AccountingWorkflow,
    DocumentInput,
    DocumentKind,
)
from estonian_annual_report_ai.stripe_assistant import StripeReportingAssistant


class StripeAssistantTests(unittest.TestCase):
    def test_basic_dashboard_checklist_guides_missing_reports_without_sigma(self) -> None:
        assistant = StripeReportingAssistant(
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
        )

        checklist = assistant.missing_reports()
        instructions = assistant.download_instructions()

        self.assertEqual(
            {item["report_type"] for item in checklist["missing_or_unverified"]},
            {"balance_summary", "fees_summary", "balance_transactions"},
        )
        self.assertTrue(all(item["sigma_required"] == "No." for item in instructions["instructions"]))
        self.assertEqual(len(instructions["instructions"]), 3)

    def test_synthetic_reports_and_bank_flow_reconcile_without_inventing_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            balance = root / "synthetic-balance-summary.csv"
            balance.write_text(
                "category,description,net_amount,currency\n"
                "starting_balance,Starting balance (2025-01-01),10.00,EUR\n"
                "activity_gross,Account activity before fees,257.00,EUR\n"
                "activity_fee,Less fees,-12.00,EUR\n"
                "activity,Activity,245.00,EUR\n"
                "payouts_gross,Payouts to bank,-245.00,EUR\n"
                "payouts_fee,Payout fees,0.00,EUR\n"
                "payouts,Total payouts,-245.00,EUR\n"
                "ending_balance,Ending balance (2025-12-31),10.00,EUR\n",
                encoding="utf-8",
            )
            fees = root / "synthetic-fees-summary.csv"
            fees.write_text(
                '"Report period","2025-01-01 to 2025-12-31"\n'
                "suite,product,feature_name,feature_description,amount,tax,currency,billing_status\n"
                "Payments,Processing,Card processing,Card payment fees,5.00,0.00,EUR,NOT_INVOICED\n"
                "Revenue management,Billing,Billing fee,Invoice fees,1.00,0.00,EUR,NOT_INVOICED\n"
                "Payments,Disputes,Dispute evidence,Submission,2.00,0.00,EUR,NOT_INVOICED\n"
                "Payments,Disputes,Dispute fee,Card dispute,4.00,0.00,EUR,NOT_INVOICED\n",
                encoding="utf-8",
            )
            activity = root / "synthetic-balance-activity.csv"
            activity.write_text(
                '"Report period","2025-01-01 to 2025-12-31"\n'
                "id,created,type,gross,fee,net,currency,reporting_category\n"
                "ch_1,2025-01-01,payment,260.00,-12.00,248.00,EUR,charge\n"
                "rf_1,2025-06-01,refund,-3.00,0.00,-3.00,EUR,refund\n"
                "dp_1,2025-09-01,dispute,0.00,0.00,0.00,EUR,dispute\n"
                "po_1,2025-12-31,payout,-245.00,0.00,-245.00,EUR,payout\n",
                encoding="utf-8",
            )
            bank = root / "synthetic-wise.csv"
            bank.write_text(
                'Opening balance,"100.00",,EUR,,\n'
                "Date,Amount,Direction,Currency,Counterparty,Description,Reference,IBAN\n"
                "2025-01-02,263.00,credit,EUR,Stripe Technology Europe Ltd,Received money from Stripe,PAYOUTS,EE001122334455667788\n"
                "2025-11-27,-14.00,debit,EUR,STRIPE,Paid to STRIPE,REF-110,EE001122334455667788\n"
                "2025-12-15,-4.00,debit,EUR,STRIPE,Paid to STRIPE,REF-4,EE001122334455667788\n"
                'Closing balance,"345.00",,EUR,,\n',
                encoding="utf-8",
            )
            workflow = AccountingWorkflow(
                company_name="Example Company OÜ",
                registry_code="EXAMPLE-123",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                rules_path=root / "rules.json",
            )

            imported = workflow.import_documents(
                (
                    DocumentInput(balance, DocumentKind.STRIPE_REPORT),
                    DocumentInput(fees, DocumentKind.STRIPE_REPORT),
                    DocumentInput(activity, DocumentKind.STRIPE_REPORT),
                    DocumentInput(bank, DocumentKind.BANK_STATEMENT),
                )
            )
            workflow.normalize_transactions()
            reconciled = workflow.reconcile_stripe_balances()
            fee_result = workflow.reconcile_stripe_fees()
            activity_result = workflow.reconcile_stripe_activity()
            transfers = workflow.match_stripe_bank_transfers()
            repeated = workflow.import_stripe_report(balance)

            self.assertEqual(imported["stripe_reports"], 3)
            self.assertEqual(reconciled["status"], "reconciled")
            self.assertEqual(reconciled["balance_difference"], "0.00")
            self.assertEqual(reconciled["closing_balance"], "10.00")
            self.assertEqual(fee_result["status"], "reconciled")
            self.assertEqual(fee_result["total_including_tax"], "12.00")
            self.assertEqual(activity_result["status"], "reconciled")
            self.assertEqual(activity_result["activity_categories"]["refunds"]["net"], "-3.00")
            self.assertEqual(activity_result["activity_categories"]["disputes"]["count"], 1)
            self.assertEqual(activity_result["itemized_payouts"]["difference"], "0.00")
            self.assertEqual(transfers["bank_receipts_from_stripe"], "263.00")
            self.assertEqual(transfers["bank_payments_to_stripe"], "18.00")
            self.assertEqual(transfers["net_bank_movement"], "245.00")
            self.assertEqual(transfers["net_amount_difference"], "0.00")
            self.assertEqual(transfers["directional_incoming_vs_payout_difference"], "18.00")
            self.assertEqual(transfers["status"], "net_amount_matches; direction_and_classification_pending")
            self.assertEqual(repeated["status"], "duplicate_file_skipped")

    def test_partial_period_and_multiple_currency_reports_are_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            balance = root / "synthetic-partial-summary.csv"
            balance.write_text(
                "category,description,net_amount,currency\n"
                "starting_balance,Starting balance (2025-01-01),10.00,EUR\n"
                "activity_gross,Activity before fees,100.00,EUR\n"
                "activity_fee,Fees,-1.00,EUR\n"
                "payouts,Payouts,-100.00,EUR\n"
                "ending_balance,Ending balance (2025-06-30),9.00,EUR\n",
                encoding="utf-8",
            )
            assistant = StripeReportingAssistant(
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
            )
            assistant.import_report(balance)
            status = assistant.missing_reports()
            balance_coverage = next(
                item for item in status["missing_or_unverified"]
                if item["report_type"] == "balance_summary"
            )
            self.assertEqual(balance_coverage["status"], "period_or_currency_mismatch")
            self.assertFalse(status["complete"])

    def test_balance_totals_are_not_combined_across_currencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            balance = root / "synthetic-multi-currency-summary.csv"
            balance.write_text(
                "category,description,net_amount,currency\n"
                "starting_balance,Starting balance (2025-01-01),10.00,EUR\n"
                "activity_gross,Activity before fees,100.00,EUR\n"
                "activity_fee,Fees,-1.00,EUR\n"
                "payouts,Payouts,-100.00,EUR\n"
                "ending_balance,Ending balance (2025-12-31),4.52,EUR\n"
                "other_activity,Other currency balance,25.00,USD\n",
                encoding="utf-8",
            )
            assistant = StripeReportingAssistant(
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
            )
            assistant.import_report(balance)

            result = assistant.reconcile_balances()

            self.assertEqual(result["status"], "currency_unverified")
            self.assertEqual(set(result["reported_currencies"]), {"EUR", "USD"})


if __name__ == "__main__":
    unittest.main()
