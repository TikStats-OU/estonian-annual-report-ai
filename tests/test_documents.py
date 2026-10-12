"""Synthetic-only tests for local financial document extraction."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from estonian_annual_report_ai.documents import (
    DocumentValidationError,
    extract_annual_report,
    find_financial_discrepancies,
    parse_bank_statement,
    parse_invoice,
)


class DocumentImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_csv_statement_extracts_transactions_balances_and_source_rows(self) -> None:
        path = self.directory / "synthetic-statement.csv"
        path.write_text(
            'Opening balance,"1,000.00",,EUR,,\n'
            "Date,Amount (EUR),Counterparty,Description,Reference,IBAN\n"
            "2025-01-05,-12.50,CloudCo,Hosting,INV-23,EE001122334455667788\n"
            "2025-01-06,80.00,Client A,Subscription,REF-1,EE001122334455667788\n"
            'Closing balance,"1,067.50",,EUR,,\n',
            encoding="utf-8",
        )

        statement = parse_bank_statement(path)

        self.assertEqual(statement.opening_balance, Decimal("1000.00"))
        self.assertEqual(statement.closing_balance, Decimal("1067.50"))
        self.assertEqual(statement.currency, "EUR")
        self.assertEqual(len(statement.transactions), 2)
        first, second = statement.transactions
        self.assertEqual(first.transaction_date, date(2025, 1, 5))
        self.assertEqual(first.amount, Decimal("-12.50"))
        self.assertEqual(first.direction, "debit")
        self.assertEqual(first.counterparty, "CloudCo")
        self.assertEqual(first.reference, "INV-23")
        self.assertEqual(first.bank_account_id, "EE001122334455667788")
        self.assertEqual(statement.bank_account_id, "EE001122334455667788")
        self.assertEqual(first.source.filename, path.name)
        self.assertEqual(first.source.row_number, 3)
        self.assertEqual(second.amount, Decimal("80.00"))
        self.assertIsNone(second.direction)
        self.assertIn("ambiguous_direction", {issue.code for issue in statement.issues})

    def test_wise_csv_uses_transaction_type_id_and_running_balances(self) -> None:
        path = self.directory / "synthetic-wise.csv"
        path.write_text(
            "TransferWise ID,Date,Amount,Currency,Description,Running Balance,Transaction Type,Transaction Details Type,Payer Name,Payee Name,Merchant\n"
            "W-1,2025-01-01,100.00,EUR,Customer deposit,1100.00,CREDIT,DEPOSIT,Customer,,\n"
            "W-2,2025-01-02,-10.00,EUR,Card purchase,1090.00,DEBIT,CARD,,,Vendor\n",
            encoding="utf-8",
        )
        statement = parse_bank_statement(path)
        self.assertEqual(len(statement.transactions), 2)
        self.assertEqual(statement.transactions[0].reference, "W-1")
        self.assertEqual(statement.transactions[0].direction, "credit")
        self.assertEqual(statement.transactions[0].counterparty, "Customer")
        self.assertEqual(statement.transactions[1].direction, "debit")
        self.assertEqual(statement.transactions[1].counterparty, "Vendor")
        self.assertEqual(statement.opening_balance, Decimal("1000.00"))
        self.assertEqual(statement.closing_balance, Decimal("1090.00"))
        self.assertIn("derived_opening_balance", {issue.code for issue in statement.issues})

    def test_wise_reverse_chronological_rows_reconcile_same_day_transactions(self) -> None:
        path = self.directory / "synthetic-wise-reverse.csv"
        path.write_text(
            "TransferWise ID,Date,Amount,Currency,Description,Running Balance,Transaction Type,Transaction Details Type\n"
            "W-3,2025-01-02,12.00,EUR,Deposit,111.00,CREDIT,DEPOSIT\n"
            "W-2,2025-01-02,-3.00,EUR,Purchase,99.00,DEBIT,CARD\n"
            "W-1,2025-01-02,2.00,EUR,Deposit,102.00,CREDIT,DEPOSIT\n",
            encoding="utf-8",
        )

        statement = parse_bank_statement(path)

        self.assertEqual(statement.opening_balance, Decimal("100.00"))
        self.assertEqual(statement.closing_balance, Decimal("111.00"))
        self.assertIn("derived_opening_balance", {issue.code for issue in statement.issues})

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl dependency is not installed")
    def test_xlsx_statement_preserves_sheet_and_row_location(self) -> None:
        from openpyxl import Workbook

        path = self.directory / "synthetic-statement.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Transactions"
        sheet.append(["Date", "Debit", "Credit", "Currency", "Counterparty", "Description"])
        sheet.append([date(2025, 2, 1), 24.5, None, "EUR", "CloudCo", "Server fee"])
        sheet.append([date(2025, 2, 2), None, 100.0, "EUR", "Client A", "Invoice 44"])
        workbook.save(path)

        statement = parse_bank_statement(path)

        self.assertEqual(len(statement.transactions), 2)
        debit, credit = statement.transactions
        self.assertEqual(debit.amount, Decimal("-24.5"))
        self.assertEqual(debit.direction, "debit")
        self.assertEqual(debit.source.sheet_name, "Transactions")
        self.assertEqual(debit.source.row_number, 2)
        self.assertEqual(credit.amount, Decimal("100.0"))
        self.assertEqual(credit.direction, "credit")

    def test_pdf_statement_extracts_text_transactions_and_statement_balances(self) -> None:
        path = self.directory / "synthetic-statement.pdf"
        path.write_bytes(_make_pdf([
            "Opening balance: EUR 1,000.00",
            "2025-01-02 Acme Ltd service fee -25.00 EUR Reference: FEE-1",
            "Closing balance: EUR 975.00",
        ]))

        statement = parse_bank_statement(path)

        self.assertEqual(statement.opening_balance, Decimal("1000.00"))
        self.assertEqual(statement.closing_balance, Decimal("975.00"))
        self.assertEqual(len(statement.transactions), 1)
        transaction = statement.transactions[0]
        self.assertEqual(transaction.transaction_date, date(2025, 1, 2))
        self.assertEqual(transaction.amount, Decimal("-25.00"))
        self.assertEqual(transaction.currency, "EUR")
        self.assertEqual(transaction.reference, "FEE-1")
        self.assertEqual(transaction.source.page_number, 1)
        self.assertEqual(transaction.source.row_number, 2)

    def test_invoice_extraction_returns_labeled_candidates(self) -> None:
        path = self.directory / "synthetic-invoice.txt"
        path.write_text(
            "Invoice number: INV-2025-004\n"
            "Invoice date: 04.01.2025\n"
            "Seller: Acme Ltd\n"
            "Buyer: Example Company OÜ\n"
            "Subtotal: EUR 100.00\n"
            "VAT: EUR 22.00\n"
            "Total: EUR 122.00\n",
            encoding="utf-8",
        )

        invoice = parse_invoice(path)
        by_field = {}
        for candidate in invoice.candidates:
            by_field.setdefault(candidate.field, []).append(candidate)

        self.assertEqual(by_field["invoice_number"][0].value, "INV-2025-004")
        self.assertEqual(by_field["issue_date"][0].value, date(2025, 1, 4))
        self.assertEqual(by_field["seller"][0].value, "Acme Ltd")
        self.assertEqual(by_field["net_amount"][0].value, Decimal("100.00"))
        self.assertEqual(by_field["net_amount"][0].currency, "EUR")
        self.assertEqual(by_field["tax_amount"][0].value, Decimal("22.00"))
        self.assertEqual(by_field["total_amount"][0].value, Decimal("122.00"))
        self.assertEqual(by_field["total_amount"][0].source.row_number, 7)

    def test_pdf_invoice_and_annual_report_values_are_extracted(self) -> None:
        invoice_path = self.directory / "synthetic-invoice.pdf"
        invoice_path.write_bytes(_make_pdf([
            "Invoice number: INV-PDF-1",
            "Invoice date: 01.02.2025",
            "Seller: Acme Ltd",
            "Total amount: EUR 100.00",
        ]))
        report_path = self.directory / "synthetic-report.pdf"
        report_path.write_bytes(_make_pdf([
            "Total assets 2024: 12,000.00 EUR",
            "Revenue 2024: 4,000.00 EUR",
        ]))

        invoice = parse_invoice(invoice_path)
        report = extract_annual_report(report_path)

        invoice_number = next(candidate for candidate in invoice.candidates if candidate.field == "invoice_number")
        invoice_total = next(candidate for candidate in invoice.candidates if candidate.field == "total_amount")
        self.assertEqual(invoice_number.value, "INV-PDF-1")
        self.assertEqual(invoice_number.source.page_number, 1)
        self.assertEqual(invoice_total.value, Decimal("100.00"))
        self.assertEqual(report.for_field("assets")[0].value, Decimal("12000.00"))
        self.assertEqual(report.for_field("assets")[0].source.page_number, 1)
        self.assertEqual(report.for_field("revenue")[0].fiscal_year, 2024)

    def test_annual_report_preserves_candidates_and_surfaces_conflicts(self) -> None:
        first_path = self.directory / "synthetic-annual-report-2024.txt"
        second_path = self.directory / "synthetic-annual-report-copy.txt"
        first_path.write_text(
            "Total assets 2024: 12,000.00 EUR\n"
            "Total liabilities 2024: 2,000.00 EUR\n"
            "Total equity 2024: 10,000.00 EUR\n"
            "Revenue 2024: 5,000.00 EUR\n"
            "Total expenses 2024: 4,500.00 EUR\n"
            "Profit for the year 2024: 500.00 EUR\n"
            "Retained earnings 2024: 1,000.00 EUR\n",
            encoding="utf-8",
        )
        second_path.write_text("Total assets 2024: 12,001.00 EUR\n", encoding="utf-8")

        first = extract_annual_report(first_path)
        second = extract_annual_report(second_path)
        discrepancies = find_financial_discrepancies((first, second))

        self.assertEqual(first.for_field("assets")[0].value, Decimal("12000.00"))
        self.assertEqual(first.for_field("assets")[0].fiscal_year, 2024)
        self.assertEqual(first.for_field("retained_earnings")[0].value, Decimal("1000.00"))
        self.assertEqual(len(discrepancies), 1)
        self.assertEqual(discrepancies[0].field, "assets")
        self.assertEqual(discrepancies[0].fiscal_year, 2024)
        self.assertEqual(discrepancies[0].values, (Decimal("12000.00"), Decimal("12001.00")))

    def test_rejects_wrong_file_signature_and_oversized_documents(self) -> None:
        wrong_type = self.directory / "not-really-a.pdf"
        wrong_type.write_text("fake PDF", encoding="utf-8")
        with self.assertRaisesRegex(DocumentValidationError, "does not match"):
            parse_bank_statement(wrong_type)

        oversized = self.directory / "large.csv"
        oversized.write_text("Date,Amount\n" + "x" * 20, encoding="utf-8")
        with self.assertRaisesRegex(DocumentValidationError, "size limit"):
            parse_bank_statement(oversized, max_bytes=10)


def _make_pdf(lines: list[str]) -> bytes:
    """Create a tiny synthetic one-page PDF with only standard Helvetica text."""
    commands = ["BT", "/F1 10 Tf", "40 760 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


if __name__ == "__main__":
    unittest.main()
