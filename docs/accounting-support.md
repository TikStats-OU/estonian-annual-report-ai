# Annual-report accounting support helpers

`accounting.annual_support` contains small deterministic helpers used to prepare review workpapers:

- `recognize_subscription_revenue` allocates a fixed subscription amount across start-inclusive/end-exclusive service dates (the reporting period is also end-exclusive: use 1 January of the next year as its end) and returns recognized and deferred portions. A charge without service dates must remain provisional.
- `depreciation_scenarios` calculates sensitivities for explicitly supplied remaining useful lives. It does not create or post a journal entry.
- `owner_funded_expense_entry` returns a balanced expense/payable journal entry (debit 6000, credit 2200 by default) with the source reference preserved. Use only after the business expense and personal funding are supported.
- `cross_currency_reference_groups` groups, but never removes, same-date normalized transaction rows sharing a source reference across currencies. Reviewers can consolidate the transaction before posting.

These helpers perform no AI requests, no currency-rate lookup, no tax determination, and no filing. The caller must supply source-supported facts and a documented EUR conversion.
# Deterministic accounting issue decisions

The accounting decision engine evaluates a caller-supplied issue against a
caller-configured materiality policy. Numeric thresholds and qualitative
flags are supported; if numeric thresholds are not configured, the engine uses
qualitative flags and conservatively keeps any monetary issue at least in
`REVIEW`. The evaluation returns the next action (investigate,
research, recommend, request approval, or escalate), source references,
confidence when evidenced, and materiality.

The engine does not post journal entries, write off balances, or alter
statements. Judgmental estimates require recorded approval, and approval is
stored separately from ledger posting. Recommendations and approvals are
persisted in the private local evidence registry; changing a recommendation
resets its prior approval. Provide the reporting-currency thresholds and
qualitative flags from the applicable accounting policy. No threshold is
inferred by the engine.

The MCP tools `evaluate_accounting_issue` and
`approve_accounting_recommendation` use a per-company local registry. Synthetic
tests cover non-company-specific decisions.
