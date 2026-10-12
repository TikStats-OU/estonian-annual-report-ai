"""Synthetic tests for deterministic accounting issue decisions."""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from estonian_annual_report_ai.accounting import (
    AccountingIssue,
    DecisionStage,
    Materiality,
    MaterialityPolicy,
)
from estonian_annual_report_ai.application import AccountingWorkflow


class AccountingDecisionEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = MaterialityPolicy(
            review_threshold=Decimal(100),
            critical_threshold=Decimal(1000),
            critical_flags=("filing_blocker",),
            review_flags=("estimate",),
        )

    def test_material_issue_without_evidence_escalates_without_writeoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = AccountingWorkflow(
                company_name="Synthetic OÜ",
                registry_code="00000000",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                rules_path=root / "rules.json",
            )
            recommendation = workflow.evaluate_accounting_issue(
                AccountingIssue(
                    issue_id="synthetic-dispute",
                    description="Determine whether a dispute balance was recovered.",
                    amount=Decimal(1200),
                    evidence_refs=("synthetic-stripe-export.csv:row-4",),
                    evidence_complete=False,
                ),
                self.policy,
            )
            self.assertEqual(recommendation.materiality, Materiality.CRITICAL)
            self.assertEqual(recommendation.stage, DecisionStage.ESCALATE)
            self.assertEqual(recommendation.action, "ESCALATE")
            self.assertIn("synthetic-dispute", workflow.get_evidence_registry()["decisions"])

    def test_estimate_requires_approval_and_approval_does_not_post(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = AccountingWorkflow(
                company_name="Synthetic AS",
                registry_code="11111111",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                rules_path=root / "rules.json",
            )
            before = workflow.latest_ledger
            result = workflow.evaluate_accounting_issue(
                AccountingIssue(
                    issue_id="synthetic-useful-life",
                    description="Set remaining useful life for an asset.",
                    amount=Decimal(400),
                    evidence_refs=("synthetic-fixed-asset-register.csv:row-2",),
                    evidence_complete=True,
                    recommended_treatment="Approve a documented remaining useful life.",
                    confidence=None,
                    requires_estimate_or_judgment=True,
                    qualitative_flags=("estimate",),
                ),
                self.policy,
            )
            self.assertEqual(result.stage, DecisionStage.RECOMMEND)
            self.assertEqual(result.action, "REQUEST_APPROVAL")
            self.assertTrue(result.approval_required)
            stored = workflow.approve_accounting_recommendation(
                "synthetic-useful-life", approved_by="user"
            )
            self.assertEqual(stored["status"], "approved")
            workflow.evaluate_accounting_issue(
                AccountingIssue(
                    issue_id="synthetic-useful-life",
                    description="Set remaining useful life for an asset.",
                    amount=Decimal(400),
                    evidence_refs=("synthetic-fixed-asset-register.csv:row-2",),
                    evidence_complete=True,
                    recommended_treatment="Use an updated documented useful life.",
                    requires_estimate_or_judgment=True,
                    qualitative_flags=("estimate",),
                ),
                self.policy,
            )
            self.assertEqual(
                workflow.get_evidence_registry()["decisions"]["synthetic-useful-life"]["status"],
                "pending",
            )
            self.assertEqual(
                len(workflow.get_evidence_registry()["decisions"]["synthetic-useful-life"]["history"]),
                1,
            )
            self.assertIsNone(before)
            self.assertIsNone(workflow.latest_ledger)

    def test_low_amount_can_still_be_critical_by_qualitative_flag(self) -> None:
        from estonian_annual_report_ai.accounting import evaluate_issue

        recommendation = evaluate_issue(
            AccountingIssue(
                issue_id="synthetic-signing",
                description="A required signing disclosure is missing.",
                amount=Decimal(1),
                qualitative_flags=("filing_blocker",),
            ),
            self.policy,
        )
        self.assertEqual(recommendation.materiality, Materiality.CRITICAL)

    def test_unconfigured_amount_threshold_defaults_to_review(self) -> None:
        from estonian_annual_report_ai.accounting import evaluate_issue

        recommendation = evaluate_issue(
            AccountingIssue(
                issue_id="synthetic-unconfigured-threshold",
                description="A monetary issue without configured thresholds.",
                amount=Decimal(100000),
            ),
            MaterialityPolicy(),
        )
        self.assertEqual(recommendation.materiality, Materiality.REVIEW)

    def test_explicit_qualitative_flag_can_keep_historical_difference_informational(self) -> None:
        from estonian_annual_report_ai.accounting import evaluate_issue

        recommendation = evaluate_issue(
            AccountingIssue(
                issue_id="synthetic-prior-year-rounding",
                description="A documented immaterial prior-year rounding difference.",
                amount=Decimal("0.01"),
                qualitative_flags=("historical_difference",),
            ),
            MaterialityPolicy(informational_flags=("historical_difference",)),
        )
        self.assertEqual(recommendation.materiality, Materiality.INFORMATIONAL)

    def test_supported_deterministic_treatment_resolves_without_posting(self) -> None:
        from estonian_annual_report_ai.accounting import evaluate_issue

        recommendation = evaluate_issue(
            AccountingIssue(
                issue_id="synthetic-reconciled-fee",
                description="Match an evidenced bank fee to its statement row.",
                amount=Decimal(8),
                evidence_refs=("synthetic-bank.csv:row-5", "synthetic-fees.csv:row-2"),
                evidence_complete=True,
                recommended_treatment="Classify once as a bank fee.",
            ),
            self.policy,
        )
        self.assertEqual(recommendation.stage, DecisionStage.RESOLVE)
        self.assertEqual(recommendation.action, "RESOLVE")
        self.assertFalse(recommendation.approval_required)


if __name__ == "__main__":
    unittest.main()
