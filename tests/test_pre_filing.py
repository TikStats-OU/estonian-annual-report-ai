"""Synthetic checks for the jurisdiction-neutral filing readiness gate."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from estonian_annual_report_ai.application import AccountingWorkflow
from estonian_annual_report_ai.reporting import (
    EvidenceRequirement,
    FieldRequirement,
    FilingQuestion,
    FormRequirement,
    PreFilingContext,
    ReadinessStatus,
    validate_pre_filing,
)


def complete_profile() -> PreFilingContext:
    return PreFilingContext(
        jurisdiction="EE",
        reporting_category="small undertaking",
        profile_complete=True,
        profile_source="Synthetic jurisdiction filing specification v1",
        forms=(FormRequirement("balance-sheet"), FormRequirement("income-statement")),
        fields=(FieldRequirement("cash", "balance-sheet", "10.00"),),
        accounting_reconciled=True,
        evidence=(EvidenceRequirement("bank-statement", "verified"),),
    )


class PreFilingValidationTests(unittest.TestCase):
    def test_ready_for_synthetic_company_when_profile_is_complete(self) -> None:
        result = validate_pre_filing(complete_profile())
        self.assertEqual(result.status, ReadinessStatus.READY)
        self.assertEqual(result.verified_evidence, ("bank-statement",))

    def test_material_gaps_block_but_nonmaterial_gaps_are_partial(self) -> None:
        blocked = validate_pre_filing(PreFilingContext(
            jurisdiction="EE",
            reporting_category="small undertaking",
            profile_complete=True,
            profile_source="Synthetic jurisdiction filing specification v1",
            forms=(FormRequirement("balance-sheet"),),
            fields=(FieldRequirement("cash", "balance-sheet", None),),
            accounting_reconciled=True,
        ))
        self.assertEqual(blocked.status, ReadinessStatus.BLOCKED)

        partial = validate_pre_filing(PreFilingContext(
            jurisdiction="EE",
            reporting_category="small undertaking",
            profile_complete=True,
            profile_source="Synthetic jurisdiction filing specification v1",
            forms=(FormRequirement("balance-sheet"),),
            fields=(FieldRequirement("optional-note", "balance-sheet", None, material=False),),
            accounting_reconciled=True,
        ))
        self.assertEqual(partial.status, ReadinessStatus.PARTIALLY_READY)

    def test_user_question_is_returned_once_but_remains_a_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = AccountingWorkflow(
                company_name="Synthetic AS",
                registry_code="12345678",
                period_start=date(2025, 1, 1),
                period_end=date(2025, 12, 31),
                rules_path=root / "rules.json",
                evidence_registry_path=root / "evidence.json",
            )
            profile = PreFilingContext(
                jurisdiction="EE",
                reporting_category="large undertaking",
                profile_complete=True,
                profile_source="Synthetic jurisdiction filing specification v1",
                forms=(FormRequirement("balance-sheet"),),
                fields=(FieldRequirement("employee-fte", "labor-note", None),),
                accounting_reconciled=True,
                questions=(FilingQuestion("employment-period", "What was the employee's work period?"),),
            )
            first = workflow.validate_pre_filing(profile)
            second = workflow.validate_pre_filing(profile)
            self.assertEqual(first["status"], "BLOCKED")
            self.assertEqual(len(first["questions"]), 1)
            self.assertEqual(second["questions"], ())
            self.assertEqual(second["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
