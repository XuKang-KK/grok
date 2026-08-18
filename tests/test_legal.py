"""Terms of Service and Privacy Policy pages."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parent.parent
I18N_FILE = PROJECT_ROOT / "app" / "static" / "i18n.json"


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
        "KK_SECRETS_KEY",
        "KK_ALLOW_SIGNUP",
        "KK_REQUIRE_ACCOUNT",
        "KK_SESSION_SECRET",
        "KK_OPERATOR_EMAIL",
        "KK_BANNED_VIDS",
        "KK_LOGIN_RATE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KK_SESSION_SECRET", "test-session-secret")


def test_terms_and_privacy_pages(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    terms = client.get("/terms")
    assert terms.status_code == 200
    privacy = client.get("/privacy")
    assert privacy.status_code == 200
    legal = client.get("/api/legal")
    assert legal.status_code == 200
    body = legal.json()
    assert body.get("ok") is True
    assert body.get("updated") == "2026-08-18"
    assert "KK AI助手" in (body.get("terms") or "")
    assert "CCAPI" in (body.get("privacy") or "")
    assert "operator_email" in body
    assert "kk_user" in (body.get("privacy") or "")
    assert "kk_vid" in (body.get("privacy") or "")
    en = client.get("/api/legal", params={"lang": "en"})
    assert en.status_code == 200
    assert "independent" in (en.json().get("terms") or "").lower() or "Terms" in (en.json().get("terms") or "")


def test_register_requires_accepted_terms(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    missing = client.post(
        "/api/auth/register",
        json={"username": "legaluser", "password": "password1"},
    )
    assert missing.status_code == 400
    denied = client.post(
        "/api/auth/register",
        json={"username": "legaluser", "password": "password1", "accepted_terms": False},
    )
    assert denied.status_code == 400
    ok = client.post(
        "/api/auth/register",
        json={"username": "legaluser", "password": "password1", "accepted_terms": True},
    )
    assert ok.status_code == 201
    assert ok.json().get("user", {}).get("username") == "legaluser"


def test_i18n_has_terms_privacy_keys():
    data = json.loads(I18N_FILE.read_text(encoding="utf-8"))
    for lang in ("zh", "en"):
        assert data[lang]["legal.terms"].strip()
        assert data[lang]["legal.privacy"].strip()
    assert data["zh"]["legal.terms"] == "服务条款"
    assert data["en"]["legal.terms"] == "Terms"
    assert data["zh"]["legal.privacy"] == "隐私政策"
    assert data["en"]["legal.privacy"] == "Privacy"
