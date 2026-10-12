# Changelog

## Unreleased

### Added
- Local stdio MCP server with 33 tools for document import, review, reconciliation, evidence tracking, and review-only drafts.
- Stripe report import and three-way reconciliation (Balance Summary, Fees, itemized activity, bank payouts).
- Evidence registry, materiality-based decision engine, and a configurable pre-filing gate.
- Apache-2.0 license and NOTICE.
- Animated README illustrations and a plain-language overview.
- Two-minute setup for Claude Code, Codex, and Claude Desktop via `uvx`, with an animated connection banner.
- `--check` and `--version` options: validate the configuration and pre-download everything before a client starts the server.

### Fixed
- Stripe fee amounts exported with many trailing zeros (`66.280000000000000000`) were ignored, so fee reconciliation always failed.
- Missing-information checks and opening balances were tied to 2022–2025; they now follow the configured reporting period, and older reports are advisory instead of blocking.
- The optional classification helper could propose categories for possible duplicates, own-account transfers, and refunds; those rows now always wait for a person.
- A corrupted classification-rules file was silently replaced on the next approval; it now stops with an error and is preserved.
- Long lines in the PDF draft were cut off at 120 characters; they now wrap.
- Spreadsheet formula escaping now also covers tab and carriage-return prefixes.
- Journal amounts sent to `generate_financial_statements` are validated before parsing.
- `mcp/server.py` now starts from a source checkout without installing the package.
- Four workpaper tests were not run by the CI test command.
- Stripe export instructions are now in English.
- `pip install` of the package now includes the MCP SDK, so the server command works without extras.
- Docker Compose no longer allocates a TTY, which corrupted the MCP stdio stream.
- Without `ESTONIAN_REPORT_YEAR` the server now defaults to the previous (finished) year, and a malformed year gives a clear message.
