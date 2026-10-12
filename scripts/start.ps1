# Single-app mode: API + background worker + built UI on one port (config.yaml host/port,
# default http://127.0.0.1:8000). Nothing is built at runtime: run build.ps1 first.
# Stop with Ctrl+C (graceful: the worker finishes or hands back its jobs).
. "$PSScriptRoot\_common.ps1"

Require-Command "uv" "Install it from https://docs.astral.sh/uv/getting-started/installation/"
if (-not (Test-Path (Join-Path $FrontendRoot "dist\index.html"))) {
    Write-Warning "frontend/dist is missing: the API will run, but run .\scripts\build.ps1 to get the UI."
}
uv run --locked python -m backend
exit $LASTEXITCODE
