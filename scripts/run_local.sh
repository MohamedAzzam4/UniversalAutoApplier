#!/usr/bin/env bash
# Linux/macOS equivalent of scripts/run_local.ps1.

set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d ".venv" ]; then
    echo "Virtual environment not found. Run ./scripts/setup.sh first." >&2
    exit 1
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo "Starting UniversalAutoApplier on 127.0.0.1 (local-first)"
python -m universal_auto_applier
