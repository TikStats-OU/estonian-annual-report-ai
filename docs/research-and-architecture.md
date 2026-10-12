# Estonian Annual Reporting: Initial Research and Architecture

Source review date: 10 October 2026. This is engineering research, not legal or accounting advice. A qualified professional should confirm the requirements for each company and reporting year.

## Key findings

- Under the Accounting Act, an annual report generally consists of annual accounts and a management report. A micro-undertaking applying the Estonian Financial Reporting Standard may submit annual accounts only. For micro and small undertakings applying that standard, minimum annual accounts include a balance sheet, income statement, and notes (Accounting Act §§ 14–15).
- Micro-undertaking thresholds: at least two of assets of €450,000, annual revenue of €900,000, and 10 average employees are not exceeded. A small undertaking that is not micro meets at least two of assets of €7,500,000, revenue of €15,000,000, and 50 employees (Accounting Act § 3(14)–(15)). For an undertaking whose principal activity is selling goods or services, the law allows sales revenue to be used instead of revenue when assessing the category. The application does not determine reporting category.
- The revised thresholds apply to financial years beginning on or after 1 January 2024. The category cannot be changed based on one reporting date alone; the two-consecutive-reporting-date rule must be considered. A micro-undertaking may choose micro or small forms, and eligible micro/small undertakings may choose a full set of forms. The application does not select categories or forms.
- For undertakings following the Estonian Financial Reporting Standard, the balance sheet uses Annex 1 and one of the income statement formats in Annex 2 (Accounting Act § 18). Minimum notes differ by category: § 21(3) sets the expanded minimum for small undertakings, while § 21(4) refers micro-undertakings to a narrower set of disclosures.
- Annual reports are prepared in Estonian and in the official currency, the euro (Accounting Act § 17). Complete statements require a general ledger, asset and liability inventory/valuation, and adjusting and closing entries. Bank statement rows alone do not constitute complete annual accounts.
- The annual report is generally due within six months after the financial year ends (Accounting Act § 14(3)). The applicable due date must be checked for the specific entity and period in the register.
- The e-Business Register portal supports electronic submission and describes importing XBRL/XML files prepared by accounting software, followed by portal review and completion.
- On 24 April 2025, RIK announced a data-bridge API for transferring data and preparing annual reports. The announcement described an initial micro-undertaking scope and pilot integrations. The announcement alone is not an implementation specification; current API documentation, integration authorization requirements, and supported form coverage must be confirmed. Public-register data APIs are separate from annual-report submission APIs.
- RIK’s Andmesild documentation describes an API for accounting-software integrations. Registration/contract, client ID, authorization, and user consent are required. Data is submitted as JSON or XML according to report types, schemas, and classifiers obtained through the API. The technical documentation lists `POST /api/v1/ledger/submissions` and submission-status retrieval. Schemas may change, so an application should retrieve current schemas instead of hardcoding them. The reviewed material did not confirm API support for final signing and filing.
- The official process requires identification, review, approval/signing, and submission by an authorized person. Browser automation is not included. Do not bypass CAPTCHA, identity checks, or electronic signing. The reviewed instructions cover the portal and XBRL/XML import but do not establish that arbitrary browser automation is permitted; treat it as unsupported until RIK confirms otherwise.

## Official sources

1. [Accounting Act, current English version (Riigi Teataja)](https://www.riigiteataja.ee/en/akt/522012025001) — §§ 3, 14–18, 21; category definitions, report content, and forms.
2. [RIK: changes to electronic annual-report filing](https://www.rik.ee/en/agency/news/what-kind-changes-have-been-made-submitting-annual-reports-electronically) — micro category changes from 17 January 2025 and report composition.
3. [RIK Help: category of reporting forms](https://abiinfo.rik.ee/en/filing-annual-reports/category-reporting-forms) — practical categorization after 2025 amendments.
4. [RIK Help: loading an annual report in XBRL format](https://abiinfo.rik.ee/en/filing-annual-reports/5-compiling-report/57-loading-report-xbrl-file-format) — importing XBRL/XML prepared by accounting software.
5. [RIK: data bridge for annual reports](https://abiinfo.rik.ee/en/node/346/) — API announcement, initial micro scope, and pilot integrations.
6. [RIK Help: filing annual reports](https://abiinfo.rik.ee/en/filing-annual-reports) — electronic submission, forms, and portal guidance.
7. [RIK Help: signing in to the e-Business Register](https://abiinfo.rik.ee/en/applications-and-dashboard/entering-e-business-register) — ID-card, Mobile-ID, Smart-ID, and other access limitations.
8. [RIK: open data and register API](https://abiinfo.rik.ee/en/e-business-register-queries/open-data-e-business-register) — public-register API/XML, separate from annual-report filing.
9. [RIK: Andmesild for developers](https://www.rik.ee/et/e-ariregister/andmesilla-teenus/andmesild-arendajale) — integration registration, API access, and technical documentation.
10. [RIK: getting started with Andmesild development](https://rikpublic.atlassian.net/wiki/spaces/zeroavalik/pages/997523497/Kuidas+alustada+arendusega) — developer registration and API schemas.
11. [RIK: sending data from accounting software](https://rikpublic.atlassian.net/wiki/spaces/zeroavalik/pages/519766033/Andmete+edastamine+majandustarkvarast) — data transfer and review process.
12. [RTJ 15 — disclosures in notes](https://www.riigiteataja.ee/aktilisa/1201/2201/9026/RM_m63_lisa15.pdf) — minimum disclosures for micro and small undertakings.

## Architecture

```text
Documents and prior reports
        ↓
Import/extraction → normalized records with source references
        ↓                            ↓
Classification proposals → user approval for ambiguous items
        ↓
Deterministic double-entry ledger (Decimal)
        ↓
Reconciliation and validation → issues and missing information
        ↓
Review-only financial statements and exports
        ↓
Manual RIK portal work or a separately authorized integration
```

The current implementation provides local document parsing, transaction normalization and classification, a deterministic ledger workflow, reconciliation, report drafts, evidence tracking, and MCP tools. Classification proposals do not perform financial arithmetic or convert ambiguous bank movements into revenue or expenses without approval. The application does not implement the official reporting taxonomy, statutory form selection, XBRL/XML generation, browser automation, or RIK submission. Local document storage and any RIK integration require a separate security model and current official technical specifications.

## Remaining product questions

- How should the application determine and record the reporting category and accounting basis for a specific entity and year? It needs revenue, assets, average employees, group data, and relevant exemptions.
- What validation model is required for full account-level opening balances, cash inventory, receivables/payables, fixed assets, loans, taxes, payroll, related parties, and events after the reporting date?
- What are the current Andmesild integration terms, schemas, supported payloads, and note coverage for a product seeking integration access?
- What retention, backup, access-control, approval, and audit-log policies should apply to a production deployment?
- Is the annual report already filed or still due for the specific entity? Verify this in RIK; a calendar-year report is generally due by 30 June of the following year.
- Category determination and audit/review obligations need a separate legal review. The application does not decide them.
