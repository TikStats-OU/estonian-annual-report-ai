# Contributing to FlaiReport

Thanks for helping. FlaiReport handles real company finances, so the bar is simple: every number must be traceable, and nothing private may ever reach the repository.

## Ground rules

- Money is `Decimal`, never `float`. Accounting arithmetic lives in `accounting/` and stays deterministic.
- The accounting engine does not import from the MCP layer, the optional AI provider, or the document parsers.
- MCP handlers call workflow services. They do not implement their own accounting rules.
- Uncertain classifications stay uncertain until a person approves them.
- Use synthetic data only: company names, registry codes, IBANs, dates, and documents in tests and examples must be invented.
- Do not describe the project as compliant with Estonian filing or tax requirements.
- Code, comments, and documentation are written in English.

## Development setup

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the same checks as CI before opening a pull request:

```sh
python -m unittest discover -s tests -v
ruff check src mcp tests examples scripts
python scripts/check_privacy.py
python scripts/check_docs.py
```

`check_privacy.py` always looks for credentials. To also block your own private identifiers (company names, registry codes, IBANs), put one regular expression per line in `.privacy-patterns` at the repository root. That file is ignored by Git and never leaves your machine.

## Pull requests

- Keep each pull request focused, with tests for the behaviour it changes.
- Explain the privacy and security impact, and any accounting or filing assumption you made.
- Do not attach real documents, screenshots of real accounts, credentials, or local paths.
- CI must pass before review.

## Good first issues

- Add a synthetic bank CSV fixture for another bank layout (date and decimal conventions differ) and document it.
- Add a synthetic malformed-document fixture for a parser edge case that is not covered yet.
- Add read-only MCP resources or prompts, after describing their data-access contract in the pull request.
- Improve the plain-language explanations in `docs/guide.md`.

## License of contributions

FlaiReport is licensed under the [Apache License 2.0](LICENSE). Unless you state otherwise, any contribution you submit is licensed under the same terms (Apache-2.0, section 5). Please sign off your commits (`git commit -s`) to confirm you have the right to submit the change.
