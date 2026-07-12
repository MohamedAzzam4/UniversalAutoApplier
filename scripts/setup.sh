#!/usr/bin/env bash
# Linux/macOS equivalent of scripts/setup.ps1.
# Per TECHNICAL_BASELINE.md: "CI and non-Windows users must also be able to
# run equivalent commands directly".

set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_BROWSER="${SKIP_BROWSER:-0}"

echo "==> Creating virtual environment (.venv)"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo "==> Upgrading pip"
python -m pip install --upgrade pip

echo "==> Installing project + dev dependencies"
python -m pip install -e ".[dev]"

if [ "$SKIP_BROWSER" != "1" ]; then
    echo "==> Installing Playwright Chromium"
    python -m playwright install chromium
fi

echo "==> Applying database migrations"
export UAA_DATA_DIR="${UAA_DATA_DIR:-./.uaa_data}"
mkdir -p "$UAA_DATA_DIR"
python -c "from universal_auto_applier.persistence.migrations import apply_migrations; from universal_auto_applier.persistence.db import build_engine_url; from pathlib import Path; print('head=', apply_migrations(build_engine_url(Path('$UAA_DATA_DIR') / 'uaa.sqlite')))"

echo "==> Running smoke tests"
python -m pytest tests/unit tests/integration -q

echo ""
echo "Bootstrap complete."
echo "Next: ./scripts/run_local.sh"
