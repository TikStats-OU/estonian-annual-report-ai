"""Check repository-local Markdown links without network access."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)|<(?:img|a)\b[^>]*?(?:src|href)=\"([^\"]+)\"")


def main() -> int:
    markdown_files = [ROOT / "README.md", *ROOT.glob("*.md"), *ROOT.glob("docs/**/*.md")]
    missing: list[tuple[str, str]] = []
    for source in sorted(set(markdown_files)):
        text = source.read_text(encoding="utf-8")
        for target in (markdown or html for markdown, html in LINK.findall(text)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            relative_path = target.split("#", 1)[0]
            if relative_path and not (source.parent / relative_path).exists():
                missing.append((source.relative_to(ROOT).as_posix(), target))

    if missing:
        for source, target in missing:
            print(f"Broken local Markdown link: {source} -> {target}")
        return 1

    print(f"Local Markdown links passed ({len(set(markdown_files))} files scanned).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
