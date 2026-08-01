# Atlas Setup SOP

## Purpose

This SOP is the repeatable Windows PowerShell workflow for setting up and starting Atlas using the blazing-fast `uv` package manager and a fresh virtual environment.

Atlas runs as two development processes:

- FastAPI backend on `http://127.0.0.1:8000`
- Vite frontend on `http://127.0.0.1:5173`

## Prerequisites

- Windows with PowerShell
- Node.js 18+ and `npm.cmd`
- Internet access for package installation
- API keys for:
  - `OPENROUTER_API_KEY`
  - `YOUTUBE_API_KEY`

## 1. Open PowerShell In The Repo

```powershell
cd D:\Projects\atlas
```

## 2. Verify Or Install uv

Check whether `uv` is already installed:

```powershell
uv --version
```

If that fails, install `uv` (it is highly recommended to do this in PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

*Note: After installing, you may need to restart your PowerShell window or manually add it to your PATH if instructed by the installer.*

## 3. Create The Standard Python Environment

Create the repo-standard environment (forcing Python 3.10 if available or using system default):

```powershell
uv venv --python 3.10
```

Activate it:

```powershell
.\.venv\Scripts\activate
```

Validate Python:

```powershell
python --version
```

Expected result:

```text
Python 3.10.x
```

## 4. Install Backend Dependencies

From the repo root with your `.venv` active:

```powershell
uv pip install -r requirements.txt
```

*(This uses `uv`'s fast Rust-based resolver and installer to drastically cut down installation time compared to standard pip).*

Validate the key backend packages:

```powershell
uv pip show fastapi uvicorn llama-index-vector-stores-lancedb
```

## 5. Install Frontend Dependencies

From the repo root:

```powershell
cd frontend
npm.cmd install
cd ..
```

Use `npm.cmd` in PowerShell. This avoids PowerShell execution-policy failures from `npm.ps1`.

## 6. Configure Environment Variables

Create or update `.env` in the repo root:

```dotenv
OPENROUTER_API_KEY=your_openrouter_key
YOUTUBE_API_KEY=your_youtube_key
```

## 7. Start The Backend

From the repo root, with the `.venv` active:

```powershell
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```text
http://127.0.0.1:8000/api/health
```

Expected response:

```json
{"status":"ok","service":"atlas-api"}
```

## 8. Start The Frontend

Open a second PowerShell window, navigate to the frontend directory, and run:

```powershell
cd D:\Projects\atlas\frontend
npm.cmd run dev
```

Frontend URL:

```text
http://127.0.0.1:5173
```

The frontend proxies `/api` requests to `http://127.0.0.1:8000`.

## 9. Verification Checklist

Run these checks after both processes are started:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/api/health
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173
```

Success criteria:

- backend responds on `127.0.0.1:8000`
- frontend responds on `127.0.0.1:5173`
- frontend can load and call backend `/api` routes

## Common Failures And Fixes

### `uv` is not recognized

Ensure you restarted PowerShell after running the install script. If it still fails, check that `~/.cargo/bin` or equivalent was added to your environment `PATH`.

### `npm` fails with execution-policy errors

Use:

```powershell
npm.cmd run dev
```

Do not use plain `npm` from PowerShell on this machine.

### The `.venv` fails to start Python or throws permission errors

Make sure you are activating with `.\.venv\Scripts\activate` or `.\.venv\Scripts\Activate.ps1`. If PowerShell blocks script execution, you might need to temporarily allow it:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Backend fails with missing modules

If you encounter missing modules like `llama_index.vector_stores`, it means the installation failed or the `.venv` wasn't active.
Ensure the environment is active and run:

```powershell
uv pip install -r requirements.txt
```

### Backend starts but health check fails immediately

Read the backend traceback in the current terminal and confirm:

- the `.venv` virtual environment is active
- `uv pip install -r requirements.txt` completed successfully
- `.env` contains valid API keys

## Shutdown And Restart

To stop either process, use `Ctrl+C` in that terminal.

To restart cleanly:

1. Open PowerShell in `D:\Projects\atlas`
2. Run `.\.venv\Scripts\activate`
3. Start backend from repo root
4. Start frontend from `frontend` with `npm.cmd run dev`

## Standard Commands Summary

```powershell
cd D:\Projects\atlas
.\.venv\Scripts\activate
uv pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

In a second shell:

```powershell
cd D:\Projects\atlas\frontend
npm.cmd run dev
```
