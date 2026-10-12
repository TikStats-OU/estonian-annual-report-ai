# Connecting an MCP client

FlaiReport runs as a **local MCP server over stdio**. Any MCP client that can launch a local command can use it. The server reads files on your machine and writes results to your private data directory; it opens no network port.

The short version for Claude Code, Codex, and Claude Desktop is at the top of the [README](../README.md#connect-in-about-2-minutes). This page covers every option in detail.

## 1. Install

Pick one. All of them give you the `estonian-annual-report-ai-mcp` command.

```sh
# A. Recommended: no install step, the client runs it through uv (https://docs.astral.sh/uv/).
#    uv also fetches Python 3.12+ if needed.
uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai estonian-annual-report-ai-mcp --check

# B. Install the command once, isolated from other Python projects (needs Python 3.12+).
pipx install git+https://github.com/TikStats-OU/estonian-annual-report-ai

# C. From a clone, for development.
python -m pip install -e .
```

`--check` validates the configuration, prints the company, period, and data directory, and exits. Run it once before adding the server to a client: the first download takes 10–60 seconds, and some clients give up on a server that does not start within 10 seconds. `--version` prints the installed version.

## 2. Configure the company

| Variable | Example | Required |
| --- | --- | --- |
| `ESTONIAN_REPORT_COMPANY_NAME` | `Example Company OÜ` | yes |
| `ESTONIAN_REPORT_REGISTRY_CODE` | `12345678` | yes |
| `ESTONIAN_REPORT_YEAR` | `2025` | no, defaults to the previous calendar year |
| `ESTONIAN_REPORT_PERIOD_START` / `_END` | `2025-01-01` / `2025-12-31` | no, defaults to the calendar year |
| `ESTONIAN_REPORT_DATA_DIR` | `~/.local/share/estonian-annual-report-ai/data` | no |

Use one server entry and one data directory per company. Keep real values in your client's private settings; never commit them to a shared project file.

## 3. Register the server

### Claude Code

```sh
claude mcp add flaireport --scope user \
  -e ESTONIAN_REPORT_COMPANY_NAME="Your Company OÜ" \
  -e ESTONIAN_REPORT_REGISTRY_CODE=12345678 \
  -e ESTONIAN_REPORT_YEAR=2025 \
  -- uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai estonian-annual-report-ai-mcp
claude mcp list        # flaireport … ✔ Connected
```

`--scope user` makes the server available in every project. Use `--scope local` to limit it to the current folder. If startup is slow, launch Claude Code with a longer timeout, e.g. `MCP_TIMEOUT=60000 claude`.

### Codex (CLI, IDE extension, app)

```sh
codex mcp add flaireport \
  --env ESTONIAN_REPORT_COMPANY_NAME="Your Company OÜ" \
  --env ESTONIAN_REPORT_REGISTRY_CODE=12345678 \
  --env ESTONIAN_REPORT_YEAR=2025 \
  -- uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai estonian-annual-report-ai-mcp
codex mcp list
```

This writes the following to `~/.codex/config.toml`, which you can also edit by hand. The timeout line is optional and only matters for the very first start:

```toml
[mcp_servers.flaireport]
command = "uvx"
args = ["--from", "git+https://github.com/TikStats-OU/estonian-annual-report-ai", "estonian-annual-report-ai-mcp"]
startup_timeout_sec = 60

[mcp_servers.flaireport.env]
ESTONIAN_REPORT_COMPANY_NAME = "Your Company OÜ"
ESTONIAN_REPORT_REGISTRY_CODE = "12345678"
ESTONIAN_REPORT_YEAR = "2025"
```

### Claude Desktop and other JSON-configured clients

In Claude Desktop open **Settings → Developer → Edit Config**. The file is `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS and `%APPDATA%\Claude\claude_desktop_config.json` on Windows.

```json
{
  "mcpServers": {
    "flaireport": {
      "command": "/full/path/to/uvx",
      "args": ["--from", "git+https://github.com/TikStats-OU/estonian-annual-report-ai", "estonian-annual-report-ai-mcp"],
      "env": {
        "ESTONIAN_REPORT_COMPANY_NAME": "Your Company OÜ",
        "ESTONIAN_REPORT_REGISTRY_CODE": "12345678",
        "ESTONIAN_REPORT_YEAR": "2025"
      }
    }
  }
}
```

Desktop apps often start without your shell `PATH`, so put the full path from `which uvx` (Windows: `where uvx`) in `command`. Restart the app after saving.

With option B or C, use `"command": "/full/path/to/estonian-annual-report-ai-mcp"` and no `args`.

### Docker

The client launches `docker` with `"args": ["run", "-i", "--rm", "--read-only", "--tmpfs", "/tmp", "-e", "ESTONIAN_REPORT_COMPANY_NAME", "-e", "ESTONIAN_REPORT_REGISTRY_CODE", "-e", "ESTONIAN_REPORT_YEAR", "-v", "/path/to/private-data:/data", "-v", "/path/to/input:/input:ro", "estonian-annual-report-ai:local"]` after `docker compose build`. Keep `-i` and never add `-t`: a terminal would corrupt the JSON stream. File paths you pass to the tools must then be container paths under `/input`.

### Remote-only clients

Clients that only connect to **remote** MCP servers over HTTPS cannot launch this local process. FlaiReport deliberately ships no HTTP transport, because a remote server would need authentication and per-user storage first.

## 4. Check it works

1. Start with the synthetic profile and synthetic files, never real statements.
2. Ask the client to list the tools: you should see `import_documents`, `reconcile_accounts`, `generate_annual_report`, and the rest.
3. Try: *"Use FlaiReport to tell me which documents you need for the annual report."*

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Server fails to start or times out on first use | Run the `--check` command from step 1 once, then restart the client. |
| `uvx: command not found` in a desktop app | Use the full path to `uvx` in `command`. |
| `Configuration error: …` | Run `--check` with the same environment values; it prints which setting is wrong. |
| A newer version is not picked up | Run the step 1 command with `--refresh` after `uvx`, then restart the client. |
| Permission errors when writing | The data directory must be writable by your user (or by the container user in Docker). |

Run `estonian-annual-report-ai-mcp` in a terminal only to read start-up errors; stdio output must not be mixed with debug prints.
