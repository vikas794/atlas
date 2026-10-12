# All quality checks: backend lint + tests, frontend lint + typecheck + unit tests.
. "$PSScriptRoot\_common.ps1"

Require-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "node" "Install Node.js 20 or later from https://nodejs.org/"
if (-not (Test-Path (Join-Path $FrontendRoot "node_modules"))) {
    Invoke-Step "Install frontend dependencies (npm ci)" { & $Npm --prefix frontend ci }
}

Invoke-Step "Backend lint (ruff)" { uv run --locked ruff check . }
Invoke-Step "Backend tests (pytest)" { uv run --locked pytest -q }
Invoke-Step "Frontend lint (eslint)" { & $Npm --prefix frontend run lint }
Push-Location $FrontendRoot
try {
    Invoke-Step "Frontend typecheck (tsc -b)" { & $Npx tsc -b }
} finally {
    Pop-Location
}
Invoke-Step "Frontend tests (vitest)" { & $Npm --prefix frontend test }
Write-Host "All checks passed." -ForegroundColor Green
