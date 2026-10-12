"""Synthetic transaction normalization, classification and reconciliation tests."""

import tempfile
import unittest
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from estonian_annual_report_ai.documents import (
    BankTransaction,
    ParsedBankStatement,
    SourceReference,
)
from estonian_annual_report_ai.transactions import (
    AIClassificationOptions,
    ClassificationCategory,
    ClassificationRuleStore,
    classify_transactions,
    classify_with_ai,
    normalize_transactions,
    reconcile_statements,
)


def _source(name: str, row: int = 2) -> SourceReference:
    return SourceReference(name, name, name, "csv", row_number=row)


def _transaction(
    *, amount: str, direction: str | None, description: str = "",
    counterparty: str = "", currency: str = "EUR", bank: str = "A",
    row: int = 2, day: date = date(2025, 1, 10), reference: str | None = None,
) -> BankTransaction:
    return BankTransaction(
        transaction_date=day,
        amount=Decimal(amount),
        currency=currency,
        source=_source("synthetic.csv", row),
        bank_account_id=bank,
        counterparty=counterparty,
        description=description,
        reference=reference,
        direction=direction,
    )


def _statement(*rows: BankTransaction, opening="100.00", closing="100.00", currency="EUR", bank="A", filename=None) -> ParsedBankStatement:
    return ParsedBankStatement(
        source=_source(filename or f"{bank}.csv"),
        transactions=tuple(rows),
        opening_balance=Decimal(opening) if opening is not None else None,
        closing_balance=Decimal(closing) if closing is not None else None,
        currency=currency,
        bank_account_id=bank,
    )


