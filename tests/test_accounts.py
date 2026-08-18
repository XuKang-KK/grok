"""Visitor accounts, session isolation, and encrypted settings secrets."""

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
        "CCAPI_API_KEY",
        "CCAPI_BASE_URL",
        "KK_ACCESS_TOKEN",
        "ACCESS_TOKEN",
        "HOST",
        "PORT",
        "KK_CORS_ORIGINS",
        "KK_PUBLIC",
        "PUBLIC_MODE",
        "KK_ALLOWED_HOSTS",
        "KK_CHAT_RATE",
        "KK_CHAT_WINDOW_SEC",
        "KK_TRUSTED_PROXY",
        "KK_VISITOR_TOKEN_LIMIT",
        "KK_GLOBAL_TOKEN_LIMIT",
        "KK_SECRETS_KEY",
        "KK_ALLOW_SIGNUP",
        "KK_REQUIRE_ACCOUNT",
        "KK_SESSION_SECRET",
        "KK_BANNED_VIDS",
        "KK_LOGIN_RATE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KK_SESSION_SECRET", "test-session-secret")
    from app.publicmode import reset_rate_limits
    from app.quota import reset_quota
    from app.secrets_crypto import reset_crypto_warnings

    reset_rate_limits()
    reset_quota()
    reset_crypto_warnings()


def _register(client: TestClient, username: str, password: str = "password1"):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "accepted_terms": True},
    )


def test_register_me_then_chat(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = _register(client, "alice")
    assert res.status_code == 201
    body = res.json()
    assert body.get("ok") is True
    assert body.get("user", {}).get("username") == "alice"
    assert "password" not in json.dumps(body)
    stored = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    blob = json.dumps(stored)
    assert "password1" not in blob
    assert "scrypt$" in blob
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json().get("authenticated") is True
    assert me.json().get("user", {}).get("username") == "alice"
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code in {200, 503}


def test_bad_password_login_401(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    assert _register(client, "bob").status_code == 201
    client.post("/api/auth/logout")
    bad = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "wrong-password"},
    )
    assert bad.status_code == 401
    assert "wrong-password" not in bad.text
    assert "password" not in json.dumps(bad.json()).lower() or "密码" in bad.json().get("detail", "")
    ok = client.post(
        "/api/auth/login",
        json={"username": "bob", "password": "password1"},
    )
    assert ok.status_code == 200
    assert ok.json().get("user", {}).get("username") == "bob"


def test_public_require_account_chat_401(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    client = TestClient(app)
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json().get("auth_required_for_chat") is True
    assert me.json().get("authenticated") is False
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code == 401
    assert chat.json().get("detail") == "请先登录"
    upload = client.post(
        "/api/upload",
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert upload.status_code == 401
    sessions = client.get("/api/sessions")
    assert sessions.status_code == 401


def test_disabled_user_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = _register(client, "carol")
    assert res.status_code == 201
    uid = res.json()["user"]["id"]
    data = json.loads((tmp_path / "users.json").read_text(encoding="utf-8"))
    data["users"][uid]["disabled"] = True
    (tmp_path / "users.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    me = client.get("/api/auth/me")
    assert me.json().get("authenticated") is False
    login = client.post(
        "/api/auth/login",
        json={"username": "carol", "password": "password1"},
    )
    assert login.status_code == 401
    assert "禁用" in login.json().get("detail", "")


def test_settings_encrypt_roundtrip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_SECRETS_KEY", "unit-test-passphrase")
    from app.settings import get_api_key, save_settings

    save_settings({"ccapi_api_key": "sk-raw-secret-value"})
    disk = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert "enc:v1:" in disk
    assert "sk-raw-secret-value" not in disk
    assert get_api_key("ccapi") == "sk-raw-secret-value"
    pub_from_file = json.loads(disk)
    assert str(pub_from_file.get("ccapi_api_key") or "").startswith("enc:v1:")


def test_signup_disabled_403(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_ALLOW_SIGNUP", "0")
    from app.main import app

    client = TestClient(app)
    res = _register(client, "dave")
    assert res.status_code == 403
    assert "注册" in res.json().get("detail", "")


def test_two_users_session_isolation(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    a = TestClient(app)
    b = TestClient(app)
    assert _register(a, "erin").status_code == 201
    created = a.post("/api/sessions")
    assert created.status_code == 200
    sid = created.json()["session_id"]
    assert a.get("/api/history", params={"session_id": sid}).status_code == 200
    assert _register(b, "frank").status_code == 201
    assert b.get("/api/history", params={"session_id": sid}).status_code == 404
    ids = [row["id"] for row in b.get("/api/sessions").json().get("sessions") or []]
    assert sid not in ids


def test_local_chat_without_account(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    me = client.get("/api/auth/me")
    assert me.json().get("auth_required_for_chat") is False
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code in {200, 503}


def test_ban_user_id(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.audit import save_bans
    from app.main import app

    client = TestClient(app)
    res = _register(client, "gina")
    uid = res.json()["user"]["id"]
    save_bans([], users=[uid])
    chat = client.post("/api/chat", json={"message": "你好"})
    assert chat.status_code == 403
    assert chat.json().get("detail") == "已封禁"
