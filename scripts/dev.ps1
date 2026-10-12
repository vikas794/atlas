# Development: backend with auto-reload + Vite dev server (hot reload, proxies /api to the
# backend). Open http://127.0.0.1:5173. Ctrl+C stops both.
. "$PSScriptRoot\_common.ps1"

Require-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "node" "Install Node.js 20 or later from https://nodejs.org/"
if (-not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
    Invoke-Step "Install frontend dependencies (npm ci)" { & $Npm --prefix frontend ci }
}

# Fail before starting Vite if the config is invalid or the API port is taken (Vite would
# otherwise proxy /api to whatever else listens there).
Invoke-Step "Check configuration and port" { uv run --locked python -m backend --check }

# Same console, so Ctrl+C reaches the backend too and it shuts down gracefully.
$backend = Start-Process -FilePath "uv" -ArgumentList "run", "--locked", "python", "-m", "backend", "--reload" `
    -NoNewWindow -PassThru
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
