"""Fail when credentials or locally configured private identifiers enter public files.

Generic credential patterns are built in. Names, registry codes, IBANs, or paths
that must never be published are kept out of the repository: list them, one
regular expression per line, in `.privacy-patterns` (ignored by Git) or in a file
named by the PRIVACY_PATTERNS_FILE environment variable. Lines starting with
`#` are comments; matching is case-insensitive.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = (
    ".github", "docs", "examples", "mcp", "scripts", "src", "tests",
    "README.md", "pyproject.toml", "Dockerfile", "compose.yaml", ".env.example",
    "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "NOTICE", ".gitignore",
)
SKIP_DIRS = {".git", ".venv", "__pycache__", "local-data", "build", "dist"}

CREDENTIAL_RULES = {
    "live Stripe credential": re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{12,}\b"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "macOS home directory": re.compile(r"/Users/[A-Za-z0-9._-]+/"),
}


def local_rules() -> dict[str, re.Pattern[str]]:
    """Load private patterns from an untracked file so they are never published."""
    configured = os.environ.get("PRIVACY_PATTERNS_FILE")
    path = Path(configured) if configured else ROOT / ".privacy-patterns"
    if not path.is_file():
        return {}
    rules: dict[str, re.Pattern[str]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        pattern = line.strip()
        if pattern and not pattern.startswith("#"):
            # Report only the line number so the identifier never appears in CI logs.
            rules[f"local pattern #{number}"] = re.compile(pattern, re.IGNORECASE)
    return rules


def scanned_files() -> list[Path]:
    files: list[Path] = []
    for item in SCAN_DIRS:
        path = ROOT / item
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if not candidate.is_file() or any(part in SKIP_DIRS for part in candidate.parts):
                    continue
                files.append(candidate)
    return files


def main() -> int:
    rules = {**CREDENTIAL_RULES, **local_rules()}
    findings: list[tuple[str, str]] = []
    for path in scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(ROOT).as_posix()
        for label, pattern in rules.items():
            if pattern.search(text):
                findings.append((relative, label))
    if findings:
        for path, label in sorted(set(findings)):
            print(f"Privacy check failed: {path} matched rule '{label}'.")
        return 1
    local_count = len(rules) - len(CREDENTIAL_RULES)
    print(f"Privacy check passed ({len(CREDENTIAL_RULES)} credential rules, {local_count} local patterns).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
