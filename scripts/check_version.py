#!/usr/bin/env python3
"""Fail when a git tag does not match nishizumi_sync.__version__.

    python scripts/check_version.py v2.0.0
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nishizumi_sync import __version__  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    tag = argv[1].lstrip("vV")
    if tag != __version__:
        print(f"Tag '{argv[1]}' does not match __version__ '{__version__}'", file=sys.stderr)
        return 1
    print(f"Version {__version__} matches tag {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
