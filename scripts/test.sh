#!/usr/bin/env bash
# Linux/macOS equivalent of scripts/test.ps1.
#
# `set -euo pipefail` fails on any command error. Marker expression is built
# without duplication (the PowerShell version previously had a `not live and
# not live` bug; this version mirrors the fix).

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Run ./scripts/setup.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
. .venv/bin/activate

INCLUDE_PLAYWRIGHT="${INCLUDE_PLAYWRIGHT:-0}"
INCLUDE_LIVE="${INCLUDE_LIVE:-0}"
RUN_RUFF="${RUN_RUFF:-0}"
RUN_PYRIGHT="${RUN_PYRIGHT:-0}"
RUN_ALL="${RUN_ALL:-0}"

if [ "$RUN_ALL" = "1" ]; then
    RUN_RUFF=1
    RUN_PYRIGHT=1
fi

if [ "$RUN_RUFF" = "1" ]; then
    echo "==> Ruff lint"
    python -m ruff check src tests
    echo "==> Ruff format check"
    python -m ruff format --check src tests
fi

if [ "$RUN_PYRIGHT" = "1" ]; then
    echo "==> Pyright"
    python -m pyright
fi

# Build marker expression without duplication.
negations=()
if [ "$INCLUDE_PLAYWRIGHT" != "1" ]; then
    negations+=("not playwright")
fi
if [ "$INCLUDE_LIVE" != "1" ]; then
    negations+=("not live")
fi

if [ "${#negations[@]}" -gt 0 ]; then
    # Join with " and "
    markers=$(IFS=" and "; echo "${negations[*]}")
    echo "==> Pytest (markers: $markers)"
    python -m pytest -m "$markers" --maxfail=1 -q
else
    echo "==> Pytest (no markers excluded)"
    python -m pytest --maxfail=1 -q
fi

echo ""
echo "All checks passed."
