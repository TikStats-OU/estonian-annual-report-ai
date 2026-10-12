"""Regression tests for parser, workflow, and export defects found in review."""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from estonian_annual_report_ai.application import AccountingWorkflow
from estonian_annual_report_ai.documents import (
    BankTransaction,
    ParsedBankStatement,
    SourceReference,
)
from estonian_annual_report_ai.documents.common import parse_money
from estonian_annual_report_ai.reporting.draft import (
    AnnualReportDraft,
    export_annual_report_draft,
)
from estonian_annual_report_ai.stripe_assistant import StripeReportingAssistant, _money
from estonian_annual_report_ai.transactions import (
    AIClassificationOptions,
    ClassificationCategory,
    ClassificationRuleStore,
    classify_transactions,
    classify_with_ai,
    normalize_transactions,
)


def _row(amount: str, *, description: str, bank: str = "A", row: int = 2,
         reference: str | None = None, currency: str = "EUR") -> BankTransaction:
    return BankTransaction(
        transaction_date=date(2025, 3, 1),
        amount=Decimal(amount),
        currency=currency,
        source=SourceReference(f"{bank}.csv", f"{bank}.csv", f"{bank}.csv", "csv", row_number=row),
        bank_account_id=bank,
        description=description,
        reference=reference,
        direction="credit" if Decimal(amount) > 0 else "debit",
    )


def _statement(*rows: BankTransaction, bank: str = "A") -> ParsedBankStatement:
    return ParsedBankStatement(
        source=SourceReference(f"{bank}.csv", f"{bank}.csv", f"{bank}.csv", "csv"),
        transactions=rows,
        opening_balance=Decimal("0.00"),
        closing_balance=Decimal("0.00"),
        currency="EUR",
        bank_account_id=bank,
    )


class _CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, transaction):
        self.calls += 1
        return ClassificationCategory.REVENUE, Decimal("0.95"), "Looks like a sale"


class MoneyParsingTests(unittest.TestCase):
    def test_zero_padded_decimals_from_payment_exports_are_accepted(self) -> None:
        self.assertEqual(parse_money("66.280000000000000000"), (Decimal("66.28"), None))
        self.assertEqual(parse_money("1,234.500000"), (Decimal("1234.50"), None))

    def test_extra_significant_decimals_stay_ambiguous(self) -> None:
        self.assertEqual(parse_money("12.3456"), (None, None))
        self.assertEqual(parse_money("1.234"), (Decimal(1234), None))

    def test_negative_zero_difference_formats_as_zero(self) -> None:
        self.assertEqual(_money(-Decimal("0.00")), "0.00")


