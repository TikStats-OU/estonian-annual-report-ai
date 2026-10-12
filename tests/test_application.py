"""Synthetic end-to-end document-to-draft workflow checks."""

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
from estonian_annual_report_ai.reporting.draft import (
    AnnualReportDraft,
    export_annual_report_draft,
)
from estonian_annual_report_ai.transactions import ClassificationCategory


class AccountingWorkflowTests(unittest.TestCase):
    def test_import_to_reconciliation_and_blocked_report_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bank = root / "synthetic-bank.csv"
            bank.write_text(
                'Opening balance,"100.00",,EUR,,\n'
                "Date,Amount (EUR),Counterparty,Description,Reference,IBAN\n"
                "2025-01-05,-10.00,CloudCo,Hosting invoice,S-10,EE001122334455667788\n"
                'Closing balance,"90.00",,EUR,,\n',
                encoding="utf-8",
            )
            invoice = root / "synthetic-expense.txt"
            invoice.write_text(
                "Invoice number: S-10\nInvoice date: 05.01.2025\nSeller: CloudCo\n"
                "Total amount: EUR 10.00\n",
                encoding="utf-8",
            )
            report_2024 = root / "synthetic-annual-report-2024.txt"
            report_2024.write_text(
                "Total assets 2024: 100.00 EUR\n"
                "Total liabilities 2024: 0.00 EUR\n"
                "Total equity 2024: 100.00 EUR\n",
                encoding="utf-8",
            )
            report_2022 = root / "synthetic-annual-report-2022.txt"
            report_2022.write_text("Total assets 2022: 80.00 EUR\nRevenue 2022: 20.00 EUR\n", encoding="utf-8")
            report_2023 = root / "synthetic-annual-report-2023.txt"
            report_2023.write_text("Total assets 2023: 90.00 EUR\nRevenue 2023: 30.00 EUR\n", encoding="utf-8")

            workflow = AccountingWorkflow(
                company_name="Synthetic OÜ",
                registry_code="00000000",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                rules_path=root / "private" / "rules.json",
            )
            result = workflow.import_documents(
                (
                    DocumentInput(bank, DocumentKind.BANK_STATEMENT),
                    DocumentInput(invoice, DocumentKind.INVOICE),
                    DocumentInput(report_2024, DocumentKind.ANNUAL_REPORT),
                    DocumentInput(report_2022, DocumentKind.ANNUAL_REPORT),
                    DocumentInput(report_2023, DocumentKind.ANNUAL_REPORT),
                )
            )
            self.assertEqual(result["bank_statements"], 1)
            self.assertEqual(result["invoices"], 1)
            self.assertEqual(result["annual_reports"], 3)
            self.assertTrue(workflow.parse_bank_statements()[0].transactions[0].source.sha256)
            self.assertEqual(len(workflow.report_candidates()), 7)
            repeated = workflow.import_documents((DocumentInput(bank, DocumentKind.BANK_STATEMENT),))
            self.assertEqual(repeated["bank_statements"], 0)
            self.assertEqual(len(workflow.parse_bank_statements()), 1)
            self.assertTrue(any("Duplicate source document skipped" in item for item in repeated["errors"]))

            normalized = workflow.normalize_transactions()
            self.assertEqual(len(normalized), 1)
            classified = workflow.classify_transactions()
            self.assertEqual(classified[0].classification.category, ClassificationCategory.OPERATING_EXPENSE)
            self.assertEqual(classified[0].classification.status, "rule")
            self.assertEqual(workflow.unclassified_transactions(), ())
            self.assertEqual(workflow.reconcile_accounts()[0].status, "reconciled")
            for candidate_id, candidate in workflow.report_candidates():
                if candidate.fiscal_year == 2024 and candidate.field in {"assets", "liabilities", "equity"}:
                    workflow.approve_report_candidate(candidate_id)
            ledger, ledger_issues = workflow.build_ledger()
            self.assertEqual(len(ledger.entries), 2)  # approved opening control plus bank expense
            self.assertTrue(any("aggregate control totals" in issue for issue in ledger_issues))

            draft, paths = workflow.generate_annual_report(root / "reports")
            self.assertIsNotNone(paths)
            self.assertEqual(draft.status, "blocked")
            self.assertIsNone(draft.balance_sheet)
            self.assertIsNone(draft.income_statement)
            self.assertEqual({Path(path).suffix for path in paths.values()}, {".json", ".xlsx", ".pdf"})
            self.assertTrue(all(Path(path).is_file() for path in paths.values()))
            missing = workflow.get_missing_information()
            self.assertTrue(any("Duplicate source document skipped" in item for item in missing))
            self.assertTrue(draft.missing_information)


class DraftXlsxFormulaSafetyTests(unittest.TestCase):
    def test_untrusted_text_is_literal_and_financial_values_remain_numeric(self) -> None:
        from openpyxl import load_workbook

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft = AnnualReportDraft(
                company_name="=1+1",
                registry_code="+12345678",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                status="ready_for_user_review",
                figure_status="calculated",
                balance_sheet={
                    "assets": [{
                        "account_code": "-ACCT",
                        "name": "@SUM(1,1)",
                        "amount": "-10.25",
                    }],
                    "total_assets": "-10.25",
                },
                income_statement={"total_revenue": "10.25"},
                notes_draft=(),
                reconciliation_issues=(),
                missing_information=("-Confirm source document",),
                disclaimer="Review only",
            )
            paths = export_annual_report_draft(draft, root)
            workbook = load_workbook(paths["xlsx"], data_only=False)

            expected_text = {
                "Summary": {"B1": "'=1+1", "B2": "'+12345678"},
                "Balance Sheet": {
                    "A3": "'-ACCT",
                    "B3": "'@SUM(1,1)",
                },
                "Missing information": {"A1": "'-Confirm source document"},
            }
            for sheet_name, cells in expected_text.items():
                for address, expected in cells.items():
                    cell = workbook[sheet_name][address]
                    self.assertEqual(cell.value, expected)
                    self.assertEqual(cell.data_type, "s")

            amount = workbook["Balance Sheet"]["C3"]
            self.assertEqual(amount.value, -10.25)
            self.assertEqual(amount.data_type, "n")
            self.assertFalse(
                any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row)
            )


if __name__ == "__main__":
    unittest.main()
