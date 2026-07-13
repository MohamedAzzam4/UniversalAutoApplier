<#
.SYNOPSIS
    Start the local UniversalAutoApplier API and dashboard.

.DESCRIPTION
    Per docs/generalization/TECHNICAL_BASELINE.md -> Commands:

      run_local.ps1 starts the API/dashboard and worker, opens no public
      listener, and prints the dashboard URL.

    This script binds to 127.0.0.1 only. To override host/port, set UAA_HOST
    and UAA_PORT in .env (see .env.example).

    The script fails fast if the venv is missing or `python -m
    universal_auto_applier` exits nonzero.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

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

Write-Host "Starting UniversalAutoApplier on 127.0.0.1 (local-first)" -ForegroundColor Cyan
# NOTE: this is a long-running foreground process. We do NOT wrap it in
# Invoke-NativeCommand because uvicorn holds the terminal until Ctrl+C, at
# which point $LASTEXITCODE is undefined. We just run it directly.
& $py -m universal_auto_applier
if ($LASTEXITCODE -ne 0) {
    throw "universal_auto_applier exited with code $LASTEXITCODE"
}
