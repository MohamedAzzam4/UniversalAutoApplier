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

    IMPORTANT: This script fails fast on any native command error. Every
    python/pip/playwright/pytest invocation is followed by an explicit
    $LASTEXITCODE check so that a silent failure cannot print "Bootstrap
    complete" (which happened with the previous version).

.PARAMETER SkipBrowser
    Skip the Playwright Chromium install. Useful in CI containers.

.PARAMETER PythonExecutable
    Override the Python executable used to create the venv. Defaults to
    `python` on PATH. Use this if you have multiple Python installs and want
    to pin one (e.g. py -3.12).
#>

[CmdletBinding()]
param(
    [switch]$SkipBrowser,
    [string]$PythonExecutable = "python"
)

# Fail fast on any native command error AND on PowerShell errors.
$ErrorActionPreference = "Stop"

# Helper: run a native command and check $LASTEXITCODE.
# PowerShell does NOT propagate $LASTEXITCODE from native commands by default,
# so `python -m pip install ...` returning nonzero would NOT stop the script
# unless we check explicitly. This helper closes that gap.
function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [scriptblock]$ScriptBlock,
        [string]$Description = ""
    )
    if ($Description) {
        Write-Host "==> $Description" -ForegroundColor Cyan
    }
    & $ScriptBlock
    if ($LASTEXITCODE -ne 0) {
        $msg = "Command exited with code $LASTEXITCODE"
        if ($Description) { $msg = "$Description failed: $msg" }
        throw $msg
    }
}

Set-Location -Path (Split-Path -Parent $PSScriptRoot)

Write-Host "==> Verifying Python version (requires >=3.11)" -ForegroundColor Cyan
Invoke-NativeCommand {
    & $PythonExecutable -c "import sys; v=sys.version_info; print(f'Python {v.major}.{v.minor}.{v.micro}'); sys.exit(0 if (v.major, v.minor) >= (3,11) else 1)"
} -Description "Python version check"

Write-Host "==> Creating virtual environment (.venv)" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    Invoke-NativeCommand {
        & $PythonExecutable -m venv .venv
    } -Description "python -m venv .venv"
} else {
    Write-Host "    .venv already exists; reusing." -ForegroundColor DarkGray
}

$py = ".\.venv\Scripts\python.exe"

Invoke-NativeCommand { & $py -m pip install --upgrade pip } -Description "Upgrade pip"

Invoke-NativeCommand { & $py -m pip install -e ".[dev]" } -Description "Install project + dev dependencies (pinned)"

if (-not $SkipBrowser) {
    Invoke-NativeCommand { & $py -m playwright install chromium } -Description "Install Playwright Chromium"
}

Write-Host "==> Applying database migrations" -ForegroundColor Cyan
$env:UAA_DATA_DIR = if ($env:UAA_DATA_DIR) { $env:UAA_DATA_DIR } else { ".\.uaa_data" }
New-Item -ItemType Directory -Force -Path $env:UAA_DATA_DIR | Out-Null
Invoke-NativeCommand {
    & $py -c "from universal_auto_applier.persistence.migrations import apply_migrations; from universal_auto_applier.persistence.db import build_engine_url; from pathlib import Path; print('head=', apply_migrations(build_engine_url(Path('$env:UAA_DATA_DIR') / 'uaa.sqlite')))"
} -Description "Apply Alembic migrations to head"

Write-Host "==> Running smoke tests (unit + integration, no Playwright)" -ForegroundColor Cyan
Invoke-NativeCommand {
    & $py -m pytest tests/unit tests/integration -q
} -Description "Smoke tests"

Write-Host ""
Write-Host "Bootstrap complete." -ForegroundColor Green
Write-Host "Next: .\scripts\run_local.ps1" -ForegroundColor Green
