"""Single-app mode: serve the built frontend (``frontend/dist``) from the API process.

Installed as the router's *default* handler, so it only runs when no API route matched:

* ``/api`` and ``/api/...`` never reach the SPA. They get the JSON 404 envelope for every
  HTTP method, so an unknown API route never answers with ``index.html``.
* A request for a file that exists inside ``dist`` is served from disk. The path is resolved
  and must stay inside ``dist``: ``/..%2f..%2f.env`` style requests (``%2f`` and ``%5c`` are
  decoded before routing) cannot escape it.
* Any other GET for a path without a file extension gets ``index.html`` (client-side
  routing); a missing asset (``/assets/x.js``) is a plain 404 instead of HTML.
* When the build is missing, a short page says how to build it (HTTP 503), instead of a
  bare 404 that looks like a broken server.

``dist`` is checked on every request, so building the frontend while the server runs works
without a restart.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.types import Receive, Scope, Send

_MISSING_BUILD_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Atlas: frontend not built</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; padding: 0 1rem;">
<h1>The Atlas frontend has not been built yet</h1>
<p>The API is running, but <code>frontend/dist</code> does not exist. Build it once, then reload:</p>
<pre>Windows:        .\\scripts\\build.ps1
macOS / Linux:  ./scripts/build.sh</pre>
<p>For development with hot reload use <code>scripts/dev</code> instead and open the Vite URL.</p>
<p>API health: <a href="/api/health">/api/health</a> &middot; diagnostics:
<a href="/api/diagnostics">/api/diagnostics</a></p>
</body></html>
"""


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class SpaFallback:
    """ASGI app used as ``app.router.default`` (see module docstring)."""

    def __init__(self, dist_dir: Path) -> None:
        self.dist_dir = dist_dir

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Websockets / lifespan never reach a static fallback.
            raise HTTPException(status_code=404)
        request = Request(scope, receive)
        response = self.resolve(request.url.path, request.method)
        await response(scope, receive, send)

    def resolve(self, path: str, method: str) -> Response:
        if is_api_path(path) or method not in ("GET", "HEAD"):
            raise HTTPException(status_code=404, detail="Not Found")

        dist = self.dist_dir.resolve()
        index = dist / "index.html"
        if not index.is_file():
            return HTMLResponse(_MISSING_BUILD_PAGE, status_code=503)

        relative = path.lstrip("/")
        parts = PurePosixPath(relative.replace("\\", "/")).parts
        if any(part in ("..", "") or ":" in part for part in parts):
            raise HTTPException(status_code=404, detail="Not Found")

        if parts:
            candidate = dist.joinpath(*parts).resolve()
            if not candidate.is_relative_to(dist):
                raise HTTPException(status_code=404, detail="Not Found")
            if candidate.is_file():
                return FileResponse(candidate)
            if PurePosixPath(parts[-1]).suffix:
                # A missing asset must not be answered with HTML.
                raise HTTPException(status_code=404, detail="Not Found")

        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def default_dist_dir() -> Path:
    from src.config.settings import repo_root

    return repo_root() / "frontend" / "dist"


__all__ = ["SpaFallback", "default_dist_dir", "is_api_path"]
