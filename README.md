<p align="center">
  <a href="#connect-in-3-steps"><img src="docs/assets/connect.svg" alt="Connect FlaiReport to Claude or Codex in about 2 minutes" width="100%"></a>
</p>

<p align="center">
  <a href="https://github.com/TikStats-OU/estonian-annual-report-ai/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/TikStats-OU/estonian-annual-report-ai/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-55c2a3"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-102235">
  <img alt="MCP over stdio" src="https://img.shields.io/badge/MCP-local%20stdio-f5c46b">
</p>

## Connect in 3 steps

No coding needed. Copy, paste, done.

### Step 1. Install uv (30 seconds)

uv is a small free tool that downloads and runs FlaiReport for you. Already have it? Skip to step 2.

**Mac or Linux:** open the Terminal app and paste:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:** open PowerShell and paste:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then close the window and open a new one.

### Step 2. Add FlaiReport to your app (1 minute)

Copy the box for the app you use and paste it into the terminal. The first line downloads FlaiReport (about 10 seconds), the second line connects it.

**Claude Code**

```sh
uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport --check
claude mcp add flaireport --scope user -- uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport
```

**Codex**

```sh
uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport --check
codex mcp add flaireport -- uvx --from git+https://github.com/TikStats-OU/estonian-annual-report-ai flaireport
```

