# One-time setup: Python deps (uv), frontend deps (npm), and .env from the template.
. "$PSScriptRoot\_common.ps1"

Require-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "node" "Install Node.js 20 or later from https://nodejs.org/"

Invoke-Step "Install Python dependencies (uv sync)" { uv sync --locked }
Invoke-Step "Install frontend dependencies (npm ci)" { & $Npm --prefix frontend ci }

$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $RepoRoot ".env.example") $envFile
    Write-Host "Created .env from .env.example: add YOUTUBE_API_KEY and GEMINI_API_KEY." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists (left unchanged)."
}

Write-Host ""
Write-Host "Setup done. The database is created and migrated on first start."
Write-Host "  Build the UI once:   .\scripts\build.ps1"
Write-Host "  Run (one port):      .\scripts\start.ps1"
Write-Host "  Develop (hot reload): .\scripts\dev.ps1"
