[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$frontendRoot = Join-Path $repoRoot "frontend"
$logsRoot = Join-Path $repoRoot "logs"
$backendLog = Join-Path $logsRoot "backend-dev.log"
$backendErrorLog = Join-Path $logsRoot "backend-dev.err.log"
$frontendLog = Join-Path $logsRoot "frontend-dev.log"
$frontendErrorLog = Join-Path $logsRoot "frontend-dev.err.log"

function Test-Endpoint {
    param([string]$Url)

    try {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Wait-ForEndpoint {
    param([string]$Url, [int]$TimeoutSeconds = 30)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Endpoint $Url) {
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

Set-Location $repoRoot
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Path $logsRoot -Force | Out-Null

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/getting-started/installation/"
}

$createdVenv = -not (Test-Path $python)
if ($createdVenv) {
    uv venv --python 3.10
}

if ($Install -or $createdVenv) {
    uv pip install -r requirements.txt
}

if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    throw "Node.js and npm are required. Install Node.js 18 or later."
}

if ($Install -or -not (Test-Path (Join-Path $frontendRoot "node_modules"))) {
    Push-Location $frontendRoot
    try {
        npm.cmd ci
    } finally {
        Pop-Location
    }
}

$backendUrl = "http://127.0.0.1:8000/api/health"
$frontendUrl = "http://127.0.0.1:5173"

if (-not (Test-Endpoint $backendUrl)) {
    Start-Process -FilePath $python `
        -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload" `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $backendLog `
        -RedirectStandardError $backendErrorLog `
        -WindowStyle Hidden
}

if (-not (Test-Endpoint $frontendUrl)) {
    Start-Process -FilePath "npm.cmd" `
        -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173" `
        -WorkingDirectory $frontendRoot `
        -RedirectStandardOutput $frontendLog `
        -RedirectStandardError $frontendErrorLog `
        -WindowStyle Hidden
}

if (-not (Wait-ForEndpoint $backendUrl)) {
    throw "The backend did not become ready. Start it manually with: $python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000"
}

if (-not (Wait-ForEndpoint $frontendUrl)) {
    throw "The frontend did not become ready. Start it manually with: npm.cmd run dev"
}

Write-Host "Atlas is ready"
Write-Host "  Frontend: http://127.0.0.1:5173"
Write-Host "  API:      http://127.0.0.1:8000/api/health"
Write-Host "  Logs:     $logsRoot"

if ($OpenBrowser) {
    Start-Process "http://127.0.0.1:5173"
}
