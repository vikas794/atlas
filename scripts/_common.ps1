# Shared helpers for the Atlas PowerShell scripts (dot-sourced; not run directly).
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$FrontendRoot = Join-Path $RepoRoot "frontend"
Set-Location $RepoRoot
$env:PYTHONUTF8 = "1"

function Require-Command([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Error "$Name is required. $Hint"
        exit 1
    }
}

# Run a native command and stop the script if it fails (PowerShell 5.1 does not do this).
function Invoke-Step([string]$Label, [scriptblock]$Command) {
    Write-Host "==> $Label" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Label (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# npm.cmd avoids the npm.ps1 shim, which a restrictive execution policy blocks.
$Npm = if (Get-Command "npm.cmd" -ErrorAction SilentlyContinue) { "npm.cmd" } else { "npm" }
$Npx = if (Get-Command "npx.cmd" -ErrorAction SilentlyContinue) { "npx.cmd" } else { "npx" }
