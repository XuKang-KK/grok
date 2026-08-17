"""Access token middleware, login cookie, CORS, and readiness."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _isolate(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.settings.ENV_FILE", tmp_path / "no.env")
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("app.sessions.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.sessions.SESSIONS_DIR", sess_dir)
    monkeypatch.setattr("app.sessions.ACTIVE_FILE", tmp_path / "active_session.json")
    for name in (
        "XAI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KK_ACCESS_TOKEN",
        "ACCESS_TOKEN",
        "HOST",
        "PORT",
        "KK_CORS_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_apis_open_without_token(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body.get("ok") is True
    assert body.get("auth_required") is False
    assert "version" in body
    assert "host" in body
    assert "bind" in body
    assert "has_any_provider_key" in body
    assert isinstance(body.get("chromium"), bool)
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/models").status_code == 200
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code in {200, 503}


def test_token_protects_chat_and_settings_then_header_works(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_ACCESS_TOKEN", "secret-token")
    from app.main import app

    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json().get("auth_required") is True
    assert "secret-token" not in health.text

    denied_settings = client.get("/api/settings")
    assert denied_settings.status_code == 401
    denied_chat = client.post("/api/chat", json={"message": "你好"})
    assert denied_chat.status_code == 401

    headers = {"Authorization": "Bearer secret-token"}
    ok_settings = client.get("/api/settings", headers=headers)
    assert ok_settings.status_code == 200
    blob = json.dumps(ok_settings.json())
    assert "secret-token" not in blob
    assert ok_settings.json().get("has_access_token") is True
    assert "access_token" not in ok_settings.json()

    ok_chat = client.post("/api/chat", json={"message": "你好"}, headers=headers)
    assert ok_chat.status_code in {200, 503}


def test_access_token_alias_and_cookie_login(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("ACCESS_TOKEN", "alias-token")
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/settings").status_code == 401
    bad = client.post("/api/login", json={"token": "wrong"})
    assert bad.status_code == 401
    login = client.post("/api/login", json={"token": "alias-token"})
    assert login.status_code == 200
    assert login.json().get("ok") is True
    assert "alias-token" not in login.text
    # TestClient keeps the httpOnly cookie
    assert client.get("/api/settings").status_code == 200


def test_settings_can_set_and_clear_token_without_echo(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = client.put("/api/settings", json={"access_token": "ui-secret"})
    assert res.status_code == 200
    assert "ui-secret" not in res.text
    assert res.json().get("has_access_token") is True
    assert "access_token" not in res.json()
    assert client.get("/api/settings").status_code == 401
    headers = {"Authorization": "Bearer ui-secret"}
    assert client.get("/api/settings", headers=headers).status_code == 200
    cleared = client.put(
        "/api/settings",
        json={"clear_access_token": True},
        headers=headers,
    )
    assert cleared.status_code == 200
    assert cleared.json().get("has_access_token") is False
    assert client.get("/api/settings").status_code == 200


def test_health_and_ready_fields(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8000")
    from app.main import app

    client = TestClient(app)
    health = client.get("/api/health").json()
    assert health["host"] == "127.0.0.1"
    assert health["bind"] == "127.0.0.1:8000"
    assert health["auth_required"] is False
    ready = client.get("/api/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert "ok" in body
    assert isinstance(body.get("issues"), list)
    assert "listening_only_on_localhost" in body["issues"]


def test_ready_lan_without_token(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("HOST", "0.0.0.0")
    from app.main import app

    client = TestClient(app)
    body = client.get("/api/ready").json()
    assert "auth_missing_on_lan" in body["issues"]
    assert body["ok"] is False


def test_cors_extra_origin_and_same_origin(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_CORS_ORIGINS", "http://phone.local:8000")
    from app.main import app

    client = TestClient(app)
    extra = client.get(
        "/api/health",
        headers={"Origin": "http://phone.local:8000"},
    )
    assert extra.status_code == 200
    assert extra.headers.get("access-control-allow-origin") == "http://phone.local:8000"
    assert extra.headers.get("access-control-allow-credentials") == "true"

    same = client.get(
        "/api/health",
        headers={"Origin": "http://testserver", "Host": "testserver"},
    )
    assert same.status_code == 200
    assert same.headers.get("access-control-allow-origin") == "http://testserver"
    assert same.headers.get("access-control-allow-credentials") == "true"

    other = client.get(
        "/api/health",
        headers={"Origin": "http://evil.example"},
    )
    assert other.status_code == 200
    assert other.headers.get("access-control-allow-origin") != "http://evil.example"
