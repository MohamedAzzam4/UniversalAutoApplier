#!/usr/bin/env bash
# Linux/macOS equivalent of scripts/setup.ps1.
# Per TECHNICAL_BASELINE.md: "CI and non-Windows users must also be able to
# run equivalent commands directly".
#
# `set -euo pipefail` makes the script fail on any command error, any unset
# variable, and any pipe failure. This is the bash equivalent of the
# $LASTEXITCODE checks in setup.ps1.

set -euo pipefail
cd "$(dirname "$0")/.."

SKIP_BROWSER="${SKIP_BROWSER:-0}"
PYTHON="${PYTHON:-python3}"

echo "==> Verifying Python version (requires >=3.11)"
"$PYTHON" -c "import sys; v=sys.version_info; print(f'Python {v.major}.{v.minor}.{v.micro}'); sys.exit(0 if (v.major, v.minor) >= (3,11) else 1)"

echo "==> Creating virtual environment (.venv)"
if [ ! -d ".venv" ]; then
    "$PYTHON" -m venv .venv
fi

# shellcheck disable=SC1091
. .venv/bin/activate

echo "==> Upgrading pip"
python -m pip install --upgrade pip

echo "==> Installing project + dev dependencies (pinned)"
python -m pip install -e ".[dev]"

if [ "$SKIP_BROWSER" != "1" ]; then
    echo "==> Installing Playwright Chromium"
    python -m playwright install chromium
fi

echo "==> Applying database migrations"
export UAA_DATA_DIR="${UAA_DATA_DIR:-./.uaa_data}"
mkdir -p "$UAA_DATA_DIR"
# Pass the data dir to Python via UAA_DATA_DIR env var and read it with
# os.environ inside Python. Do NOT interpolate the path into the Python
# source string — backslashes in Windows paths trigger SyntaxWarning.
python -c "import os; from pathlib import Path; from universal_auto_applier.persistence.migrations import apply_migrations; from universal_auto_applier.persistence.db import build_engine_url; data_dir = Path(os.environ['UAA_DATA_DIR']); print('head=', apply_migrations(build_engine_url(data_dir / 'uaa.sqlite')))"

echo "==> Running smoke tests (unit + integration, no Playwright)"
python -m pytest tests/unit tests/integration -q

echo ""
echo "Bootstrap complete."
echo "Next: ./scripts/run_local.sh"
