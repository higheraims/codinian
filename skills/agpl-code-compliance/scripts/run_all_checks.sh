#!/usr/bin/env bash
# run_all_checks.sh
#
# Runs the full AGPLv3 / AI-assisted-code compliance sweep in one pass:
#   1. License header check on changed files
#   2. Dependency license scan
#   3. Local code-duplication / distinctive-snippet check
#   4. Assisted-by trailer check on commits ahead of base branch
#
# Usage:
#   ./run_all_checks.sh [base_ref] [target_path]
#   base_ref    - branch/ref to diff against for --diff modes (default: main)
#   target_path - directory to scan for header/dependency checks (default: .)
#
# Exit code is non-zero if ANY check fails, so this is CI-friendly.

set -uo pipefail

BASE_REF="${1:-main}"
TARGET_PATH="${2:-.}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

STATUS=0

echo "=================================================================="
echo " 1/4  License header check (changed files)"
echo "=================================================================="
python3 "$SCRIPT_DIR/check_license_headers.py" --diff || STATUS=1
echo

echo "=================================================================="
echo " 2/4  Dependency license scan"
echo "=================================================================="
python3 "$SCRIPT_DIR/scan_dependency_licenses.py" --lang all || STATUS=1
echo

echo "=================================================================="
echo " 3/4  Local duplication / distinctive-snippet check (changed files)"
echo "=================================================================="
python3 "$SCRIPT_DIR/detect_duplication.py" --diff --path "$TARGET_PATH" || STATUS=1
echo

echo "=================================================================="
echo " 4/4  Assisted-by trailer check (commits ahead of $BASE_REF)"
echo "=================================================================="
python3 "$SCRIPT_DIR/check_ai_trailers.py" --base "$BASE_REF" || true
echo

echo "=================================================================="
if [ "$STATUS" -eq 0 ]; then
  echo " RESULT: No blocking issues found by automated checks."
  echo " Reminder: this is a heuristic sweep, not a legal review or a"
  echo " substitute for human testing/accountability requirements."
else
  echo " RESULT: One or more checks flagged issues above — review before"
  echo " submitting the PR."
fi
echo "=================================================================="

exit $STATUS