class StripeFeeReportTests(unittest.TestCase):
    def test_fee_report_with_padded_decimals_reconciles_with_balance_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            balance = root / "synthetic-balance-summary.csv"
            balance.write_text(
                "category,description,net_amount,currency\n"
                "starting_balance,Starting balance (2025-01-01),0.00,EUR\n"
                "activity_gross,Account activity before fees,100.00,EUR\n"
                "activity_fee,Less fees,-6.50,EUR\n"
                "activity,Activity,93.50,EUR\n"
                "payouts_gross,Payouts to bank,-93.50,EUR\n"
                "payouts,Total payouts,-93.50,EUR\n"
                "ending_balance,Ending balance (2025-12-31),0.00,EUR\n",
                encoding="utf-8",
            )
            fees = root / "synthetic-fees-summary.csv"
            fees.write_text(
                "suite,product,feature_name,feature_description,amount,tax,currency,billing_status\n"
                "Payments,Processing,Card processing,Card fees,4.500000000000000000,"
                "0.000000000000000000,eur,NOT_INVOICED\n"
                "Payments,Disputes,Dispute fee,Card dispute,2.000000000000000000,"
                "0.000000000000000000,eur,NOT_INVOICED\n",
                encoding="utf-8",
            )
            assistant = StripeReportingAssistant(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
            assistant.import_report(balance)
            assistant.import_report(fees)

            result = assistant.reconcile_fees()

        self.assertEqual(result["total_including_tax"], "6.50")
        self.assertEqual(result["status"], "reconciled")


class AIClassificationSafetyTests(unittest.TestCase):
    def test_rows_held_for_human_review_are_never_sent_to_the_provider(self) -> None:
        rows = normalize_transactions((
            _statement(
                _row("50.00", description="Customer payment", row=2),
                _row("50.00", description="Customer payment", row=3),
                _row("20.00", description="Refund for order 7", row=4),
                _row("-30.00", description="Transfer to savings", row=5, reference="T-1"),
            ),
            _statement(_row("30.00", description="Transfer from main", bank="B", reference="T-1"), bank="B"),
        ))
        rows = classify_transactions(rows)
        provider = _CountingProvider()

        result = classify_with_ai(
            rows,
            provider=provider,
            options=AIClassificationOptions(enabled=True, confirm_external_processing=True),
        )

        self.assertEqual(provider.calls, 0)
        self.assertFalse(any(row.classification.status == "ai_proposed" for row in result))


class ReportingYearTests(unittest.TestCase):
    def _workflow(self, root: Path, year: int) -> AccountingWorkflow:
        return AccountingWorkflow(
            company_name="Example Company OÜ",
            registry_code="EXAMPLE-123",
            period_start=date(year, 1, 1),
            period_end=date(year, 12, 31),
            rules_path=root / "rules.json",
        )

    def test_missing_information_follows_the_configured_period(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow = self._workflow(Path(temporary), 2026)
            missing = " ".join(workflow.get_missing_information())
            advisory = workflow.get_advisory_information()

        self.assertIn("2025 annual report to establish the 2026 opening balances", missing)
        self.assertNotIn("2024", missing)
        self.assertEqual(len(advisory), 2)
        self.assertIn("2023", advisory[0])

    def test_older_reports_do_not_block_a_young_company(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow = self._workflow(Path(temporary), 2026)
            draft, _ = workflow.generate_annual_report()

        critical = " ".join(issue.message for issue in draft.reconciliation_issues if issue.severity == "critical")
        self.assertNotIn("historical consistency", critical)


class RuleStoreTests(unittest.TestCase):
    def test_corrupted_rules_file_is_not_silently_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rules.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unreadable"):
                ClassificationRuleStore(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")


class DraftExportTests(unittest.TestCase):
    def test_long_pdf_lines_are_wrapped_not_truncated(self) -> None:
        import pdfplumber

        long_item = "Confirm " + "supporting evidence " * 12 + "FINAL-MARKER."
        draft = AnnualReportDraft(
            company_name="Example Company OÜ",
            registry_code="EXAMPLE-123",
            period_start=date(2025, 1, 1),
            period_end=date(2025, 12, 31),
            status="blocked",
            figure_status="calculated_from_ledger; not source-verified",
            balance_sheet=None,
            income_statement=None,
            notes_draft=(),
            reconciliation_issues=(),
            missing_information=(long_item,),
            disclaimer="Draft for review only.",
        )
        with tempfile.TemporaryDirectory() as temporary:
            paths = export_annual_report_draft(draft, Path(temporary))
            with pdfplumber.open(paths["pdf"]) as pdf:
                text = "".join(page.extract_text() or "" for page in pdf.pages)

        self.assertIn("FINAL-MARKER.", text)


class CommandLineTests(unittest.TestCase):
    def _run(self, *args: str, **env: str):
        import os
        import subprocess
        import sys

        return subprocess.run(
            [sys.executable, "-m", "estonian_annual_report_ai.mcp_server", *args],
            capture_output=True, text=True, timeout=60, check=False,
            env={**os.environ, **env},
        )

    def test_check_reports_tools_and_configuration_then_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run(
                "--check",
                ESTONIAN_REPORT_DATA_DIR=temporary,
                ESTONIAN_REPORT_REGISTRY_CODE="EXAMPLE-123",
                ESTONIAN_REPORT_YEAR="2025",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"ready: \d+ MCP tools")
        self.assertIn("2025-01-01 to 2025-12-31", result.stdout)

    def test_check_without_a_company_explains_the_next_step(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run(
                "--check",
                ESTONIAN_REPORT_DATA_DIR=temporary,
                ESTONIAN_REPORT_COMPANY_NAME="",
                ESTONIAN_REPORT_REGISTRY_CODE="",
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not chosen yet", result.stdout)

    def test_check_rejects_a_malformed_year(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._run("--check", ESTONIAN_REPORT_DATA_DIR=temporary, ESTONIAN_REPORT_YEAR="next")
        self.assertEqual(result.returncode, 2)
        self.assertIn("four-digit year", result.stderr)


class CompanySetupTests(unittest.TestCase):
    def test_company_chosen_in_chat_is_remembered_for_the_next_session(self) -> None:
        import os
        from unittest import mock

        from estonian_annual_report_ai import mcp_server as server

        saved_instance = server._workflow_instance
        env = {key: value for key, value in os.environ.items() if not key.startswith("ESTONIAN_REPORT_")}
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {**env, "ESTONIAN_REPORT_DATA_DIR": temporary}, clear=True
        ):
            try:
                server._workflow_instance = None
                self.assertFalse(server.get_company()["configured"])

                chosen = server.set_company(
                    server.CompanyInput(company_name="Sample Shop OÜ", registry_code="SAMPLE-1", year=2025)
                )
                self.assertTrue(chosen["configured"])
                self.assertEqual(chosen["period_end"], "2025-12-31")
                profile = Path(temporary) / "company.json"
                self.assertEqual(profile.stat().st_mode & 0o777, 0o600)

                server._workflow_instance = None  # simulate a new session
                reloaded = server.get_company()
                self.assertTrue(reloaded["configured"])
                self.assertEqual(reloaded["registry_code"], "SAMPLE-1")
            finally:
                server._workflow_instance = saved_instance

    def test_invalid_registry_code_is_rejected(self) -> None:
        from pydantic import ValidationError

        from estonian_annual_report_ai import mcp_server as server

        with self.assertRaises(ValidationError):
            server.CompanyInput(company_name="Sample Shop OÜ", registry_code="../escape", year=2025)


if __name__ == "__main__":
    unittest.main()
