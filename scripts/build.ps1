# Build the frontend into frontend/dist, which `start` serves on the API port.
. "$PSScriptRoot\_common.ps1"

Require-Command "node" "Install Node.js 20 or later from https://nodejs.org/"
if (-not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
    Invoke-Step "Install frontend dependencies (npm ci)" { & $Npm --prefix frontend ci }
}
Invoke-Step "Build frontend (frontend/dist)" { & $Npm --prefix frontend run build }