class TransactionWorkflowTests(unittest.TestCase):
    def test_ai_requires_opt_in_and_returns_unapproved_proposals(self) -> None:
        row = normalize_transactions((_statement(
            _transaction(amount="25.00", direction="credit", description="Incoming payment"),
        ),))

        class FakeProvider:
            calls = 0

            def propose(self, transaction):
                self.calls += 1
                return ClassificationCategory.REVENUE, Decimal("0.91"), "Likely sale"

        provider = FakeProvider()
        self.assertEqual(classify_with_ai(row, provider=provider), row)
        self.assertEqual(provider.calls, 0)
        with self.assertRaisesRegex(ValueError, "Confirm external processing"):
            classify_with_ai(
                row,
                provider=provider,
                options=AIClassificationOptions(enabled=True),
            )
        proposed = classify_with_ai(
            row,
            provider=provider,
            options=AIClassificationOptions(enabled=True, confirm_external_processing=True),
        )[0]
        self.assertEqual(provider.calls, 1)
        self.assertEqual(proposed.classification.category, ClassificationCategory.REVENUE)
        self.assertEqual(proposed.classification.status, "ai_proposed")

    def test_ai_invalid_confidence_and_internal_transfer_remain_unclassified(self) -> None:
        row = normalize_transactions((_statement(
            _transaction(amount="25.00", direction="credit", description="Incoming payment"),
        ),))

        class FakeProvider:
            def __init__(self, category, confidence):
                self.category = category
                self.confidence = confidence

            def propose(self, transaction):
                return self.category, self.confidence, "proposal"

        options = AIClassificationOptions(enabled=True, confirm_external_processing=True)
        invalid = classify_with_ai(
            row, provider=FakeProvider(ClassificationCategory.REVENUE, Decimal("NaN")), options=options
        )[0]
        transfer = classify_with_ai(
            row, provider=FakeProvider(ClassificationCategory.INTERNAL_TRANSFER, Decimal("0.9")), options=options
        )[0]
        self.assertEqual(invalid.classification.category, ClassificationCategory.UNCLASSIFIED)
        self.assertEqual(invalid.classification.confidence, Decimal(0))
        self.assertEqual(transfer.classification.category, ClassificationCategory.UNCLASSIFIED)

    def test_duplicate_rows_are_marked_and_do_not_gain_revenue_classification(self) -> None:
        first = _transaction(amount="25.00", direction="credit", description="Payment", counterparty="Customer", row=2, reference="TX-1")
        duplicate = _transaction(amount="25.00", direction="credit", description="Payment", counterparty="Customer", row=8, reference="TX-1")
        normalized = normalize_transactions((_statement(first), _statement(duplicate, bank="B")))
        self.assertIsNone(normalized[0].duplicate_of)
        self.assertIsNone(normalized[1].duplicate_of)
        self.assertEqual(normalized[1].possible_duplicate_of, normalized[0].transaction_id)
        classified = classify_transactions(normalized)
        self.assertEqual(classified[0].classification.category, ClassificationCategory.UNCLASSIFIED)
        self.assertEqual(classified[1].classification.status, "needs_review")

    def test_equal_transactions_without_matching_stable_reference_are_retained_for_review(self) -> None:
        first = _transaction(amount="2.00", direction="debit", description="Monthly fee", row=2)
        second = _transaction(amount="2.00", direction="debit", description="Monthly fee", row=8)
        normalized = normalize_transactions((_statement(first), _statement(second, filename="second-statement.csv")))
        self.assertNotEqual(normalized[0].transaction_id, normalized[1].transaction_id)
        self.assertIsNone(normalized[1].duplicate_of)
        self.assertEqual(normalized[1].possible_duplicate_of, normalized[0].transaction_id)
        self.assertEqual(classify_transactions(normalized)[1].classification.status, "needs_review")

    def test_tax_and_loan_interest_do_not_post_as_operating_expense_or_principal(self) -> None:
        tax = _transaction(amount="-22.00", direction="debit", description="VAT tax payment", row=2)
        interest = _transaction(amount="-5.00", direction="debit", description="Interest on loan", row=3)
        classified = classify_transactions(normalize_transactions((_statement(tax, interest),)))
        self.assertTrue(all(item.classification.category == ClassificationCategory.UNCLASSIFIED for item in classified))
        self.assertTrue(all(item.classification.status == "needs_review" for item in classified))

    def test_internal_transfers_match_equal_opposite_movements_across_accounts(self) -> None:
        outgoing = _transaction(amount="-40.00", direction="debit", description="Internal transfer", bank="A")
        incoming = _transaction(amount="40.00", direction="credit", description="Internal transfer", bank="B", row=3)
        normalized = normalize_transactions((_statement(outgoing, bank="A"), _statement(incoming, bank="B")))
        self.assertEqual(normalized[0].internal_transfer_id, normalized[1].internal_transfer_id)
        classified = classify_transactions(normalized)
        self.assertEqual(classified[0].classification.category, ClassificationCategory.UNCLASSIFIED)
        self.assertEqual(classified[0].classification.status, "needs_review")
        self.assertEqual(classified[1].classification.status, "needs_review")
        from estonian_annual_report_ai.transactions import build_ledger
        ledger, issues = build_ledger(classified, period_start=date(2025, 1, 1), period_end=date(2025, 12, 31))
        self.assertEqual(ledger.entries, ())
        self.assertEqual(len(issues), 2)

    def test_equal_opposite_movements_are_not_transfers_without_evidence(self) -> None:
        outgoing = _transaction(amount="-40.00", direction="debit", description="Supplier payment", bank="A")
        incoming = _transaction(amount="40.00", direction="credit", description="Client refund", bank="B", row=3)
        normalized = normalize_transactions((_statement(outgoing, bank="A"), _statement(incoming, bank="B")))
        self.assertIsNone(normalized[0].internal_transfer_id)
        self.assertIsNone(normalized[1].internal_transfer_id)

    def test_each_row_can_match_only_one_transfer(self) -> None:
        first_out = _transaction(amount="-40.00", direction="debit", description="Internal transfer", bank="A")
        first_in = _transaction(amount="40.00", direction="credit", description="Internal transfer", bank="B", row=3)
        second_in = _transaction(amount="40.00", direction="credit", description="Internal transfer", bank="C", row=4)
        normalized = normalize_transactions((
            _statement(first_out, bank="A"),
            _statement(first_in, bank="B"),
            _statement(second_in, bank="C"),
        ))
        self.assertEqual(normalized[0].internal_transfer_id, normalized[1].internal_transfer_id)
        self.assertIsNone(normalized[2].internal_transfer_id)

    def test_income_requires_specific_wording_and_bank_fee_is_deterministic(self) -> None:
        incoming = _transaction(amount="50.00", direction="credit", description="Incoming transfer")
        fee = _transaction(amount="-2.00", direction="debit", description="Monthly bank fee", row=3)
        classified = classify_transactions(normalize_transactions((_statement(incoming, fee, closing="148.00"),)))
        self.assertEqual(classified[0].classification.category, ClassificationCategory.UNCLASSIFIED)
        self.assertEqual(classified[1].classification.category, ClassificationCategory.BANK_FEE)

    def test_fx_requires_a_date_specific_user_supplied_rate_and_source(self) -> None:
        row = _transaction(amount="10.00", direction="credit", currency="USD")
        statement = _statement(row, currency="USD")
        without_rate = normalize_transactions((statement,))[0]
        with_rate = normalize_transactions(
            (statement,), fx_rates={("USD", date(2025, 1, 10)): (Decimal("0.92"), "ECB rate 2025-01-10")}
        )[0]
        self.assertIsNone(without_rate.amount_eur)
        self.assertEqual(with_rate.amount_eur, Decimal("9.20"))
        self.assertEqual(with_rate.fx_rate_source, "ECB rate 2025-01-10")

    def test_reconciliation_checks_statement_equation_without_decimal_context_rounding(self) -> None:
        statement = _statement(
            _transaction(amount="0.01", direction="credit"),
            opening="100000000000000000000.00",
            closing="100000000000000000000.00",
        )
        with localcontext() as context:
            context.prec = 3
            result = reconcile_statements((statement,))[0]
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(result.difference, Decimal("0.01"))

    def test_reconciliation_requires_direction_and_balances(self) -> None:
        statement = _statement(
            _transaction(amount="15.00", direction=None),
            closing="115.00",
        )
        result = reconcile_statements((statement,))[0]
        self.assertEqual(result.status, "unresolved")
        self.assertIsNone(result.calculated_closing_balance)
        self.assertTrue(any("Ambiguous" in issue for issue in result.issues))

    def test_confirmed_classification_rule_is_persisted_with_private_permissions(self) -> None:
        row = classify_transactions(normalize_transactions((
            _statement(_transaction(amount="-22.00", direction="debit", counterparty="Trusted vendor")),
        )))[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            store = ClassificationRuleStore(path)
            store.approve(row, ClassificationCategory.OPERATING_EXPENSE)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            reloaded = ClassificationRuleStore(path)
            result = classify_transactions((row,), rule_store=reloaded)[0]
            self.assertEqual(result.classification.status, "user_confirmed")
            different_amount = normalize_transactions((
                _statement(_transaction(amount="-44.00", direction="debit", counterparty="Trusted vendor")),
            ))[0]
            conservative = classify_transactions((different_amount,), rule_store=reloaded)[0]
            self.assertEqual(conservative.classification.category, ClassificationCategory.UNCLASSIFIED)


if __name__ == "__main__":
    unittest.main()
