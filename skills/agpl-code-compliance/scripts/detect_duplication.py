#!/usr/bin/env python3
"""
detect_duplication.py

Heuristic check for whether newly-added/AI-generated code closely matches
code already present elsewhere in the repository (local self-duplication),
and prepares distinctive snippets for manual public-code-search lookup.

This does NOT query the internet itself (keeps it safe/offline-friendly).
It flags:
  1. Local near-duplicates: new code that closely matches existing code
     elsewhere in the repo (possible copy-paste that should be refactored,
     or a sign the "new" code isn't actually novel).
  2. Distinctive snippets: unusually specific function signatures / long
     literal strings / uncommon identifier combinations worth manually
     checking against GitHub code search or Sourcegraph for verbatim
     matches to code you may not have rights to use.

Usage:
    python detect_duplication.py --diff                 # check files changed vs HEAD
    python detect_duplication.py --file path/to/new.py  # check a specific file
    python detect_duplication.py --path <dir>            # check all files under a dir against each other

Exit codes:
    0 = no near-duplicates above threshold found
    1 = near-duplicates found (review required)
    2 = usage / runtime error

Optional (better results, if available on PATH): jscpd
    npx --yes jscpd <path> --min-lines 5 --min-tokens 50 --reporters json
This script will use jscpd automatically if found; otherwise falls back
to a pure-Python difflib comparison (weaker, but zero-dependency).
"""
import argparse
import ast
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

CODE_EXTENSIONS = {".c", ".cc", ".cpp", ".h", ".hpp", ".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".go", ".rs"}
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "third_party", "__pycache__"}

SIMILARITY_THRESHOLD = 0.85   # ratio above which two blocks are flagged as near-duplicate
MIN_BLOCK_LINES = 6           # ignore trivially short blocks


def get_git_changed_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", "HEAD"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    return [Path(f) for f in out if Path(f).suffix in CODE_EXTENSIONS and Path(f).exists()]


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in CODE_EXTENSIONS:
            yield path


def try_jscpd(paths: list[Path]) -> bool | None:
    """Return True if jscpd found clones, False if clean, None if unavailable."""
    if shutil.which("npx") is None:
        return None
    try:
        result = subprocess.run(
            ["npx", "--yes", "jscpd", *[str(p) for p in paths], "--reporters", "json",
             "--silent", "--min-lines", "5", "--min-tokens", "50"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    # jscpd writes report to ./jscpd-report/jscpd-report.json by default with json reporter
    report_path = Path("jscpd-report/jscpd-report.json")
    if not report_path.exists():
        return None
    data = json.loads(report_path.read_text())
    clones = data.get("duplicates", [])
    if clones:
        print(f"jscpd found {len(clones)} clone pair(s):\n")
        for c in clones[:20]:
            fa = c["firstFile"]["name"]
            fb = c["secondFile"]["name"]
            print(f"  {fa} <-> {fb}  ({c['lines']} lines, {c['tokens']} tokens)")
        return True
    return False


def block_windows(lines: list[str], size: int):
    for i in range(0, max(1, len(lines) - size + 1)):
        yield i, "\n".join(lines[i:i + size])


def fallback_local_duplication(changed: list[Path], corpus: list[Path]):
    """Pure-python fallback: compare sliding windows of changed files against
    sliding windows of the rest of the codebase using difflib ratio."""
    findings = []
    corpus_blocks = []
    for f in corpus:
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for start, block in block_windows(lines, MIN_BLOCK_LINES):
            if block.strip():
                corpus_blocks.append((f, start, block))

    for f in changed:
        try:
            lines = f.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for start, block in block_windows(lines, MIN_BLOCK_LINES):
            if not block.strip():
                continue
            for cf, cstart, cblock in corpus_blocks:
                if cf == f and abs(cstart - start) < MIN_BLOCK_LINES:
                    continue  # skip trivial self-overlap
                ratio = difflib.SequenceMatcher(None, block, cblock).ratio()
                if ratio >= SIMILARITY_THRESHOLD:
                    findings.append((f, start, cf, cstart, ratio))
    return findings


def extract_distinctive_snippets(files: list[Path], max_snippets=15):
    """Pull out longer literal strings and unusual function signatures —
    good candidates to manually check via GitHub code search / Sourcegraph
    for verbatim matches to code the project may not have rights to use."""
    snippets = []
    string_re = re.compile(r'["\']([^"\']{25,})["\']')
    func_re = re.compile(r'\b(def|function|func|fn)\s+([a-zA-Z_][a-zA-Z0-9_]{6,})\s*\(')

    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in string_re.finditer(text):
            snippets.append((f, "string literal", m.group(1)[:80]))
        for m in func_re.finditer(text):
            snippets.append((f, "function name", m.group(2)))
        if len(snippets) >= max_snippets:
            break
    return snippets[:max_snippets]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diff", action="store_true", help="Check files changed vs HEAD")
    ap.add_argument("--file", type=str, help="Check a single file")
    ap.add_argument("--path", type=str, default=".", help="Root of codebase to treat as comparison corpus")
    args = ap.parse_args()

    root = Path(args.path)

    if args.file:
        changed = [Path(args.file)]
    elif args.diff:
        changed = get_git_changed_files()
    else:
        changed = list(iter_files(root))

    if not changed:
        print("No files to check.")
        sys.exit(0)

    print(f"Checking {len(changed)} file(s) for local near-duplication...\n")

    jscpd_result = try_jscpd(changed if not args.diff else changed + [root])
    used_jscpd = jscpd_result is not None

    if used_jscpd:
        found = jscpd_result
    else:
        corpus = list(iter_files(root))
        findings = fallback_local_duplication(changed, corpus)
        found = bool(findings)
        if findings:
            print(f"difflib fallback found {len(findings)} near-duplicate block(s) (threshold={SIMILARITY_THRESHOLD}):\n")
            for f, start, cf, cstart, ratio in findings[:20]:
                print(f"  {f}:{start} ~ {cf}:{cstart}  (similarity={ratio:.2f})")
        else:
            print("difflib fallback: no near-duplicate blocks found above threshold.")
            print("(Note: install jscpd — `npx jscpd` — for a stronger token-based clone check.)")

    print("\n--- Distinctive snippets worth a manual public-code-search check ---")
    print("(Paste these into GitHub code search or Sourcegraph to check for verbatim")
    print(" matches to code you may not have rights to reuse.)\n")
    for f, kind, snippet in extract_distinctive_snippets(changed):
        print(f"  [{kind}] {f}: {snippet}")

    if found:
        print("\nLocal near-duplicates detected — review before merging.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
