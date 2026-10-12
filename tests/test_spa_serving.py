"""Single-app mode: static serving of frontend/dist, SPA fallback, traversal safety."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.spa import SpaFallback


@pytest.fixture
def site(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    """The real app with its SPA fallback pointed at a temp ``dist`` next to a secret file."""
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>atlas-spa</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('atlas')", encoding="utf-8")
    (tmp_path / "web" / ".env").write_text("GEMINI_API_KEY=canary-secret", encoding="utf-8")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=canary-secret", encoding="utf-8")

    original = app.router.default
    app.router.default = SpaFallback(dist)
    try:
        yield TestClient(app, raise_server_exceptions=False), dist
    finally:
        app.router.default = original


def test_index_is_served_at_root_and_for_client_routes(site) -> None:
    client, _ = site
    for path in ("/", "/playlists/3", "/playlists/3/review/7", "/settings"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "atlas-spa" in resp.text
        assert resp.headers["content-type"].startswith("text/html")


def test_existing_assets_are_served_with_their_type(site) -> None:
    client, _ = site
    resp = client.get("/assets/app.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_a_missing_asset_is_a_404_envelope_not_html(site) -> None:
    client, _ = site
    resp = client.get("/assets/missing.js")
    assert resp.status_code == 404
    assert set(resp.json()) == {"code", "message", "details"}


@pytest.mark.parametrize(
    "path",
    [
        "/..%2f.env",
        "/..%2f..%2f.env",
        "/%2e%2e%2f.env",
        "/%2e%2e/.env",
        "/assets/..%2f..%2f.env",
        "/..%5c.env",
        "/..%5c..%5c.env",
        "/C:%5cWindows%5cwin.ini",
    ],
)
def test_path_traversal_never_leaves_dist(site, path: str) -> None:
    client, _ = site
    resp = client.get(path)
    assert "canary-secret" not in resp.text
    assert resp.status_code == 404


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE", "PATCH"])
def test_unknown_api_routes_get_the_json_404_for_every_method(site, method: str) -> None:
    client, _ = site
    resp = client.request(method, "/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.json()["code"] == "HTTP_404"


def test_known_api_routes_still_win_over_the_spa(site) -> None:
    client, _ = site
    assert client.get("/api/health").json()["status"] == "ok"


def test_missing_build_shows_a_page_that_says_how_to_build(tmp_path: Path) -> None:
    original = app.router.default
    app.router.default = SpaFallback(tmp_path / "no-dist")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/")
        assert resp.status_code == 503
        assert "scripts/build.sh" in resp.text and "build.ps1" in resp.text
        # The API is unaffected and unknown API routes still answer JSON.
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/nope").json()["code"] == "HTTP_404"
    finally:
        app.router.default = original
