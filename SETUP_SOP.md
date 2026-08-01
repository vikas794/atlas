# Atlas Setup and Fast Start

Atlas normally starts with one command. The launcher reuses a ready backend and frontend, so later starts are fast.

## Prerequisites

- Windows PowerShell
- Node.js 18+ (`npm.cmd`)
- `uv`
- API keys in the repository `.env` file:

```dotenv
OPENROUTER_API_KEY=your_openrouter_key
YOUTUBE_API_KEY=your_youtube_key
GEMINI_API_KEY=your_gemini_key
```

`GEMINI_API_KEY` is required only for playlist quiz generation.

## First Start

From the repository root, run:

```powershell
.\scripts\start-dev.ps1
```

The launcher automatically creates `.venv` with Python 3.10, installs the backend and frontend dependencies, starts both servers, and waits until they respond.

Open:

- Frontend: `http://127.0.0.1:5173`
- API health: `http://127.0.0.1:8000/api/health`

## Later Starts

Run the same command:

```powershell
.\scripts\start-dev.ps1
```

It skips dependency installation when `.venv` and `frontend/node_modules` already exist, then starts only services that are not already running.

To open the frontend automatically:

```powershell
.\scripts\start-dev.ps1 -OpenBrowser
```

## Refresh Dependencies

After changing either dependency manifest, run:

```powershell
.\scripts\start-dev.ps1 -Install
```

The standard `requirements.txt` covers the FastAPI application. To use the old Gradio interface in `app.py`, install the optional legacy dependency set:

```powershell
uv pip install -r requirements-legacy.txt
```

## Troubleshooting

If PowerShell blocks the launcher, run it for the current session only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\start-dev.ps1
```

If a server is stuck, close its PowerShell process and run the launcher again. The script verifies both URLs before reporting success.
