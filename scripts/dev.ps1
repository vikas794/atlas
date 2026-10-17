# Development: backend with auto-reload + Vite dev server (hot reload, proxies /api to the
# backend). Open http://127.0.0.1:5174. Ctrl+C stops both.
. "$PSScriptRoot\_common.ps1"

Require-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "node" "Install Node.js 20 or later from https://nodejs.org/"
if (-not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
    Invoke-Step "Install frontend dependencies (npm ci)" { & $Npm --prefix frontend ci }
}

# Fail before starting Vite if the config is invalid or the API port is taken (Vite would
# otherwise proxy /api to whatever else listens there).
Invoke-Step "Check configuration and port" { uv run --locked python -m backend --check }

# Point the Vite proxy at the configured host/port (config.yaml or ATLAS_PORT), not a
# hard-coded 8000, so a changed port never proxies to some other server.
if (-not $env:ATLAS_API_URL) {
    $env:ATLAS_API_URL = (uv run --locked python -c "from src.config.settings import load_settings as l; s = l(); print(f'http://{s.host}:{s.port}')").Trim()
}
Write-Host "Vite proxies /api to $env:ATLAS_API_URL"

# Backend output goes to logs\backend-dev.log (stdout) and logs\backend-dev.err.log (stderr,
# where the Python logging lands); both are overwritten on every start.
$LogDir = Join-Path $PSScriptRoot "..\logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$LogDir = (Resolve-Path $LogDir).Path
Write-Host "Backend logs: $LogDir\backend-dev.log, $LogDir\backend-dev.err.log"

# Same console, so Ctrl+C reaches the backend too and it shuts down gracefully.
$backend = Start-Process -FilePath "uv" -ArgumentList "run", "--locked", "python", "-m", "backend", "--reload" `
    -NoNewWindow -PassThru `
    -RedirectStandardOutput (Join-Path $LogDir "backend-dev.log") `
    -RedirectStandardError (Join-Path $LogDir "backend-dev.err.log")
try {
    & $Npm --prefix frontend run dev
} finally {
    if (-not $backend.HasExited) {
        # Give the backend a moment to finish its own Ctrl+C shutdown, then make sure.
        if (-not $backend.WaitForExit(10000)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID $backend.Id /T /F | Out-Null
        }
    }
}
