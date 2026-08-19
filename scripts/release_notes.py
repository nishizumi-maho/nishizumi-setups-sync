#!/usr/bin/env python3
"""Extract one version's section from CHANGELOG.md.

    python scripts/release_notes.py 3.0.0 > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
HEADING = re.compile(r"^##\s+\[?v?(?P<version>[0-9][^\]\s]*)\]?", re.MULTILINE)


def section_for(version: str, text: str) -> str:
    wanted = version.lstrip("vV")
    matches = list(HEADING.finditer(text))
    for index, match in enumerate(matches):
        if match.group("version") != wanted:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[1]
    if not CHANGELOG.is_file():
        print(f"Release {version}", file=sys.stdout)
        return 0
    body = section_for(version, CHANGELOG.read_text(encoding="utf-8"))
    print(body or f"Release {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
