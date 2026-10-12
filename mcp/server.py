"""Compatibility launcher for the packaged local stdio MCP server.

Works from a source checkout without `pip install -e .`: the `src` directory
is added to the import path when the package is not installed.
"""

import sys
from pathlib import Path

try:
    from estonian_annual_report_ai.mcp_server import main, mcp
except ModuleNotFoundError as exc:
    if exc.name != "estonian_annual_report_ai":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from estonian_annual_report_ai.mcp_server import main, mcp

__all__ = ["main", "mcp"]

if __name__ == "__main__":
    main()
