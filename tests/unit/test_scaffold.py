"""T0.1: the API boots, health responds, the SPA is served, settings load."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gbya.api.main import create_app
from gbya.config import Settings


def _client(tmp_path: Path, with_spa: bool) -> TestClient:
    dist = tmp_path / "dist"
    if with_spa:
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html><body>GateBench</body></html>")
        (dist / "assets" / "app.js").write_text("console.log('x')")
    return TestClient(create_app(Settings(frontend_dist=dist)))


def test_health_returns_ok(tmp_path: Path) -> None:
    resp = _client(tmp_path, with_spa=False).get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"api": "ok"}


def test_spa_index_and_client_routes(tmp_path: Path) -> None:
    client = _client(tmp_path, with_spa=True)
    assert "GateBench" in client.get("/").text
    assert "GateBench" in client.get("/windows/SDWIN-1").text  # client-side route
    assert client.get("/assets/app.js").status_code == 200


def test_unknown_api_route_uses_error_envelope(tmp_path: Path) -> None:
    resp = _client(tmp_path, with_spa=True).get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_spa_does_not_serve_files_outside_dist(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("secret")
    resp = _client(tmp_path, with_spa=True).get("/../secret.txt")
    assert "secret" not in resp.text


def test_settings_defaults_bind_localhost() -> None:
    s = Settings()
    assert s.api_host == "127.0.0.1"
    assert s.resolve(s.app_db_path).is_absolute()


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GBYA_API_PORT", "9123")
    assert Settings().api_port == 9123
