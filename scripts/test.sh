#!/usr/bin/env bash
# Linux/macOS equivalent of scripts/test.ps1.

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
    echo "==> Ruff check"
    python -m ruff check src tests
    python -m ruff format --check src tests
fi

if [ "$RUN_PYRIGHT" = "1" ]; then
    echo "==> Pyright"
    python -m pyright
fi

MARKERS="not live"
if [ "$INCLUDE_PLAYWRIGHT" != "1" ]; then
    MARKERS="$MARKERS and not playwright"
fi
if [ "$INCLUDE_LIVE" != "1" ]; then
    MARKERS="$MARKERS and not live"
fi

echo "==> Pytest (markers: $MARKERS)"
python -m pytest -m "$MARKERS" --maxfail=1 -q
