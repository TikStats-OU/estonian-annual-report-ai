## Summary

<!-- Describe the change. -->

## Verification

- [ ] Unit or integration tests added/updated
- [ ] `python -m unittest discover -s tests -v`
- [ ] `ruff check src mcp tests examples scripts`
- [ ] `python scripts/check_privacy.py`

## Privacy and security

- [ ] Synthetic data only; no real financial documents or personal information
- [ ] No secrets, credentials, or local paths
- [ ] File access and logging behavior reviewed

## Accounting and compatibility

- [ ] Deterministic accounting engine behavior preserved
- [ ] Uncertain classifications remain reviewable
- [ ] Filing/legal claims have authoritative support or are labeled as unverified
