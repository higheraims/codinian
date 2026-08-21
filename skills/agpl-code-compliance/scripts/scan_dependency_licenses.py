#!/usr/bin/env python3
"""
scan_dependency_licenses.py

Enumerates dependency licenses for a project (Python, Node, or both) and
flags any that are commonly considered incompatible with, or risky to
combine with, AGPLv3-licensed code.

This is a heuristic aid, NOT a legal determination. Anything it flags
(or fails to flag) should still get a human/legal review before release.

Usage:
    python scan_dependency_licenses.py --lang python   # requires pip-licenses
    python scan_dependency_licenses.py --lang node      # requires license-checker (npx)
    python scan_dependency_licenses.py --lang all

Exit codes:
    0 = no flagged licenses found
    1 = one or more flagged/unknown licenses found
    2 = usage / runtime error (missing tool, etc.)
"""
import argparse
import json
import shutil
import subprocess
import sys

# Licenses generally considered INCOMPATIBLE or high-risk to combine with
# AGPLv3 code in a single distributed/network-served work. This list is
# deliberately conservative — flag first, resolve with a human/legal review.
FLAGGED_LICENSES = {
    "GPL-2.0-only",          # GPLv2-only is incompatible with AGPLv3 without a compatible "or later" clause
    "GPL-2.0",
    "Proprietary",
    "Commercial",
    "CC-BY-NC",              # non-commercial clause conflicts with AGPL's commercial-use freedom
    "CC-BY-NC-SA",
    "SSPL-1.0",              # Server Side Public License — not OSI-approved, do not mix
    "BUSL-1.1",              # Business Source License — not OSI-approved
    "Unlicense-with-restriction",
    "UNKNOWN",
    "UNLICENSED",
}

# Licenses that are fine (permissive or AGPL/GPL-3-compatible copyleft).
# Included for reference in output annotations only.
GENERALLY_COMPATIBLE = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "GPL-3.0-only", "GPL-3.0-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later",
    "AGPL-3.0-only", "AGPL-3.0-or-later", "MPL-2.0", "Python-2.0",
}


def scan_python():
    if shutil.which("pip-licenses") is None:
        print("pip-licenses not found. Install with:\n"
              "  pip install pip-licenses --break-system-packages", file=sys.stderr)
        return None
    out = subprocess.run(
        ["pip-licenses", "--format=json"], capture_output=True, text=True
    )
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        return None
    return json.loads(out.stdout)


def scan_node():
    if shutil.which("npx") is None:
        print("npx not found (Node.js required) for --lang node", file=sys.stderr)
        return None
    out = subprocess.run(
        ["npx", "--yes", "license-checker", "--json"], capture_output=True, text=True
    )
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        return None
    data = json.loads(out.stdout)
    # Normalize to a list of {"Name":..., "License":...}
    normalized = []
    for pkg, info in data.items():
        normalized.append({"Name": pkg, "License": info.get("licenses", "UNKNOWN")})
    return normalized


def evaluate(entries, ecosystem):
    flagged = []
    for entry in entries:
        name = entry.get("Name", "?")
        license_str = str(entry.get("License", "UNKNOWN"))
        # license-checker can return combined strings like "MIT OR Apache-2.0"
        pieces = [p.strip() for p in license_str.replace("(", "").replace(")", "").split(" OR ")]
        if any(p in FLAGGED_LICENSES for p in pieces) or license_str.strip() == "":
            flagged.append((ecosystem, name, license_str))
    return flagged


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lang", choices=["python", "node", "all"], default="all")
    args = ap.parse_args()

    all_flagged = []

    if args.lang in ("python", "all"):
        entries = scan_python()
        if entries is not None:
            all_flagged += evaluate(entries, "python")

    if args.lang in ("node", "all"):
        entries = scan_node()
        if entries is not None:
            all_flagged += evaluate(entries, "node")

    if all_flagged:
        print(f"Flagged {len(all_flagged)} dependency license(s) for review:\n")
        for ecosystem, name, lic in all_flagged:
            print(f"  [{ecosystem}] {name}: {lic}")
        print("\nThese require a human/legal review before merging or releasing. "
              "This scan is heuristic and not a legal determination.")
        sys.exit(1)

    print("No flagged dependency licenses found (heuristic scan — not a legal determination).")
    sys.exit(0)


if __name__ == "__main__":
    main()
