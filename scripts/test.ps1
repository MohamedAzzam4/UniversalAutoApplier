<#
.SYNOPSIS
    Run the UniversalAutoApplier regression gate.

.DESCRIPTION
    Per docs/generalization/TESTING_STRATEGY.md -> Regression Gate.

    Default behavior: run unit, contract, integration, and pipeline tests,
    excluding live and Playwright tests by default. Use the switches to opt
    into broader runs.

    The script returns a nonzero exit code on failure so it can be used as a
    CI gate. Every python/ruff/pyright/pytest invocation is followed by an
    explicit $LASTEXITCODE check.

.PARAMETER IncludePlaywright
    Also run tests marked `playwright` (launches Chromium).

.PARAMETER IncludeLive
    Also run tests marked `live` (hits real ATS websites). Never run by default.

.PARAMETER Ruff
    Run Ruff check + format --check.

.PARAMETER Pyright
    Run Pyright strict type check.

.PARAMETER All
    Equivalent to -Ruff -Pyright. Does NOT imply -IncludePlaywright or
    -IncludeLive; those must be opted in explicitly.
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

# Helper: run a native command and check $LASTEXITCODE.
# Without this, a failing `ruff check` or `pytest` would NOT stop the script
# and the script would print "All checks passed" incorrectly.
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

if (-not (Test-Path ".venv")) {
    Write-Error "Virtual environment not found. Run .\scripts\setup.ps1 first."
    exit 1
}

$py = ".\.venv\Scripts\python.exe"

if ($All) {
    $Ruff = $true
    $Pyright = $true
}

if ($Ruff) {
    Invoke-NativeCommand { & $py -m ruff check src tests } -Description "Ruff lint"
    Invoke-NativeCommand { & $py -m ruff format --check src tests } -Description "Ruff format check"
}

if ($Pyright) {
    Invoke-NativeCommand { & $py -m pyright } -Description "Pyright type check"
}

# Build the pytest marker expression WITHOUT duplication.
# Previous version had a bug: when IncludePlaywright was set, the expression
# became "not live and not live" because both branches appended "and not live".
# This version builds a clean list of negations and joins them with " and ".
$negations = [System.Collections.Generic.List[string]]::new()
if (-not $IncludePlaywright) { $negations.Add("not playwright") }
if (-not $IncludeLive) { $negations.Add("not live") }

if ($negations.Count -gt 0) {
    $markers = [string]::Join(" and ", $negations)
} else {
    $markers = ""
}

if ($markers) {
    Write-Host "==> Pytest (markers: $markers)" -ForegroundColor Cyan
    Invoke-NativeCommand { & $py -m pytest -m "$markers" --maxfail=1 -q } -Description "Pytest"
} else {
    Write-Host "==> Pytest (no markers excluded)" -ForegroundColor Cyan
    Invoke-NativeCommand { & $py -m pytest --maxfail=1 -q } -Description "Pytest"
}

Write-Host ""
Write-Host "All checks passed." -ForegroundColor Green
