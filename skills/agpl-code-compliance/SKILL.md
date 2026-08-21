---
name: agpl-code-compliance
description: Run automated compliance checks on code before it's committed, submitted in a PR, or released — specifically AGPLv3 license-header verification, dependency license scanning for AGPL-incompatible licenses, local code-duplication detection for AI-generated code, and Assisted-by git-trailer verification per Euro-Office's AI Contribution Policy. Use this whenever the user asks to check license compliance, verify AGPL/AGPLv3 obligations, scan for incompatible licenses, check whether AI-generated/agent-written code might duplicate copyrighted or training-data code, prepare a PR for an AGPLv3 project, or "run compliance checks" / "check this code is clean" on a Euro-Office (or similar AGPLv3) contribution. Trigger even if the user doesn't name a specific tool — phrases like "make sure this is AGPL compliant," "did the AI copy something," or "check licenses before I submit" all apply.
---

# AGPLv3 & Agent-Assisted Code Compliance

Automatable routines for verifying that code — especially agent/AI-generated code — is safe to submit to an AGPLv3 project such as Euro-Office. Combines license-header checks, dependency-license scanning, local duplication detection, and AI-disclosure trailer checks into one sweep.

**This skill produces heuristic, automatable signal — not a legal opinion.** Always say so when reporting results, and recommend human/legal review before any release for anything flagged (or for anything with real legal exposure regardless of a clean scan).

## When to use this

- Before opening a PR to an AGPLv3 (or similarly copyleft) project, especially one written or assisted by an AI agent.
- When the user asks to verify AGPL/AGPLv3 compliance, scan dependency licenses, or check for missing license headers.
- When the user is worried an LLM may have reproduced memorized/copyrighted training data verbatim.
- When the user asks to verify `Assisted-by` disclosure trailers are present per an AI contribution policy (e.g., Euro-Office's).

## Workflow

1. **Locate the repo.** Confirm you're operating inside (or pointed at) the actual git repository the user wants checked — don't assume; `git rev-parse --show-toplevel` or ask if ambiguous.
2. **Copy scripts in if needed.** These scripts are read-only under the skill directory. Either run them directly by path, or `cp` the `scripts/` directory into the target repo's working tree (e.g., `.compliance/`) if the user wants them committed for CI use.
3. **Run the full sweep** with the orchestrator (recommended default):
   ```bash
   bash scripts/run_all_checks.sh <base_ref> <target_path>
   ```
   - `base_ref` — branch to diff against for changed-file / commit checks (default `main`).
   - `target_path` — root directory to scan for headers/dependencies/duplication (default `.`).

   This runs, in order: license-header check → dependency license scan → local duplication check → Assisted-by trailer check — and exits non-zero if any blocking check fails, so it's CI-usable.
4. **Or run checks individually** when the user only wants one thing (see Scripts below).
5. **Report results plainly.** For each flagged item: what was flagged, why, and what action fixes it (add a header, swap/review a dependency, refactor a near-duplicate block, add a commit trailer). Don't overstate confidence — these are heuristics.
6. **For duplication concerns specifically**, `detect_duplication.py` also prints "distinctive snippets" (unusual function names, long string literals) worth the user manually pasting into GitHub code search or Sourcegraph — this skill does not query the public internet on its own, by design, to keep it safe to run offline/in CI.
7. **If the user wants this wired into CI**, point them at `run_all_checks.sh` as a single CI step and suggest failing the build on non-zero exit.

## Scripts

All scripts live in `scripts/` and are independently runnable. Each supports `--help`.

| Script | Purpose |
|---|---|
| `check_license_headers.py` | Scans files (or `--diff` changed files) for an AGPLv3/SPDX license header; `--fix` inserts a default template. |
| `scan_dependency_licenses.py` | Enumerates Python (`pip-licenses`) and/or Node (`license-checker`) dependency licenses and flags ones commonly incompatible with AGPLv3 (GPLv2-only, SSPL, BUSL, non-commercial-use licenses, unknown/unlicensed). |
| `detect_duplication.py` | Checks new/changed code for local near-duplicates elsewhere in the repo (via `jscpd` if available, else a difflib fallback), and extracts distinctive snippets for manual public-code-search verification. |
| `check_ai_trailers.py` | Verifies `Assisted-by: AGENT_NAME:MODEL_VERSION` git trailers are present and well-formed on commits ahead of a base ref. |
| `run_all_checks.sh` | Runs all of the above in sequence with a combined pass/fail report; CI-friendly exit code. |

### Dependencies these scripts expect (install as needed)

```bash
pip install pip-licenses --break-system-packages   # for scan_dependency_licenses.py --lang python
npm install -g license-checker                      # for scan_dependency_licenses.py --lang node (or rely on npx)
npm install -g jscpd                                 # optional, stronger duplication detection (or rely on npx)
```

If a tool is missing, the relevant script degrades gracefully (skips that sub-check, or falls back to a pure-Python method) rather than crashing — report to the user what was skipped and why.

## Reference

`references/euro-office-policy.md` has the full text summary of Euro-Office's AI Contribution Policy, general AGPLv3 obligations, and the broader tooling landscape (FOSSA, Black Duck, ScanCode, REUSE, MOSS, Copilot's public-code-match filter) for anything beyond what these scripts automate. Read it when the user wants more context than the pass/fail output gives, or wants to know what a deeper/paid tool would add.

## Boundaries

- This skill does not make legal determinations. Never tell the user their code "is AGPLv3 compliant" as a final legal conclusion — say the automated checks passed/flagged X, and that legal review is still the human's responsibility for release-grade decisions.
- Don't silently auto-fix and commit. `--fix` on the header script only inserts a template into the working tree; the user reviews and commits.
- Don't query external services (public code search, license databases) automatically — surface distinctive snippets for the user to check manually, consistent with keeping this skill safe to run in CI/offline.
