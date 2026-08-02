#!/usr/bin/env bash
# Runs all three test suites. A bare `pytest` only covers the backend, because
# the root pytest.ini sets `testpaths = tests` — the seeds and dashboard suites
# each need their own rootdir so their `pythonpath = .` resolves to their own
# directory. See docs/superpowers/plans/2026-08-02-monorepo-merge.md Task 4.
#
# This matters more than it looks: `seeds/tests` and `seeds/scripts` collide by
# name with the backend's `tests/` and `scripts/`. Invoking by directory makes
# pytest pick that directory as rootdir, which is what keeps the imports honest.
set -euo pipefail

# Run from the repo root regardless of where the caller is.
cd "$(dirname "$0")/.."

# The venv layout differs by platform: Windows puts the interpreter in
# .venv/Scripts/python.exe, Linux (and the CI runner) in .venv/bin/python.
# Resolve it rather than hardcoding, so the same script works in both places.
if [ -n "${PYTHON:-}" ]; then
  PY="$PYTHON"
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  echo "No interpreter found — expected .venv/Scripts/python.exe or .venv/bin/python." >&2
  echo "Override with PYTHON=/path/to/python" >&2
  exit 1
fi

echo "=== backend ==="
"$PY" -m pytest -q

echo "=== seeds ==="
"$PY" -m pytest seeds/ -q

echo "=== dashboard ==="
"$PY" -m pytest dashboard/ -q

echo "All three suites passed."
