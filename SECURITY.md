# Security policy

## Supported versions

Security fixes are currently accepted for the latest code on the default branch. No release has been published yet.

## Reporting a vulnerability

Do not open a public issue containing credentials, private financial records, or an exploit that exposes another user's files. Use GitHub's private vulnerability reporting for this repository if enabled. Otherwise contact the repository owner privately through the contact method published in the repository settings. Include affected version, impact, and a minimal synthetic reproduction.

## Data handling

The supported implementation is local stdio. It can read files available to the local process and is not a multi-user service. Do not expose it over HTTP or the public internet. No authenticated remote deployment is implemented. Keep financial documents, local registries, environment files, and generated reports outside version control and CI artifacts.

Report suspected accidental data exposure promptly. Do not rewrite published Git history or force-push a remediation without repository-owner approval.
