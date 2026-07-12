<#
.SYNOPSIS
    Bootstrap the UniversalAutoApplier local development environment.

.DESCRIPTION
    Per docs/generalization/TECHNICAL_BASELINE.md -> Commands:

      setup.ps1 creates .venv, installs the project and development
      dependencies, installs Chromium through Playwright, applies migrations,
      and prints the next run command. It must be safe to rerun.

    This script is idempotent: re-running it refreshes dependencies and
    re-applies migrations to head.

.PARAMETER SkipBrowser
    Skip the Playwright Chromium install. Useful in CI containers.
#>

[CmdletBinding()]
param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

Write-Host "==> Creating virtual environment (.venv)" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1

Write-Host "==> Upgrading pip" -ForegroundColor Cyan
python -m pip install --upgrade pip

Write-Host "==> Installing project + dev dependencies" -ForegroundColor Cyan
python -m pip install -e ".[dev]"

if (-not $SkipBrowser) {
    Write-Host "==> Installing Playwright Chromium" -ForegroundColor Cyan
    python -m playwright install chromium
}

Write-Host "==> Applying database migrations" -ForegroundColor Cyan
$env:UAA_DATA_DIR = if ($env:UAA_DATA_DIR) { $env:UAA_DATA_DIR } else { ".\.uaa_data" }
New-Item -ItemType Directory -Force -Path $env:UAA_DATA_DIR | Out-Null
python -c "from universal_auto_applier.persistence.migrations import apply_migrations; from universal_auto_applier.persistence.db import build_engine_url; from pathlib import Path; print('head=', apply_migrations(build_engine_url(Path('$env:UAA_DATA_DIR') / 'uaa.sqlite')))"

Write-Host "==> Running smoke tests" -ForegroundColor Cyan
python -m pytest tests/unit tests/integration -q

Write-Host ""
Write-Host "Bootstrap complete." -ForegroundColor Green
Write-Host "Next: .\scripts\run_local.ps1" -ForegroundColor Green
