# Connecting an MCP client

FlaiReport runs as a **local MCP server over stdio**. Any MCP client that can launch a local command can use it. The server reads files on your machine and writes results to your private data directory; it opens no network port.

## 1. Install

Pick one. All of them give you the `estonian-annual-report-ai-mcp` command; Python 3.12 or newer is required.

```sh
# A. No install step: the MCP client runs it through uv (https://docs.astral.sh/uv/)
uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai estonian-annual-report-ai-mcp

# B. Install the command once, isolated from other Python projects
pipx install git+https://github.com/TikStats-OU/estonian-annual-report-ai

# C. From a clone, for development
python -m pip install -e .
```

Use one process and one data directory per company.

## 2. Configure the company

| Variable | Example | Required |
| --- | --- | --- |
| `ESTONIAN_REPORT_COMPANY_NAME` | `Example Company OÜ` | yes |
| `ESTONIAN_REPORT_REGISTRY_CODE` | `EXAMPLE-123` | yes |
| `ESTONIAN_REPORT_YEAR` | `2025` | yes |
| `ESTONIAN_REPORT_PERIOD_START` / `_END` | `2025-01-01` / `2025-12-31` | no, defaults to the calendar year |
| `ESTONIAN_REPORT_DATA_DIR` | `~/.local/share/estonian-annual-report-ai/data` | no |

Keep real values in a local wrapper script or in your client's private settings. Never commit them.

## 3. Register the server in your client

Most clients accept a command plus environment variables. Two command-line examples with synthetic values:

```sh
# Codex CLI
codex mcp add annual-report \
  --env ESTONIAN_REPORT_COMPANY_NAME='Example Company OÜ' \
  --env ESTONIAN_REPORT_REGISTRY_CODE=EXAMPLE-123 \
  --env ESTONIAN_REPORT_YEAR=2025 \
  -- estonian-annual-report-ai-mcp

# Claude Code
claude mcp add annual-report --scope local \
  --env ESTONIAN_REPORT_COMPANY_NAME='Example Company OÜ' \
  --env ESTONIAN_REPORT_REGISTRY_CODE=EXAMPLE-123 \
  --env ESTONIAN_REPORT_YEAR=2025 \
  -- estonian-annual-report-ai-mcp
```

Desktop clients that use a JSON configuration file take the same parts. With uv (option A), nothing else needs to be installed:

```json
{
  "mcpServers": {
    "annual-report": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/TikStats-OU/estonian-annual-report-ai",
        "estonian-annual-report-ai-mcp"
      ],
      "env": {
        "ESTONIAN_REPORT_COMPANY_NAME": "Example Company OÜ",
        "ESTONIAN_REPORT_REGISTRY_CODE": "EXAMPLE-123",
        "ESTONIAN_REPORT_YEAR": "2025"
      }
    }
  }
}
```

With option B or C, use `"command": "estonian-annual-report-ai-mcp"` and no `args`. If the client cannot find the command, give its full path (`which estonian-annual-report-ai-mcp`).

To run it in Docker instead, the client launches `docker` with `"args": ["run", "-i", "--rm", "--read-only", "--tmpfs", "/tmp", "-e", "ESTONIAN_REPORT_COMPANY_NAME", "-e", "ESTONIAN_REPORT_REGISTRY_CODE", "-e", "ESTONIAN_REPORT_YEAR", "-v", "/path/to/private-data:/data", "-v", "/path/to/input:/input:ro", "estonian-annual-report-ai:local"]`. Keep `-i` and never add `-t`: a terminal would corrupt the JSON stream. File paths you pass to the tools must then be container paths under `/input`.

Clients that only connect to **remote** MCP servers over HTTPS cannot launch this local process. FlaiReport deliberately ships no HTTP transport, because a remote server would need authentication and per-user storage first.

## 4. Check it works

1. Start with the synthetic profile and synthetic files, never real statements.
2. Ask the client to list the tools: you should see `import_documents`, `reconcile_accounts`, `generate_annual_report`, and the rest.
3. Try: *"Import the synthetic bank statement and summarize its rows."*

## Troubleshooting

- The client and the package must use the same Python environment.
- Run `estonian-annual-report-ai-mcp` in a terminal only to see start-up errors; stdio output must not be mixed with debug prints.
- The data directory must exist and be writable by your user (or by the container user when using Docker).
