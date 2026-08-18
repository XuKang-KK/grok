"""KK_PUBLIC visitor isolation, tool lockdown, and public health surface."""

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
        "KK_VISITOR_COST_CENTS",
        "KK_GLOBAL_COST_CENTS",
        "KK_BANNED_VIDS",
        "KK_UPLOAD_TOTAL_BYTES",
        "KK_UPLOAD_TTL_SEC",
        "KK_SESSION_MAX_MESSAGES",
        "KK_UPLOAD_RATE",
        "KK_LOGIN_RATE",
        "KK_SECRETS_KEY",
        "KK_ALLOW_SIGNUP",
        "KK_REQUIRE_ACCOUNT",
        "KK_SESSION_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    # Existing visitor tests cover anonymous public mode.
    monkeypatch.setenv("KK_REQUIRE_ACCOUNT", "0")
    from app.publicmode import reset_rate_limits
    from app.quota import reset_quota

    reset_rate_limits()
    reset_quota()


def test_is_public_mode_false_by_default(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.settings import is_public_mode
    from app.tools import get_tool_schemas

    assert is_public_mode() is False
    names = {t["function"]["name"] for t in get_tool_schemas()}
    assert "run_command" in names
    assert "web_search" in names


def test_local_settings_still_open_without_token(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/settings").status_code == 200
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json().get("public_mode") in {False, None}


def test_public_tool_schemas_and_execute_blocked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.settings import is_public_mode
    from app.tools import execute_tool, get_tool_schemas

    assert is_public_mode() is True
    names = {t["function"]["name"] for t in get_tool_schemas()}
    assert names == {"web_search", "fetch_url", "generate_image"}
    names_explicit = {t["function"]["name"] for t in get_tool_schemas(public=True)}
    assert names_explicit == {"web_search", "fetch_url", "generate_image"}

    called: list[int] = []

    def boom(*_a, **_k):
        called.append(1)
        return json.dumps({"ok": True})

    monkeypatch.setattr("app.tools.run_command", boom)
    raw, ui = execute_tool("run_command", '{"command":"echo hi"}')
    assert called == []
    data = json.loads(raw)
    assert data.get("ok") is False
    assert "对外模式已禁用" in data.get("error", "")
    assert ui.get("ok") is False


def test_public_visitor_cannot_read_other_session(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    a = TestClient(app)
    b = TestClient(app)
    created = a.post("/api/sessions")
    assert created.status_code == 200
    sid = created.json()["session_id"]
    assert a.get("/api/history", params={"session_id": sid}).status_code == 200
    assert b.get("/api/history", params={"session_id": sid}).status_code == 404
    ids = [row["id"] for row in b.get("/api/sessions").json().get("sessions") or []]
    assert sid not in ids


def test_public_settings_admin_required_for_keys(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    monkeypatch.setenv("KK_ACCESS_TOKEN", "admin-secret")
    from app.main import app

    client = TestClient(app)
    denied = client.put("/api/settings", json={"ccapi_api_key": "sk-should-not-leak"})
    assert denied.status_code == 403
    ok = client.put(
        "/api/settings",
        json={"ccapi_api_key": "sk-should-not-leak"},
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert ok.status_code == 200
    assert "sk-should-not-leak" not in ok.text
    body = ok.json()
    assert "ccapi_api_key" not in body
    assert body.get("is_admin") is True
    lang = client.put("/api/settings", json={"language": "en"})
    assert lang.status_code == 200
    assert lang.json().get("language") == "en"


def test_public_chat_rate_limit(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    monkeypatch.setenv("KK_CHAT_RATE", "3")
    monkeypatch.setenv("KK_CHAT_WINDOW_SEC", "600")
    from app.main import app
    from app.publicmode import reset_rate_limits

    reset_rate_limits()
    client = TestClient(app)
    codes = []
    for _ in range(3):
        res = client.post("/api/chat", json={"message": "你好"})
        codes.append(res.status_code)
        assert res.status_code != 429
    fourth = client.post("/api/chat", json={"message": "你好"})
    assert fourth.status_code == 429
    assert fourth.json().get("detail") == "请求过于频繁，请稍后再试"


def test_public_health_is_slim(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    client = TestClient(app)
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body.get("ok") is True
    assert body.get("public_mode") is True
    assert "version" in body
    assert "auth_required" in body
    assert "has_any_provider_key" in body
    assert "workspace" not in body or body.get("workspace") in (None, "")
    assert "session_id" not in body
    blob = json.dumps(body)
    for secret in ("ccapi_api_key", "access_token", "xai_api_key", "KK_ACCESS_TOKEN"):
        assert secret not in blob
        assert secret not in body


def test_public_put_model_is_session_only(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app
    from app.settings import get_model, save_settings

    save_settings({"provider": "ccapi", "model": "gpt-5.6-terra"})
    before = get_model()
    client = TestClient(app)
    res = client.put("/api/model", json={"family": "grok", "model": "grok-4.5"})
    assert res.status_code == 200
    assert res.json().get("model") == "grok-4.5"
    assert get_model() == before
    hist = client.get("/api/history").json()
    assert hist.get("model") == "grok-4.5"


def test_security_headers_on_health(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    client = TestClient(app)
    res = client.get("/api/health")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert res.headers.get("Referrer-Policy") == "same-origin"
    assert "camera=()" in (res.headers.get("Permissions-Policy") or "")
    assert res.headers.get("Cache-Control") == "no-store"
    csp = res.headers.get("Content-Security-Policy") or ""
    assert "default-src 'self'" in csp
    assert "connect-src 'self'" in csp


def test_public_docs_and_mcp_locked(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    mcp = client.get("/api/mcp")
    assert mcp.status_code == 403
    routines = client.get("/api/routines")
    assert routines.status_code == 403


def test_public_mode_fail_secure_env_locks_settings(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.settings import is_public_mode, load_settings, save_settings

    save_settings({"public_mode": False})
    monkeypatch.setenv("KK_PUBLIC", "1")
    assert is_public_mode() is True
    save_settings({"public_mode": True})
    save_settings({"public_mode": False, "language": "en"})
    assert is_public_mode() is True
    stored = load_settings()
    assert stored.get("public_mode") is True
    assert stored.get("language") == "en"


def test_bearer_digest_rejected_cookie_digest_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_ACCESS_TOKEN", "secret-token")
    from app.auth import token_cookie_digest
    from app.main import app

    digest = token_cookie_digest("secret-token")
    bearer_digest = TestClient(app)
    assert (
        bearer_digest.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {digest}"},
        ).status_code
        == 401
    )
    bearer_raw = TestClient(app)
    assert (
        bearer_raw.get(
            "/api/settings",
            headers={"Authorization": "Bearer secret-token"},
        ).status_code
        == 200
    )
    cookie_client = TestClient(app)
    cookie_client.cookies.set("kk_token", digest)
    assert cookie_client.get("/api/settings").status_code == 200


def test_client_ip_xff_rightmost_hop(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_TRUSTED_PROXY", "1")
    from starlette.requests import Request
    from app.publicmode import client_ip

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"1.1.1.1, 2.2.2.2")],
        "client": ("9.9.9.9", 123),
        "server": ("test", 80),
    }
    assert client_ip(Request(scope)) == "2.2.2.2"


def test_visitor_rate_buckets_are_separate(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    monkeypatch.setenv("KK_CHAT_RATE", "2")
    monkeypatch.setenv("KK_TRUSTED_PROXY", "1")
    from app.main import app
    from app.publicmode import reset_rate_limits

    reset_rate_limits()
    vid_a = "a" * 32
    vid_b = "b" * 32
    a = TestClient(app)
    b = TestClient(app)
    a.cookies.set("kk_vid", vid_a)
    b.cookies.set("kk_vid", vid_b)
    headers_a = {"X-Forwarded-For": "1.1.1.1"}
    headers_b = {"X-Forwarded-For": "3.3.3.3"}
    for _ in range(2):
        assert a.post("/api/chat", json={"message": "你好"}, headers=headers_a).status_code != 429
    # A's visitor budget is full; B has its own visitor + IP bucket.
    assert b.post("/api/chat", json={"message": "你好"}, headers=headers_b).status_code != 429
    assert a.post("/api/chat", json={"message": "你好"}, headers=headers_a).status_code == 429


def test_quota_trip_returns_429(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    monkeypatch.setenv("KK_VISITOR_TOKEN_LIMIT", "10")
    from app.main import app
    from app.quota import add_usage, reset_quota

    reset_quota()
    vid = "ab" * 16
    add_usage(vid, 11)
    client = TestClient(app)
    client.cookies.set("kk_vid", vid)
    res = client.post("/api/chat", json={"message": "你好"})
    assert res.status_code == 429
    assert res.json().get("detail") == "用量已达上限"


def test_banned_vid_forbidden(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.audit import save_bans
    from app.main import app

    vid = "cd" * 16
    save_bans([vid])
    client = TestClient(app)
    client.cookies.set("kk_vid", vid)
    res = client.post("/api/chat", json={"message": "你好"})
    assert res.status_code == 403
    assert res.json().get("detail") == "已封禁"


def test_upload_rejects_py_allows_png(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr("app.tools.WORKSPACE", tmp_path)
    monkeypatch.setenv("KK_PUBLIC", "1")
    from app.main import app

    client = TestClient(app)
    bad = client.post(
        "/api/upload",
        files={"file": ("x.py", b"print(1)\n", "text/x-python")},
    )
    assert bad.status_code == 400
    assert "不支持" in bad.json().get("detail", "")
    ok = client.post(
        "/api/upload",
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert ok.status_code == 200
    assert ok.json().get("ok") is True


def test_sessions_list_uses_meta_sidecar(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.sessions import SESSIONS_DIR, SessionStore

    store = SessionStore()
    first = store.create()
    second = store.create()
    assert (SESSIONS_DIR / f"{first.id}.meta.json").exists()
    assert (SESSIONS_DIR / f"{second.id}.meta.json").exists()
    items = store.list()
    ids = {row["id"] for row in items}
    assert first.id in ids
    assert second.id in ids


def test_session_trim_keeps_system_plus_last_40():
    from app.sessions import Session

    sid = "ef" * 16
    sess = Session(
        {
            "id": sid,
            "messages": [{"role": "system", "content": "sys"}]
            + [{"role": "user", "content": f"m{i}"} for i in range(60)],
            "ui_turns": [{"role": "user", "content": f"t{i}"} for i in range(60)],
        }
    )
    sess.trim(40)
    assert sess.messages[0]["role"] == "system"
    assert len(sess.messages) == 41
    assert [m["content"] for m in sess.messages[1:]] == [f"m{i}" for i in range(20, 60)]
    assert len(sess.ui_turns) == 40
    assert sess.ui_turns[0]["content"] == "t20"


def test_reload_false_when_public(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("KK_PUBLIC", "1")
    import inspect

    from app.main import main
    from app.settings import is_public_mode

    assert is_public_mode() is True
    assert (not is_public_mode()) is False
    src = inspect.getsource(main)
    compact = "".join(src.split())
    assert "reload=notis_public_mode()" in compact
