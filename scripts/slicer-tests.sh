#!/usr/bin/env bash
# Run SlicerFlow's in-Slicer tests and print the verdict.
#
#   scripts/slicer-tests.sh [pattern]        e.g. scripts/slicer-tests.sh 'test_tools_*.py'
#
# macOS: starts a NEW Slicer instance (open -n) and waits for that one to quit. It never
# touches a Slicer that is already running. Elsewhere it runs $SLICER (default: Slicer).
# The chat never starts by itself in that Slicer: --testing, and SLICERFLOW_NO_AUTOSTART.
# The runner writes build/slicer-tests/{result.json,tests.log}; the exit code here is
# 0 only when result.json says every test passed.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
out="$repo/build/slicer-tests"
pattern="${1:-test_*.py}"
runner="$repo/SlicerFlow/Testing/Python/run_slicer_tests.py"
mkdir -p "$out"
rm -f "$out/result.json"
if [ "$(uname)" = "Darwin" ]; then
  open -n -W -g -a "${SLICER_APP:-/Applications/Slicer.app}" \
    --env SF_TEST_OUT="$out" --env SF_TEST_PATTERN="$pattern" --env SLICERFLOW_NO_AUTOSTART=1 \
    --stdout "$out/slicer-stdout.log" --stderr "$out/slicer-stderr.log" \
    --args --testing --no-splash --additional-module-paths "$repo/SlicerFlow" \
    --python-script "$runner"
else
  SF_TEST_OUT="$out" SF_TEST_PATTERN="$pattern" SLICERFLOW_NO_AUTOSTART=1 "${SLICER:-Slicer}" --testing --no-splash \
    --additional-module-paths "$repo/SlicerFlow" \
    --python-script "$runner" > "$out/slicer-stdout.log" 2>&1 || true
fi
tail -n 30 "$out/tests.log" 2>/dev/null || true
if [ ! -f "$out/result.json" ]; then
  echo "No result.json: read $out/slicer-stdout.log"
  exit 3
fi
cat "$out/result.json"
python3 -c 'import json, sys; sys.exit(0 if json.load(open(sys.argv[1]))["ok"] else 1)' "$out/result.json"
