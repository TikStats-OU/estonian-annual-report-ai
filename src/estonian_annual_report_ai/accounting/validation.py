"""Basic deterministic checks over generated statements."""

from dataclasses import dataclass
from datetime import date

from .ledger import Ledger
from .statements import balance_sheet, trial_balance


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True, slots=True)
class ValidationReport:
    period_start: date
    period_end: date
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)


def validate_annual_report(
    ledger: Ledger, period_start: date, period_end: date
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    if period_end < period_start:
        issues.append(
            ValidationIssue(
                code="invalid_reporting_period",
                message="Period end cannot be earlier than period start.",
            )
        )
        return ValidationReport(period_start, period_end, tuple(issues))

    trial = trial_balance(ledger, period_end)
    if trial.total_debits != trial.total_credits:
        issues.append(
            ValidationIssue(
                code="unbalanced_trial_balance",
                message=(
                    f"Trial balance debit totals ({trial.total_debits}) do not equal "
                    f"credit totals ({trial.total_credits})."
                ),
            )
        )

    statement = balance_sheet(ledger, period_end, period_start)
    if not statement.is_balanced:
        issues.append(
            ValidationIssue(
                code="balance_sheet_out_of_balance",
                message=(
                    f"Assets ({statement.total_assets}) do not equal "
                    f"liabilities and equity "
                    f"({statement.total_liabilities_and_equity}). Check opening "
                    "balances and whether earlier income and expense balances were "
                    "closed into retained earnings."
                ),
            )
        )
    return ValidationReport(period_start, period_end, tuple(issues))
