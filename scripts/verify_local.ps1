<#
.SYNOPSIS
    Local verification script that mirrors the GitHub Actions Windows + Python 3.14
    workflow. Run this on a Windows machine to produce the same evidence the CI
    would produce, without needing GitHub Actions.

.DESCRIPTION
    This script runs the exact same gate steps as
    .github/workflows/verify-windows-py314.yml. It is intended for reviewers
    who want to verify locally before (or instead of) triggering CI.

    The script:
    1. Verifies LF line endings.
    2. Runs setup.ps1 (creates .venv, installs deps, applies migrations,
       runs smoke tests).
    3. Runs test.ps1 -All -IncludePlaywright (ruff + pyright + pytest).
    4. Runs the four direct commands.
    5. Runs contract tests specifically (ResourceWarning gate).
    6. Proves ResourceWarning is treated as error.
    7. Prints a summary.

.PARAMETER PythonExecutable
    Path to the Python executable to use. Defaults to "python".

.EXAMPLE
    .\scripts\verify_local.ps1 -PythonExecutable "C:\Users\LOQ\AppData\Local\Programs\Python\Python314\python.exe"
#>

[CmdletBinding()]
param(
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

$failures = @()

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    try {
        & $Action
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) {
            throw "exit code $LASTEXITCODE"
        }
        Write-Host "PASS" -ForegroundColor Green
    } catch {
        Write-Host "FAIL: $_" -ForegroundColor Red
        $failures += $Name
    }
}

# Step 1: Verify LF line endings
Invoke-Step "1. Verify LF line endings" {
    $files = @(
        "src/universal_auto_applier/__init__.py",
        "scripts/setup.ps1",
        "tests/conftest.py"
    )
    foreach ($f in $files) {
        $bytes = [System.IO.File]::ReadAllBytes($f)
        for ($i = 0; $i -lt $bytes.Length - 1; $i++) {
            if ($bytes[$i] -eq 13 -and $bytes[$i+1] -eq 10) {
                throw "FAIL: $f has CRLF line endings (expected LF)"
            }
        }
    }
    Write-Host "  All checked files have LF line endings."
}

# Step 2: Clean environment
Invoke-Step "2. Remove stale .venv and .uaa_data" {
    if (Test-Path .venv) { Remove-Item -Recurse -Force .venv }
    if (Test-Path .uaa_data) { Remove-Item -Recurse -Force .uaa_data }
    Write-Host "  Cleaned."
}

# Step 3: setup.ps1
Invoke-Step "3. setup.ps1" {
    & .\scripts\setup.ps1 -PythonExecutable $PythonExecutable
    if ($LASTEXITCODE -ne 0) { throw "setup.ps1 exit $LASTEXITCODE" }
}

# Step 4: test.ps1 -All -IncludePlaywright
Invoke-Step "4. test.ps1 -All -IncludePlaywright" {
    & .\scripts\test.ps1 -All -IncludePlaywright
    if ($LASTEXITCODE -ne 0) { throw "test.ps1 exit $LASTEXITCODE" }
}

$py = ".\.venv\Scripts\python.exe"

# Step 5: direct ruff check
Invoke-Step "5. ruff check" {
    & $py -m ruff check src tests migrations
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
}

# Step 6: direct ruff format --check
Invoke-Step "6. ruff format --check" {
    & $py -m ruff format --check src tests migrations
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
}

# Step 7: direct pyright
Invoke-Step "7. pyright" {
    & $py -m pyright
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
}

# Step 8: direct pytest
Invoke-Step "8. pytest (full suite)" {
    & $py -m pytest
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
}

# Step 9: contract tests specifically
Invoke-Step "9. contract tests (ResourceWarning gate)" {
    & $py -m pytest tests\contract\test_migrations.py -q
    if ($LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
}

# Step 10: Prove ResourceWarning is treated as error
Invoke-Step "10. Prove ResourceWarning is treated as error" {
    @"
import warnings
def test_resourcewarning_is_error():
    warnings.warn('deliberate', ResourceWarning)
"@ | Out-File -FilePath tests\test_rw_temp.py -Encoding utf8

    & $py -m pytest tests\test_rw_temp.py -q 2>&1 | Out-String | Write-Host
    $rwExit = $LASTEXITCODE
    Remove-Item tests\test_rw_temp.py -Force

    if ($rwExit -eq 0) {
        throw "ResourceWarning was NOT treated as error (test passed, should have failed)"
    }
    Write-Host "  ResourceWarning is correctly treated as error (test failed as expected)."
}

# Summary
Write-Host ""
Write-Host "=== SUMMARY ===" -ForegroundColor Cyan
Write-Host "Python: $(& $PythonExecutable --version)"
Write-Host "Commit: $(git rev-parse HEAD)"
Write-Host "Branch: $(git branch --show-current)"
if ($failures.Count -eq 0) {
    Write-Host "RESULT: ALL GATES PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "RESULT: $($failures.Count) GATE(S) FAILED:" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}
