"""Protocol-level smoke test for the optional local MCP server."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

from mcp import Client

_TEST_DATA_DIR = tempfile.TemporaryDirectory(prefix="annual-report-mcp-tests-")
os.environ["ESTONIAN_REPORT_DATA_DIR"] = _TEST_DATA_DIR.name
os.environ["ESTONIAN_REPORT_COMPANY_NAME"] = "Example Company OÜ"
os.environ["ESTONIAN_REPORT_REGISTRY_CODE"] = "EXAMPLE-123"
os.environ["ESTONIAN_REPORT_YEAR"] = "2025"

_SERVER_PATH = Path(__file__).parents[1] / "mcp" / "server.py"
_SPEC = importlib.util.spec_from_file_location("annual_report_mcp_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
mcp = _MODULE.mcp


class MCPServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_statement_tool_is_exposed_and_returns_structured_data(self) -> None:
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            self.assertTrue(
                {
                    "generate_financial_statements",
                    "import_documents",
                    "parse_bank_statements",
                    "normalize_transactions",
                    "classify_transactions",
                    "get_unclassified_transactions",
                    "approve_classification",
                    "reconcile_accounts",
                    "generate_annual_report",
                    "validate_annual_report",
                    "validate_pre_filing",
                    "get_missing_information",
                    "evaluate_accounting_issue",
                    "approve_accounting_recommendation",
                    "detect_stripe_reports",
                    "get_missing_stripe_reports",
                    "generate_stripe_download_instructions",
                    "import_stripe_report",
                    "reconcile_stripe_balances",
                    "reconcile_stripe_fees",
                    "reconcile_stripe_activity",
                    "match_stripe_bank_transfers",
                    "get_stripe_reconciliation_status",
                }.issubset(tool_names)
            )
            result = await client.call_tool(
                "generate_financial_statements",
                {
                    "accounts": [
                        {"code": "bank", "name": "Bank", "type": "asset"},
                        {"code": "capital", "name": "Capital", "type": "equity"},
                    ],
                    "entries": [
                        {
                            "entry_id": "opening",
                            "entry_date": "2025-01-01",
                            "description": "Synthetic opening balances",
                            "lines": [
                                {"account_code": "bank", "debit": "100.00"},
                                {"account_code": "capital", "credit": "100.00"},
                            ],
                        }
                    ],
                    "period_start": "2025-01-01",
                    "period_end": "2025-12-31",
                },
            )
        data = result.structured_content
        self.assertTrue(data["validation"]["is_valid"])
        self.assertEqual(data["balance_sheet"]["total_assets"], "100.00")
        self.assertTrue(data["balance_sheet"]["is_balanced"])

    async def test_pre_filing_readiness_tool_uses_synthetic_profile(self) -> None:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "validate_pre_filing",
                {
                    "profile": {
                        "company_registry_code": "EXAMPLE-123",
                        "jurisdiction": "EE",
                        "reporting_category": "synthetic category",
                        "profile_complete": True,
                        "profile_source": "Synthetic filing specification v1",
                        "forms": [{"form_id": "balance-sheet"}],
                        "fields": [{"field_id": "cash", "form_id": "balance-sheet", "value": "10.00"}],
                        "accounting_reconciled": True,
                    }
                },
            )
        self.assertEqual(result.structured_content["status"], "READY")


if __name__ == "__main__":
    unittest.main()
