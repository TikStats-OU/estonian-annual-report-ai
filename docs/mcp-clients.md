# Connecting an MCP client

FlaiReport runs as a **local MCP server over stdio**. Any MCP client that can launch a local command can use it. The server reads files on your machine and writes results to your private data directory; it opens no network port.

The three-step version for Claude Code, Codex, and Claude Desktop is at the top of the [README](../README.md#connect-in-3-steps). This page covers every option in detail.

## 1. Install

Pick one. Each gives you the `flaireport` command (also available under its longer name, `estonian-annual-report-ai-mcp`).

```sh
# A. Recommended: no install step, the client runs it through uv (https://docs.astral.sh/uv/).
#    uv also fetches Python 3.12+ if needed.
uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport --check

# B. Install the command once, isolated from other Python projects (needs Python 3.12+).
pipx install git+https://github.com/TikStats-OU/estonian-annual-report-ai

# C. From a clone, for development.
python -m pip install -e .
```

`--check` prints the active company, period, and data directory, and exits. Run it once before adding the server to a client: the first download takes 10–60 seconds, and some clients give up on a server that does not start within 10 seconds. `--version` prints the installed version.

## 2. Choose the company

The easiest way is to tell your assistant in chat, for example *"Set up FlaiReport for my company Your Company OÜ, registry code 12345678, year 2025."* It calls the `set_company` tool, and the choice is saved in `company.json` in the data directory for later sessions. `get_company` shows the current choice.

Alternatively, fix the company in the client configuration with environment variables. They take precedence over the saved choice when the server starts:

| Variable | Example | Default |
| --- | --- | --- |
| `ESTONIAN_REPORT_COMPANY_NAME` | `Your Company OÜ` | saved choice, else a synthetic example |
| `ESTONIAN_REPORT_REGISTRY_CODE` | `12345678` | saved choice, else `EXAMPLE` |
| `ESTONIAN_REPORT_YEAR` | `2025` | saved choice, else the previous calendar year |
| `ESTONIAN_REPORT_PERIOD_START` / `_END` | `2025-01-01` / `2025-12-31` | the calendar year |
| `ESTONIAN_REPORT_DATA_DIR` | `~/.local/share/estonian-annual-report-ai/data` | that path |

Each company gets its own workspace inside the data directory. For two companies at once, add two server entries with different `ESTONIAN_REPORT_REGISTRY_CODE` values.

## 3. Register the server

### Claude Code

```sh
claude mcp add flaireport --scope user -- uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport
claude mcp list        # flaireport … ✔ Connected
```

`--scope user` makes the server available in every project; `--scope local` limits it to the current folder. To pin a company, add `-e ESTONIAN_REPORT_REGISTRY_CODE=12345678` (and the other variables) before `--`. If startup is slow, launch Claude Code with a longer timeout: `MCP_TIMEOUT=60000 claude`.

### Codex (CLI, IDE extension, app)

```sh
codex mcp add flaireport -- uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport
codex mcp list
```

This writes the following to `~/.codex/config.toml`, which you can also edit by hand. The timeout line is optional and only matters for the very first start; the `env` table is optional too:

```toml
[mcp_servers.flaireport]
command = "uvx"
args = ["--from", "git+https://github.com/TikStats-OU/estonian-annual-report-ai", "flaireport"]
startup_timeout_sec = 60

[mcp_servers.flaireport.env]
ESTONIAN_REPORT_REGISTRY_CODE = "12345678"
```

### Claude Desktop and other JSON-configured clients

In Claude Desktop open **Settings → Developer → Edit Config**. The file is `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS and `%APPDATA%\Claude\claude_desktop_config.json` on Windows. Add:

```json
{
  "mcpServers": {
    "flaireport": {
      "command": "/full/path/to/uvx",
      "args": ["--from", "git+https://github.com/TikStats-OU/estonian-annual-report-ai", "flaireport"]
    }
  }
}
```

Desktop apps often start without your shell `PATH`, so replace `/full/path/to/uvx` with what `which uvx` prints (Windows: `where uvx`). If the file already has other servers, add the `"flaireport": {…}` block inside the existing `"mcpServers"`. Restart the app after saving.

With option B or C, use `"command": "/full/path/to/flaireport"` and no `args`.

### Docker

The client launches `docker` with `"args": ["run", "-i", "--rm", "--read-only", "--tmpfs", "/tmp", "-v", "/path/to/private-data:/data", "-v", "/path/to/input:/input:ro", "estonian-annual-report-ai:local"]` after `docker compose build`. Keep `-i` and never add `-t`: a terminal would corrupt the JSON stream. File paths you pass to the tools must then be container paths under `/input`.

### Remote-only clients

Clients that only connect to **remote** MCP servers over HTTPS cannot launch this local process. FlaiReport deliberately ships no HTTP transport, because a remote server would need authentication and per-user storage first.

## 4. Check it works

1. Ask the client: *"Which company is FlaiReport set up for?"* It should call `get_company`.
2. Try a synthetic file first, never a real statement, until you are comfortable with the flow.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Server fails to start or times out on first use | Run the `--check` command from step 1 once, then restart the client. |
| `uvx: command not found` | Close and reopen the terminal after installing uv. In desktop apps, use the full path to `uvx`. |
| `Configuration error: …` | Run `--check` with the same environment values; it prints which setting is wrong. |
| A newer version is not picked up | Run the step 1 command with `--refresh` after `uvx`, then restart the client. |
| Permission errors when writing | The data directory must be writable by your user (or by the container user in Docker). |

Run `flaireport` in a terminal only to read start-up errors; stdio output must not be mixed with debug prints.
