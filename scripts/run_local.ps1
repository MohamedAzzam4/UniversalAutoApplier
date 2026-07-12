<#
.SYNOPSIS
    Start the local UniversalAutoApplier API and dashboard.

.DESCRIPTION
    Per docs/generalization/TECHNICAL_BASELINE.md -> Commands:

      run_local.ps1 starts the API/dashboard and worker, opens no public
      listener, and prints the dashboard URL.

    This script binds to 127.0.0.1 only. To override host/port, set UAA_HOST
    and UAA_PORT in .env (see .env.example).
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".venv")) {
    Write-Error "Virtual environment not found. Run .\scripts\setup.ps1 first."
    exit 1
}

& .\.venv\Scripts\Activate.ps1

Write-Host "Starting UniversalAutoApplier on 127.0.0.1 (local-first)" -ForegroundColor Cyan
python -m universal_auto_applier