**Claude Desktop:** [copy this snippet into Settings → Developer → Edit Config](docs/mcp-clients.md#claude-desktop-and-other-json-configured-clients), then restart the app.

### Step 3. Say hi (30 seconds)

Open a new chat in Claude or Codex and type, with your own details:

> Set up FlaiReport for my company **Your Company OÜ**, registry code **12345678**, year **2025**. Which documents do you need from me?

That's it. FlaiReport remembers your company, and your files never leave your computer.

<sub>Stuck? See [troubleshooting](docs/mcp-clients.md#troubleshooting). Other apps, Docker, and advanced settings are in [docs/mcp-clients.md](docs/mcp-clients.md).</sub>

---

<p align="center">
  <img src="docs/assets/hero.svg" alt="FlaiReport: bank, Stripe, and invoice files flow into a balanced double-entry ledger" width="100%">
</p>

```text
  _____ _       _ ____                       _
 |  ___| | __ _(_)  _ \ ___ _ __   ___  _ __| |_
 | |_  | |/ _` | | |_) / _ \ '_ \ / _ \| '__| __|
 |  _| | | (_| | |  _ <  __/ |_) | (_) | |  | |_
 |_|   |_|\__,_|_|_| \_\___| .__/ \___/|_|   \__|
                           |_|
   bank.csv ─┐
 stripe.csv ─┼─► parse ─► review ─► ledger ─► reconcile ─► draft for review
invoice.pdf ─┘    trace    approve   Dr = Cr   to the cent   JSON · XLSX · PDF
```

**FlaiReport** helps a small Estonian company and its accountant turn bank statements, Stripe exports, invoices, and last year's report into an annual-report draft where every figure can be traced back to a file, a page, and a row. It runs on your own computer as a set of [MCP](https://modelcontextprotocol.io) tools, so any MCP client can drive it in plain language.

> FlaiReport prepares drafts for human review. It does not give legal or tax advice, and it never signs or files anything. You still review the draft and file it in the e-Business Register yourself.

---

## In plain words

Think of FlaiReport as a very careful bookkeeping assistant that refuses to guess.

- **You give it the exports you already have.** Bank CSV/XLSX/PDF, the three standard Stripe reports, invoices, and the previous annual report.
- **It reads them and keeps the receipts.** Every number remembers its source file, page, row, and the file's fingerprint (SHA-256).
- **It books only what is clear.** A bank fee is obviously a bank fee. A payment from a person with no description is not obvious, so it waits for you to say what it is.
- **It checks that everything adds up.** Opening balance plus every movement must equal the closing balance, to the cent. Stripe payouts must match what arrived in the bank.
- **It tells you what is still missing.** Instead of a nice-looking report with hidden holes, you get a list: *"approve last year's closing equity"*, *"Stripe fee report missing"*, and so on.

| You are… | FlaiReport gives you… |
| --- | --- |
| **A business owner** | A to-do list in plain language, and a draft you can hand to an accountant with all sources attached. |
| **An accountant** | A deterministic double-entry ledger, reconciliations, and an evidence registry that records what was confirmed, by whom, and from which document. |
| **A developer** | A small, tested Python codebase with a clean split between parsing, accounting, and the MCP layer. |

## How it works

<p align="center"><img src="docs/assets/workflow.svg" alt="Five steps: add files, read and trace, you review, reconcile, draft" width="100%"></p>

1. **Add files.** Bank statements, Stripe reports, invoices, earlier annual reports.
2. **Read & trace.** Rows are normalized, duplicates and transfers between your own accounts are flagged, and every value keeps its source reference.
3. **You review.** Anything ambiguous is shown to you with its source. Your answers are saved as exact-match rules, so you are never asked the same thing twice.
4. **Reconcile.** Bank, Stripe, and ledger totals are checked against each other. A mismatch blocks the statements instead of being hidden.
5. **Draft.** JSON, XLSX, and PDF drafts for review, plus the list of anything still missing.

## Double-entry in one picture

<p align="center"><img src="docs/assets/double-entry.svg" alt="Three transactions appear on both sides of the ledger; both totals end at 125 euros" width="100%"></p>

Every transaction is recorded twice: once where the money went (debit) and once where it came from (credit). The two columns must always have the same total. If they don't, something is missing, and FlaiReport says so instead of printing a report.

```text
        Bank (asset)                 Sales (revenue)
   Dr              Cr           Dr              Cr
 ─────────────┬─────────────  ─────────────┬─────────────
  100.00      │  20.00 hosting             │  100.00
              │   5.00 card fee            │
 ─────────────┴─────────────  ─────────────┴─────────────
  balance  75.00                       100.00

  + Hosting 20.00 Dr, Card fees 5.00 Dr (expense accounts)

  Debits 125.00  =  Credits 125.00   ✓ the books balance
```

All money is handled as `Decimal` and stored in cents. There is no floating-point arithmetic anywhere in the accounting engine.

## What a session looks like

<p align="center"><img src="docs/assets/terminal-demo.svg" alt="A synthetic MCP session: import, reconcile, and a draft that is blocked until last year's equity is approved" width="100%"></p>

The draft above is blocked on purpose: last year's closing equity has not been approved yet, so the opening balance would be a guess. FlaiReport reports that instead of filling in a number.

## What it checks

| Check | What it means |
| --- | --- |
| Bank reconciliation | Opening balance + all movements = closing balance, per account and currency. |
| Duplicates | The same movement imported twice is flagged and never booked twice. |
| Own-account transfers | Equal and opposite movements between your accounts are paired, not counted as income and expense. |
| Foreign currency | Non-EUR rows need a dated, sourced EUR rate. Nothing is fetched or guessed. |
| Stripe three-way check | Balance Summary, Fees report, and itemized activity must agree, and payouts must match bank receipts. |
| Prior year | Last year's closing figures must be approved before they open this year. |
| Evidence registry | Records what you confirmed, which exports are unavailable, and which questions were already asked. |
| Pre-filing gate | Returns `READY`, `PARTIALLY READY`, or `BLOCKED` against a filing profile you configure. |

## MCP tools

The server exposes 35 tools over local stdio.

| Area | Tools |
| --- | --- |
| Company | `get_company`, `set_company` |
| Import & review | `import_documents`, `parse_bank_statements`, `normalize_transactions`, `classify_transactions`, `get_unclassified_transactions`, `approve_classification`, `reconcile_accounts` |
| Statements | `generate_financial_statements`, `generate_annual_report`, `validate_annual_report`, `validate_pre_filing`, `get_missing_information` |
| Prior reports | `get_previous_report_candidates`, `approve_previous_report_value`, `get_fixed_asset_history` |
| Evidence & decisions | `get_evidence_registry`, `get_pending_evidence_questions`, `record_user_confirmation`, `mark_evidence_question_asked`, `mark_evidence_unavailable`, `record_evidence_issue`, `resolve_evidence_issue`, `evaluate_accounting_issue`, `approve_accounting_recommendation` |
| Stripe | `detect_stripe_reports`, `get_missing_stripe_reports`, `generate_stripe_download_instructions`, `import_stripe_report`, `reconcile_stripe_balances`, `reconcile_stripe_fees`, `reconcile_stripe_activity`, `match_stripe_bank_transfers`, `get_stripe_reconciliation_status` |

## Your data stays with you

<p align="center"><img src="docs/assets/privacy.svg" alt="Files, the FlaiReport server, and reports stay inside your computer; nothing is uploaded" width="100%"></p>

- Documents are parsed locally. The server opens no network port and uploads nothing.
- Each company gets its own workspace. Rules, evidence, and reports are written with owner-only permissions (`0600`).
- Parser errors never include document content.
- File size, page count, row count, and XLSX archive limits protect against malformed or hostile files.
- The optional classification helper (`pip install -e ".[ai]"`) is off by default. It only runs when you pass both `use_ai` and `confirm_external_processing`, it only sees ambiguous rows, and its answers are proposals you still approve.

Keep real documents out of Git, CI logs, and shared folders. See [SECURITY.md](SECURITY.md).

## Stripe exports you need

All three come from the standard Stripe Dashboard; Sigma is not required.

1. **Balance Summary**: Reporting → Balance Summary → export CSV for the full year.
2. **Fees**: Reporting → Fees → export CSV for the same dates.
3. **Itemized activity**: Balances → All Activity → Export (includes payouts).

Use the same dates, currency, and timezone (UTC is safest) for all three. Payouts are transfers to your bank, not extra revenue, and the Fees report details fees that are already in the Balance Summary, so they are never counted twice. `generate_stripe_download_instructions` repeats these steps for whatever is still missing.

## Architecture

```text
src/estonian_annual_report_ai/
├── documents/      parse PDF / CSV / XLSX / TXT, keep page-row-sheet references
├── transactions/   normalize, classify by rule, match transfers, reconcile, FX
├── accounting/     Decimal double-entry ledger, statements, materiality decisions
├── reporting/      review-only drafts (JSON / XLSX / PDF) and the pre-filing gate
├── application.py  workflow that ties the layers together
└── mcp_server.py   thin MCP adapter: validates input, calls the workflow
```

```mermaid
flowchart LR
  D[Documents] --> P[documents/ parsers]
  P --> T[transactions/ normalize + rules]
  T --> R{You approve<br/>unclear items}
  R --> L[accounting/ Decimal ledger]
  L --> C[reconciliation + validation]
  C --> O[reporting/ draft JSON · XLSX · PDF]
  M[MCP client] <--> S[mcp_server.py] <--> A[application.py]
  A --- P & T & L & O
```

The accounting engine does not import the parsers, the MCP layer, or the optional classification helper. MCP handlers never do arithmetic of their own.

## Known limits

- Scanned PDFs need OCR first; only text-based PDFs are read.
- Bank layouts vary. Unrecognized columns stay empty and are reported, never guessed.
- Invoices are indexed as evidence, not booked automatically: accruals, VAT, payroll, and depreciation entries still need an accountant.
- There is no XBRL export or direct e-Business Register submission. The [portal workflow](docs/e-business-register-workflow.md) is documented for manual entry.
- The reporting category (micro or small) is not decided for you.

## Run with Docker

```sh
cp .env.example .env    # adjust paths and company settings
docker compose build
docker compose run --rm annual-report-mcp
```

The container runs as a non-root user with a read-only root filesystem, no published ports, and input mounted read-only.

## Development

```sh
git clone https://github.com/TikStats-OU/estonian-annual-report-ai.git
cd estonian-annual-report-ai
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -e ".[dev]"
python -m examples.synthetic_demo        # the accounting engine without any MCP client
python -m unittest discover -s tests -v
ruff check src mcp tests examples scripts
python scripts/check_privacy.py
python scripts/check_docs.py
```

All tests use synthetic documents. More in [CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

- [User and developer guide](docs/guide.md)
- [Connecting an MCP client](docs/mcp-clients.md)
- [Accounting helpers and decision engine](docs/accounting-support.md)
- [e-Business Register workflow](docs/e-business-register-workflow.md)
- [Research notes and official sources](docs/research-and-architecture.md)
- [Changelog](CHANGELOG.md) · [Security](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)

## License

Copyright 2026 TikStats OÜ. Licensed under the [Apache License, Version 2.0](LICENSE): you may use, modify, and distribute the code, including commercially, as long as you keep the license and the [NOTICE](NOTICE). The FlaiReport and FlaiData names are not licensed for use as your own product name.
