#!/usr/bin/env python3
"""
check_license_headers.py

Scans source files (or a git diff) for the presence of an AGPLv3 (or
project-configured) license header, and flags files that are missing one.

Usage:
    python check_license_headers.py --path <dir>            # scan a directory
    python check_license_headers.py --diff                  # scan staged/changed files in current git repo
    python check_license_headers.py --path <dir> --fix       # insert the header template into files missing one

Exit codes:
    0 = all scanned files have a header (or nothing to check)
    1 = one or more files are missing a header
    2 = usage / runtime error
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

# File extensions this check applies to. Extend as needed for the codebase.
CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".h", ".hpp",
    ".js", ".jsx", ".ts", ".tsx",
    ".py", ".java", ".go", ".rs",
    ".cs", ".php", ".rb",
}

# Directories to always skip.
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "third_party", "__pycache__"}

# A header is considered present if any of these patterns appear in the
# first N lines of the file. Adjust to match the project's actual header text.
HEADER_PATTERNS = [
    re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE", re.IGNORECASE),
    re.compile(r"AGPL", re.IGNORECASE),
    re.compile(r"SPDX-License-Identifier:\s*AGPL-3\.0", re.IGNORECASE),
]

HEADER_SCAN_LINES = 20

DEFAULT_HEADER_TEMPLATE = """\
/*
 * SPDX-License-Identifier: AGPL-3.0-only
 * Copyright (C) {year} Euro-Office contributors
 *
 * This file is part of Euro-Office and is licensed under the
 * GNU Affero General Public License v3.0. See LICENSE for details.
 */
"""


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in CODE_EXTENSIONS:
            yield path


def get_git_changed_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACM", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        staged = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACM", "--cached"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        files = set(out) | set(staged)
        return [Path(f) for f in files if Path(f).suffix in CODE_EXTENSIONS]
    except subprocess.CalledProcessError as e:
        print(f"error: not a git repo or git failed: {e}", file=sys.stderr)
        sys.exit(2)


def has_header(path: Path) -> bool:
    try:
        with path.open("r", errors="ignore") as f:
            lines = []
            for _ in range(HEADER_SCAN_LINES):
                line = f.readline()
                if not line:
                    break
                lines.append(line)
            head = "".join(lines)
    except OSError:
        return True  # unreadable, don't flag
    return any(p.search(head) for p in HEADER_PATTERNS)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--path", type=str, default=".", help="Directory to scan")
    ap.add_argument("--diff", action="store_true", help="Scan only changed/staged git files instead of --path")
    ap.add_argument("--fix", action="store_true", help="Insert the default header template into files missing one")
    ap.add_argument("--year", type=str, default="2026", help="Copyright year for --fix template")
    args = ap.parse_args()

    if args.diff:
        files = get_git_changed_files()
    else:
        files = list(iter_files(Path(args.path)))

    missing = [f for f in files if f.exists() and not has_header(f)]

    print(f"Scanned {len(files)} file(s); {len(missing)} missing a license header.\n")

    if missing:
        for f in missing:
            print(f"  MISSING: {f}")
        if args.fix:
            header = DEFAULT_HEADER_TEMPLATE.format(year=args.year)
            for f in missing:
                original = f.read_text(errors="ignore")
                f.write_text(header + "\n" + original)
            print(f"\nInserted default header template into {len(missing)} file(s). Review before committing.")
        else:
            print("\nRun with --fix to insert the default header template automatically (review the result).")
        sys.exit(1)

    print("All scanned files have a recognized license header.")
    sys.exit(0)


if __name__ == "__main__":
    main()
