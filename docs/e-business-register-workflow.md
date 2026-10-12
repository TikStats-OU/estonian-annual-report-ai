# Estonian e-Business Register annual report workflow

This workflow was observed in the official portal at `https://ariregister.rik.ee/` and its linked XBRL reporting environment. It describes the screens and controls verified during preparation of a 2025 draft. It is not a statement that the report is compliant or ready for filing.

## Verified flow

1. The public register allows company search by registry code and exposes filed annual reports. Report preparation redirects to the authentication service.
2. Authentication offers Smart-ID, ID-card, Mobile-ID, EU eID, and a contract-user login. The user must complete authentication manually. The automation must not handle credentials, OTPs, CAPTCHA, or digital signatures.
3. In the authenticated annual-report dashboard, choose **Add new report**, select the company, reporting dates, and annual-report type, then configure consolidation, reporting-form category, accounting standard, taxonomy, precision, and language.
4. The observed setup screen defaults to Estonian GAAP, euro precision, no consolidation, and a 2026 taxonomy. The portal offered to reuse prior-year forms. The correct form category must be established for each company and period; it must not be copied from an unrelated example.
5. The report-creation action creates a server-side report with a `Submission in progress` status. It is an editable draft, separate from review, signature, and submission steps.
6. The observed small-undertaking form set contains a balance sheet, an income statement, and notes for accounting policies, labor expense, and related parties. Forms can be filled in the portal and saved without moving to signature.
7. **Check correspondence to the rules** runs the portal's built-in validation. The draft must be reconciled with the accounting workpapers before figures are entered or treated as complete.
8. The portal also exposes **Loading XBRL**. The verified upload screen accepts `.xbrl` files and lets the user choose which reporting periods to load. It does not advertise CSV or generic XML import on that screen. No file was uploaded.
9. The final report is generated/reviewed, then signed and submitted in separate portal stages. Signing and submission remain user-controlled.

## Automation boundary

The portal flow above was inspected manually in an authenticated desktop browser. This project does not depend on Playwright and has no bridge to an authenticated browser session. A standalone project module must not launch another login flow, persist authentication, or assume a CDP endpoint. Until a supported session bridge is chosen, keep portal automation in the user's authenticated desktop session and use the portal's own form validation. Do not automate signature or submission.

## Pre-filing gate

Before entering a filing portal, configure a jurisdiction-specific profile containing the reporting category, mandatory forms, required fields, disclosures, evidence states, and accounting reconciliation result. The reusable `validate_pre_filing` check returns `READY`, `PARTIALLY READY`, or `BLOCKED`, groups new questions, and stores issue state in the local evidence registry so previously asked questions are not repeated. `READY` requires the caller to identify the authoritative source and assert that the configured profile is complete; an unknown form schema cannot silently pass. The check does not discover jurisdictional requirements by itself. Material accounting issues or missing required values block preparation; non-material gaps produce a partial status. Use this gate before opening or resuming a filing workflow.

## Draft state

Portal drafts are company-specific and must never be documented with identifying values or amounts. The verified environment has separate stages for preparation, review, signature, and submission. Save and validate the user's existing draft; never create a replacement unless explicitly instructed. Portal validation can flag both arithmetic/form issues and warnings that require accounting or legal judgment. A warning must not be concealed by entering unsupported values.
