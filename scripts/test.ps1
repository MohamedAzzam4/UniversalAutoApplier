<#
.SYNOPSIS
    Run the UniversalAutoApplier regression gate.

.DESCRIPTION
    Per docs/generalization/TESTING_STRATEGY.md -> Regression Gate.

    Default behavior: run unit, contract, integration, and pipeline tests,
    excluding live and Playwright tests by default. Use the switches to opt
    into broader runs.

    The script returns a nonzero exit code on failure so it can be used as a
    CI gate.
#>

[CmdletBinding()]
param(
    [switch]$IncludePlaywright,
    [switch]$IncludeLive,
    [switch]$Ruff,
    [switch]$Pyright,
    [switch]$All
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
    Write-Error "Virtual environment not found. Run .\scripts\setup.ps1 first."
    exit 1
}

& .\.venv\Scripts\Activate.ps1

if ($Ruff -or $All) {
    Write-Host "==> Ruff check" -ForegroundColor Cyan
    python -m ruff check src tests
    python -m ruff format --check src tests
}

if ($Pyright -or $All) {
    Write-Host "==> Pyright" -ForegroundColor Cyan
    python -m pyright
}

$markers = "not live"
if (-not $IncludePlaywright) { $markers += " and not playwright" }
if (-not $IncludeLive) { $markers += " and not live" }

Write-Host "==> Pytest (markers: $markers)" -ForegroundColor Cyan
python -m pytest -m "$markers" --maxfail=1 -q
