"""Local Stripe report checklist and conservative reconciliation helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from estonian_annual_report_ai.documents.common import normalize_label
from estonian_annual_report_ai.documents.stripe_reports import (
    StripeReport,
    parse_stripe_report,
    reconcile_stripe_summary,
)
from estonian_annual_report_ai.transactions import NormalizedTransaction

_REPORT_GUIDANCE: dict[str, dict[str, str]] = {
    "balance_summary": {
        "name": "Balance Summary",
        "purpose": "Check the Stripe opening and closing balance, activity, fees, and payouts.",
        "location": "Stripe Dashboard → Reporting / Reports → Balance Summary → Export.",
        "format": "CSV preferred; PDF is acceptable for a summary review.",
        "sigma_required": "No.",
        "alternative": "If the menu differs, find Balance Summary under Reporting/Reports. The standard Dashboard export does not need Sigma.",
        "source": "https://support.stripe.com/questions/exporting-a-report-of-all-transactions",
    },
    "fees_summary": {
        "name": "All Fees / Fees report",
        "purpose": "Break down fees for payment processing, Billing, disputes, and other products.",
        "location": "Stripe Dashboard → Reporting → Fees → Export.",
        "format": "CSV.",
        "sigma_required": "No.",
        "alternative": "Use the standard Fees report in the Dashboard. Its totals detail the Balance Summary fees and must not be added a second time.",
        "source": "https://support.stripe.com/questions/exporting-fees-paid?locale=en-GB",
    },
    "balance_transactions": {
        "name": "Balance transactions / itemized activity",
        "purpose": "Review charges, refunds, disputes, adjustments, top-ups, and payouts transaction by transaction.",
        "location": "Stripe Dashboard → Balances → All Activity → Export; or Balance Summary → Balance change from activity → Itemised → All categories → All columns.",
        "format": "CSV. Export payouts separately when using Balance change from activity.",
        "sigma_required": "No.",
        "alternative": "All Activity includes payouts; Balance change from activity does not. Compare exports in the same timezone.",
        "source": "https://support.stripe.com/questions/compare-dashboard-exports-to-financial-reports?locale=en-GB",
    },
}


class StripeReportingAssistant:
    """In-memory local import session; exact file duplicates are skipped."""

    def __init__(self, *, period_start: date, period_end: date, currency: str = "EUR") -> None:
        self.period_start = period_start
        self.period_end = period_end
        self.currency = currency.upper()
        self.reports: list[StripeReport] = []
        self.duplicate_imports: int = 0

    def detect_report(self, path: str | Path) -> dict[str, Any]:
        return self._report_summary(parse_stripe_report(path))

    def import_report(self, path: str | Path) -> dict[str, Any]:
        report = parse_stripe_report(path)
        if any(item.source.sha256 == report.source.sha256 for item in self.reports):
            self.duplicate_imports += 1
            return {"status": "duplicate_file_skipped", **self._report_summary(report)}
        self.reports.append(report)
        return {"status": "imported", **self._report_summary(report)}

    def detected_reports(self) -> dict[str, Any]:
        return {
            "reports": [self._report_summary(report) for report in self.reports],
            "duplicate_files_skipped": self.duplicate_imports,
            "issues": ([{"code": "duplicate_file_skipped", "severity": "informational"}]
                       if self.duplicate_imports else []),
        }

    def missing_reports(self) -> dict[str, Any]:
        result: list[dict[str, Any]] = []
        for kind in _REPORT_GUIDANCE:
            matching = [report for report in self.reports if report.kind == kind]
            if not matching:
                result.append({
                    "report_type": kind,
                    "status": "missing",
                    "severity": "critical" if kind == "balance_summary" else "warning",
                    "guidance": self._instruction(kind),
                })
                continue
            if len(matching) > 1:
                result.append({
                    "report_type": kind,
                    "status": "multiple_reports_require_period_selection",
                    "severity": "warning",
                    "filenames": [report.source.filename for report in matching],
                    "note": "Overlapping exports are not merged automatically.",
                })
            for report in matching:
                report_status = self._coverage_status(report)
                if report_status["status"] != "verified":
                    result.append({
                        "report_type": kind,
                        "status": report_status["status"],
                        "severity": "critical" if report_status["period_matches"] is False else "warning",
                        "filename": report.source.filename,
                        "coverage": report_status,
                        "guidance": self._instruction(kind),
                    })
        unknown = [report for report in self.reports if report.kind == "unknown"]
        for report in unknown:
            result.append({
                "report_type": "unknown",
                "status": "unrecognized_report",
                "severity": "warning",
                "filename": report.source.filename,
                "issue_codes": [issue.code for issue in report.issues],
            })
        return {
            "expected_period": {"start": self.period_start.isoformat(), "end": self.period_end.isoformat()},
            "expected_currency": self.currency,
            "missing_or_unverified": result,
            "complete": not result,
        }

    def download_instructions(self, report_type: str | None = None) -> dict[str, Any]:
        if report_type is not None and report_type not in _REPORT_GUIDANCE:
            raise ValueError("Unknown Stripe report type.")
        selected = [report_type] if report_type else [item["report_type"] for item in self.missing_reports()["missing_or_unverified"] if item["report_type"] in _REPORT_GUIDANCE]
        return {
            "period": {"start": self.period_start.isoformat(), "end": self.period_end.isoformat()},
            "currency": self.currency,
            "timezone": "Use the same timezone in all exports; UTC is recommended for comparing Stripe Dashboard exports.",
            "instructions": [self._instruction(kind) for kind in dict.fromkeys(selected)],
            "note": "Stripe menu labels and report availability can vary by account and Dashboard version. Standard Dashboard exports do not require Sigma. Export currencies separately when needed.",
        }

    def reconcile_balances(self) -> dict[str, Any]:
        report = self._one_report("balance_summary")
        if report is None:
            return {"status": "missing_balance_summary", "reconciled": None}
        if report.currencies != (self.currency,):
            return {
                "status": "currency_unverified",
                "reconciled": None,
                "expected_currency": self.currency,
                "reported_currencies": list(report.currencies),
                "accounting_classification": "Currency totals cannot be combined or converted without sourced exchange rates.",
            }
        values = _summary_values(report)
        fee_total = self._fee_total()
        duplicate_rows = sum(row.duplicate_of is not None for row in report.rows)
        result = reconcile_stripe_summary(
            values["opening_balance"], values["gross_activity"], values["fees"],
            values["payouts"], values["closing_balance"], fee_total,
        )
        return {
            "status": (
                "duplicate_rows_require_review" if duplicate_rows
                else "reconciled" if result.balance_reconciled
                else "unresolved"
            ),
            "source": _source_summary(report),
            "currency": report.currencies[0] if len(report.currencies) == 1 else None,
            "period": _period_summary(report),
            "opening_balance": _money(result.opening_balance),
            "gross_activity_before_fees": _money(result.gross_activity),
            "fees_signed_as_reported": _money(result.fees),
            "net_activity": _money(values["net_activity"]),
            "payouts_signed_as_reported": _money(result.payouts),
            "closing_balance": _money(result.closing_balance),
            "calculated_closing_balance": _money(result.calculated_closing_balance),
            "balance_difference": _money(result.balance_difference),
            "fee_report_total_including_reported_tax": _money(result.fee_report_total),
            "fee_difference_by_absolute_value": _money(result.fee_difference),
            "fees_reconciled": result.fees_reconciled,
            "accounting_classification": "Not inferred from the summary. Gross activity is not assumed to be revenue; payouts are not revenue.",
            "source_issues": [issue.code for issue in report.issues],
            "duplicate_rows_flagged": duplicate_rows,
            "duplicate_rows_automatically_removed": False,
        }

    def reconcile_fees(self) -> dict[str, Any]:
        report = self._one_report("fees_summary")
        summary = self.reconcile_balances()
        if report is None:
            return {"status": "missing_fees_summary", "fees_total": None}
        amount_total, tax_total, combined_total, duplicates = _fee_totals(report, self.currency)
        balance_fees = _decimal(summary.get("fees_signed_as_reported"))
        difference = abs(balance_fees) - combined_total if balance_fees is not None and combined_total is not None else None
        if balance_fees is None:
            status = "awaiting_balance_summary"
        elif combined_total is None:
            status = "currency_unverified"
        else:
            status = "reconciled" if difference == 0 else "unresolved"
        return {
            "status": status,
            "source": _source_summary(report),
            "period": _period_summary(report),
            "currencies": list(report.currencies),
            "currency_reconciled": self.currency if combined_total is not None else None,
            "fee_amount_total": _money(amount_total),
            "reported_tax_total": _money(tax_total),
            "total_including_tax": _money(combined_total),
            "balance_summary_fees_absolute": _money(abs(balance_fees)) if balance_fees is not None else None,
            "difference": _money(difference),
            "duplicate_rows_flagged": duplicates,
            "duplicate_rows_automatically_removed": False,
            "accounting_note": "Fees report details the fee component already shown in Balance Summary; do not expense both totals separately.",
        }

    def reconcile_activity(self) -> dict[str, Any]:
        """Summarize raw itemized categories and compare only explicit signed totals."""
        report = self._one_report("balance_transactions")
        if report is None:
            return {"status": "missing_balance_transactions", "reconciled": None}
        if len(report.currencies) != 1 or report.currencies[0] != self.currency:
            return {
                "status": "currency_unverified",
                "reconciled": None,
                "reported_currencies": list(report.currencies),
                "expected_currency": self.currency,
            }
        aggregates: dict[str, dict[str, Any]] = {}
        total = {"gross": Decimal(0), "fee": Decimal(0), "net": Decimal(0)}
        known = {"gross": 0, "fee": 0, "net": 0}
        duplicates = 0
        for row in report.rows:
            label = _activity_label(row)
            bucket = "payouts" if "payout" in label else "refunds" if "refund" in label else "disputes" if any(word in label for word in ("dispute", "chargeback")) else "other"
            aggregate = aggregates.setdefault(bucket, {"count": 0, "gross": Decimal(0), "fee": Decimal(0), "net": Decimal(0), "gross_rows": 0, "fee_rows": 0, "net_rows": 0})
            aggregate["count"] += 1
            duplicates += row.duplicate_of is not None
            for metric, columns in {
                "gross": ("gross", "gross amount"),
                "fee": ("fee", "fee amount"),
                "net": ("net", "net amount"),
            }.items():
                value = _first_decimal(row, columns)
                if value is not None:
                    aggregate[metric] += value
                    aggregate[f"{metric}_rows"] += 1
                    if bucket != "payouts":
                        total[metric] += value
                        known[metric] += 1
        balance = self.reconcile_balances()
        expected = {
            "gross": _decimal(balance.get("gross_activity_before_fees")),
            "fee": _decimal(balance.get("fees_signed_as_reported")),
            "net": _decimal(balance.get("net_activity")),
        }
        non_payout_count = len(report.rows) - aggregates.get("payouts", {}).get("count", 0)
        comparisons = {}
        for metric, expected_value in expected.items():
            complete_total = non_payout_count > 0 and known[metric] == non_payout_count
            comparisons[metric] = {
                "itemized_total": _money(total[metric]) if complete_total else None,
                "balance_summary_total": _money(expected_value),
                "difference": _money(total[metric] - expected_value) if expected_value is not None and complete_total else None,
            }
        payout_rows = aggregates.get("payouts", {})
        summary_payouts = _decimal(balance.get("payouts_signed_as_reported"))
        payout_total = (
            payout_rows.get("net")
            if payout_rows.get("count", 0) and payout_rows.get("net_rows", 0) == payout_rows.get("count", 0)
            else None
        )
        payout_difference = payout_total - summary_payouts if payout_total is not None and summary_payouts is not None else None
        unmatched = []
        for metric, comparison in comparisons.items():
            if comparison["difference"] not in (None, "0.00"):
                unmatched.append(f"{metric}_activity")
        if payout_difference not in (None, Decimal(0)):
            unmatched.append("payouts")
        has_comparable_mismatch = any(item["difference"] not in (None, "0.00") for item in comparisons.values())
        status = (
            "duplicate_rows_require_review" if duplicates
            else "unresolved" if has_comparable_mismatch
            else "reconciled" if all(item["difference"] is not None for item in comparisons.values()) and payout_difference is not None
            else "partially_reconciled"
        )
        return {
            "status": status,
            "source": _source_summary(report),
            "period": _period_summary(report),
            "currency": self.currency,
            "activity_categories": {
                bucket: {
                    "count": values["count"],
                    "gross": _money(values["gross"]) if values["gross_rows"] == values["count"] else None,
                    "fees": _money(values["fee"]) if values["fee_rows"] == values["count"] else None,
                    "net": _money(values["net"]) if values["net_rows"] == values["count"] else None,
                }
                for bucket, values in sorted(aggregates.items())
            },
            "non_payout_comparisons": comparisons,
            "itemized_payouts": {
                "count": payout_rows.get("count", 0),
                "net_total": _money(payout_total),
                "balance_summary_total": _money(summary_payouts),
                "difference": _money(payout_difference),
            },
            "unmatched_categories": unmatched,
            "duplicate_rows_flagged": duplicates,
            "duplicate_rows_automatically_removed": False,
            "accounting_classification": "Category labels are used only to reconcile report rows; no revenue/expense or tax classification is inferred.",
        }

    def match_bank_transfers(self, bank_transactions: tuple[NormalizedTransaction, ...]) -> dict[str, Any]:
        stripe_rows = tuple(
            row for row in bank_transactions
            if "stripe" in " ".join((row.counterparty or "", row.description or "")).casefold()
            and row.amount is not None and row.currency == self.currency
        )
        incoming = sum((row.amount for row in stripe_rows if row.amount > 0), Decimal(0))
        outgoing = sum((row.amount for row in stripe_rows if row.amount < 0), Decimal(0))
        balance = self.reconcile_balances()
        payouts = _decimal(balance.get("payouts_signed_as_reported"))
        payout_abs = abs(payouts) if payouts is not None else None
        net_bank_movement = incoming + outgoing
        net_difference = net_bank_movement - payout_abs if payout_abs is not None else None
        directional_difference = incoming - payout_abs if payout_abs is not None else None
        return {
            "status": "net_amount_matches; direction_and_classification_pending" if net_difference == 0 and directional_difference != 0 else "reconciled" if net_difference == 0 else "unresolved",
            "bank_receipts_from_stripe": _money(incoming),
            "bank_payments_to_stripe": _money(abs(outgoing)),
            "net_bank_movement": _money(net_bank_movement),
            "stripe_reported_payouts": _money(payout_abs),
            "net_amount_difference": _money(net_difference),
            "directional_incoming_vs_payout_difference": _money(directional_difference),
            "directional_explanation": "Outgoing payments to Stripe are listed separately and are not treated as payouts from Stripe. A net amount match does not prove transaction-level settlement or accounting purpose.",
            "transactions": [
                {
                    "transaction_id": row.transaction_id,
                    "date": row.transaction_date.isoformat() if row.transaction_date else None,
                    "amount": _money(row.amount),
                    "currency": row.currency,
                    "flow": "bank_to_stripe" if row.amount < 0 else "stripe_to_bank_candidate",
                    "classification": "pending; no expense or revenue inferred",
                    "source": {"filename": row.source.filename, "sha256": row.source.sha256, "row": row.source.row_number},
                }
                for row in stripe_rows
            ],
        }

    def reconciliation_status(self, bank_transactions: tuple[NormalizedTransaction, ...] = ()) -> dict[str, Any]:
        balances = self.reconcile_balances()
        fees = self.reconcile_fees()
        activity = self.reconcile_activity()
        transfers = self.match_bank_transfers(bank_transactions)
        issues = [
            {"code": item["status"], "severity": item["severity"], "report_type": item["report_type"]}
            for item in self.missing_reports()["missing_or_unverified"]
        ]
        if balances.get("status") not in ("reconciled", "missing_balance_summary"):
            issues.append({"code": "stripe_balance_unresolved", "severity": "critical"})
        if fees.get("status") not in ("reconciled", "missing_fees_summary"):
            issues.append({"code": "stripe_fees_unresolved", "severity": "critical"})
        if activity.get("status") in ("unresolved", "duplicate_rows_require_review", "currency_unverified"):
            issues.append({"code": "stripe_itemized_activity_unresolved", "severity": "critical"})
        elif activity.get("status") == "partially_reconciled":
            issues.append({"code": "stripe_itemized_activity_partial", "severity": "warning"})
        if transfers.get("net_amount_difference") not in (None, "0.00"):
            issues.append({"code": "stripe_bank_net_mismatch", "severity": "critical"})
        if transfers.get("directional_incoming_vs_payout_difference") not in (None, "0.00"):
            issues.append({"code": "stripe_bank_direction_unresolved", "severity": "critical"})
        if balances.get("gross_activity_before_fees") not in (None, "0.00"):
            issues.append({"code": "stripe_activity_classification_pending", "severity": "warning"})
        if transfers.get("transactions"):
            issues.append({"code": "stripe_bank_transaction_purpose_pending", "severity": "warning"})
        if self.duplicate_imports:
            issues.append({"code": "duplicate_file_skipped", "severity": "informational"})
        blocking = any(issue["severity"] == "critical" for issue in issues)
        unresolved_material = any(issue["severity"] in ("critical", "warning") for issue in issues)
        return {
            "report_inventory": self.detected_reports(),
            "missing_reports": self.missing_reports(),
            "balance_reconciliation": balances,
            "fee_reconciliation": fees,
            "itemized_activity_reconciliation": activity,
            "bank_transfer_matching": transfers,
            "accounting_classification": "Separate from numerical reconciliation; unresolved transactions remain unclassified.",
            "issues": issues,
            "preliminary_calculations_allowed": not blocking,
            "ready_for_submission": not unresolved_material,
        }

    def _one_report(self, kind: str) -> StripeReport | None:
        matches = [report for report in self.reports if report.kind == kind]
        return matches[-1] if len(matches) == 1 else None

    def _fee_total(self) -> Decimal | None:
        report = self._one_report("fees_summary")
        if report is None:
            return None
        return _fee_totals(report, self.currency)[2]

    def _coverage_status(self, report: StripeReport) -> dict[str, Any]:
        period_matches = None
        if report.period_start is not None and report.period_end is not None:
            period_matches = report.period_start == self.period_start and report.period_end == self.period_end
        currency_matches = self.currency in report.currencies if report.currencies else None
        single_summary_currency = len(report.currencies) == 1 if report.kind == "balance_summary" else True
        if period_matches is False or currency_matches is False or not single_summary_currency:
            status = "period_or_currency_mismatch"
        elif period_matches is None or currency_matches is None:
            status = "coverage_not_fully_verifiable"
        else:
            status = "verified"
        return {
            "status": status,
            "period_start": report.period_start.isoformat() if report.period_start else None,
            "period_end": report.period_end.isoformat() if report.period_end else None,
            "period_matches": period_matches,
            "currencies": list(report.currencies),
            "expected_currency_present": currency_matches,
            "summary_has_single_currency": single_summary_currency,
            "note": "Report filenames are not treated as proof of the selected date range.",
        }

    def _instruction(self, kind: str) -> dict[str, Any]:
        return {
            "report_type": kind,
            **_REPORT_GUIDANCE[kind],
            "required_date_range": {"start": self.period_start.isoformat(), "end": self.period_end.isoformat()},
            "required_currency": self.currency,
            "timezone": "Use the same timezone for each export; UTC is recommended when comparing Dashboard exports.",
        }

    @staticmethod
    def _report_summary(report: StripeReport) -> dict[str, Any]:
        duplicate_rows = sum(row.duplicate_of is not None for row in report.rows)
        return {
            **_source_summary(report),
            "kind": report.kind,
            "period": _period_summary(report),
            "currencies": list(report.currencies),
            "row_count": len(report.rows),
            "duplicate_rows_flagged": duplicate_rows,
            "headers": list(report.headers),
            "issues": [{"code": issue.code, "severity": "warning"} for issue in report.issues],
        }


def _summary_values(report: StripeReport) -> dict[str, Decimal | None]:
    values: dict[str, Decimal | None] = {
        "opening_balance": None, "gross_activity": None, "fees": None,
        "net_activity": None, "payouts": None, "closing_balance": None,
    }
    category_map = {
        "starting_balance": "opening_balance", "opening_balance": "opening_balance",
        "activity_gross": "gross_activity", "gross_activity": "gross_activity",
        "activity_fee": "fees", "fees": "fees", "activity": "net_activity", "net_activity": "net_activity",
        "payouts": "payouts", "payout": "payouts",
        "ending_balance": "closing_balance", "closing_balance": "closing_balance",
    }
    for row in report.rows:
        category = normalize_label(row.get("category") or "").replace(" ", "_")
        field = category_map.get(category)
        if field:
            amount = row.decimal("net_amount")
            values[field] = amount if amount is not None else row.decimal("amount")
    if not any(values.values()) and report.rows:
        row = report.rows[0]
        candidates = {
            "opening_balance": ("opening balance", "starting balance"),
            "gross_activity": ("gross activity", "activity gross", "gross"),
            "fees": ("fees", "fee", "activity fee"),
            "net_activity": ("net activity", "activity"),
            "payouts": ("payouts", "payout"),
            "closing_balance": ("closing balance", "ending balance"),
        }
        for field, columns in candidates.items():
            values[field] = next((row.decimal(column) for column in columns if row.decimal(column) is not None), None)
    return values


def _fee_totals(report: StripeReport, currency: str) -> tuple[Decimal | None, Decimal | None, Decimal | None, int]:
    amount_total = Decimal(0)
    tax_total = Decimal(0)
    duplicates = 0
    included = 0
    for row in report.rows:
        row_currency = row.get("currency")
        if row_currency is None and len(report.currencies) == 1:
            row_currency = report.currencies[0]
        if row_currency is None or row_currency.upper() != currency:
            duplicates += row.duplicate_of is not None
            continue
        included += 1
        amount = row.decimal("amount")
        tax = row.decimal("tax")
        if amount is not None:
            amount_total += amount
        if tax is not None:
            tax_total += tax
        duplicates += row.duplicate_of is not None
    if not included:
        return None, None, None, duplicates
    return amount_total, tax_total, amount_total + tax_total, duplicates


def _activity_label(row: Any) -> str:
    values = (
        row.get("reporting category"),
        row.get("type"),
        row.get("transaction type"),
        row.get("description"),
    )
    return " ".join(normalize_label(value) for value in values if value)


def _first_decimal(row: Any, columns: tuple[str, ...]) -> Decimal | None:
    return next((value for column in columns if (value := row.decimal(column)) is not None), None)


def _source_summary(report: StripeReport) -> dict[str, str]:
    return {"filename": report.source.filename, "sha256": report.source.sha256}


def _period_summary(report: StripeReport) -> dict[str, str | None]:
    return {
        "start": report.period_start.isoformat() if report.period_start else None,
        "end": report.period_end.isoformat() if report.period_end else None,
    }


def _money(value: Decimal | None) -> str | None:
    # Adding zero turns a negative zero into "0.00" so string comparisons stay reliable.
    return format(value.quantize(Decimal("0.01")) + 0, "f") if value is not None else None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
