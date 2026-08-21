#!/usr/bin/env python3
"""
check_ai_trailers.py

Verifies that commits in the current branch (relative to a base ref) carry
an `Assisted-by: AGENT_NAME:MODEL_VERSION` git trailer when required, per
Euro-Office's CONTRIBUTING.md AI Contribution Policy.

This script does NOT know which commits actually used AI assistance — it
checks trailer *format* on commits you tell it were AI-assisted, and can
also just report all trailers present so a human can eyeball disclosure
completeness before opening a PR.

Usage:
    # List Assisted-by trailers found on commits ahead of main
    python check_ai_trailers.py --base main

    # Validate a specific range strictly (fail if ANY commit lacks the trailer)
    python check_ai_trailers.py --base main --require-all

Exit codes:
    0 = OK (trailers present / nothing to enforce)
    1 = missing or malformed trailers found with --require-all
    2 = usage / runtime error
"""
import argparse
import re
import subprocess
import sys

TRAILER_RE = re.compile(r"^Assisted-by:\s*([A-Za-z0-9_\-\.]+):([A-Za-z0-9_\-\.]+)\s*$", re.MULTILINE)


def get_commits(base: str):
    out = subprocess.run(
        ["git", "log", f"{base}..HEAD", "--format=%H%x01%B%x02"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        sys.exit(2)
    commits = []
    for chunk in out.stdout.split("\x02"):
        chunk = chunk.strip()
        if not chunk:
            continue
        sha, _, body = chunk.partition("\x01")
        commits.append((sha.strip(), body))
    return commits


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="main", help="Base ref to diff against (default: main)")
    ap.add_argument("--require-all", action="store_true",
                     help="Fail if any commit in range lacks an Assisted-by trailer")
    args = ap.parse_args()

    commits = get_commits(args.base)
    if not commits:
        print(f"No commits found between {args.base} and HEAD.")
        sys.exit(0)

    missing = []
    print(f"Checking {len(commits)} commit(s) against {args.base}..HEAD\n")
    for sha, body in commits:
        m = TRAILER_RE.search(body)
        short = sha[:8]
        subject = body.strip().splitlines()[0] if body.strip() else "(empty)"
        if m:
            agent, model = m.groups()
            print(f"  {short}  OK  Assisted-by: {agent}:{model}  — {subject}")
        else:
            print(f"  {short}  --  no Assisted-by trailer  — {subject}")
            missing.append((short, subject))

    if args.require_all and missing:
        print(f"\n{len(missing)} commit(s) missing the Assisted-by trailer.")
        print("If these commits used AI assistance, add a trailer like:")
        print("  Assisted-by: Claude:claude-sonnet-5")
        print("(via `git commit --amend` or interactive rebase). If they were NOT")
        print("AI-assisted, this is expected — only re-run with --require-all if")
        print("every commit in range is known to be AI-assisted.")
        sys.exit(1)

    print("\nDone. Cross-check against the PR description's AI-tool disclosure "
          "before submitting — this script only checks commit trailer format.")
    sys.exit(0)


if __name__ == "__main__":
    main()
