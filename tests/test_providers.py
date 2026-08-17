"""Multi-provider catalog, settings secrecy, and missing-key errors."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.providers import PROVIDERS
from app.settings import public_settings, save_settings


PRESET_IDS = {
    "ccapi": [
        "gpt-5.6",
        "gpt-5",
        "claude-sonnet-5",
        "claude-opus-5",
        "grok-4.6",
        "deepseek-v3.2",
        "gemini-2.5-flash",
    ],
    "xai": ["grok-4.6", "grok-4.5"],
    "openai": ["gpt-5.6", "gpt-5", "gpt-5-mini", "gpt-5-chat-latest"],
    "anthropic": [
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-fable-5",
        "claude-haiku-4-5",
    ],
}


def _isolate_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.settings.ENV_FILE", tmp_path / "no.env")
    sess_dir = tmp_path / "sessions"
    sess_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("app.sessions.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.sessions.SESSIONS_DIR", sess_dir)
    monkeypatch.setattr("app.sessions.ACTIVE_FILE", tmp_path / "active_session.json")
    for name in ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CCAPI_API_KEY", "CCAPI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_models_catalog_has_providers_and_preset_ids(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    providers = {row["id"]: row for row in body.get("providers") or []}
    assert set(providers) == {"ccapi", "xai", "openai", "anthropic"}
    assert list(providers)[0] == "ccapi"
    assert providers["ccapi"]["name"] == "中转站"
    assert providers["ccapi"]["compat"] == "openai"
    assert body.get("ccapi_base_url") == "https://api.ccapi.ai/v1"
    assert "https://api.ccapi.ai/v1" in (body.get("ccapi_base_presets") or [])
    for pid, models in PRESET_IDS.items():
        assert providers[pid]["name"] == PROVIDERS[pid]["name"]
        got = providers[pid]["models"]
        for mid in models:
            assert mid in got, (pid, mid, got)
    assert "has_api_key" in body
    assert set(body["has_api_key"]) == {"ccapi", "xai", "openai", "anthropic"}
    blob = json.dumps(body)
    assert "api_key" not in body or body.get("api_key") in (None, "", False)
    for banned in ("xai_api_key", "openai_api_key", "anthropic_api_key", "ccapi_api_key"):
        assert banned not in body
        assert f'"{banned}"' not in blob


def test_settings_put_does_not_echo_secrets(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = client.put(
        "/api/settings",
        json={
            "xai_api_key": "xai-secret-aaa",
            "openai_api_key": "sk-secret-bbb",
            "anthropic_api_key": "sk-ant-secret-ccc",
            "ccapi_api_key": "ccapi-secret-ddd",
            "ccapi_base_url": "https://api.ccapi.ai/v1",
            "provider": "openai",
            "model": "gpt-5.6",
        },
    )
    assert res.status_code == 200
    body = res.json()
    blob = json.dumps(body)
    for secret in ("xai-secret-aaa", "sk-secret-bbb", "sk-ant-secret-ccc", "ccapi-secret-ddd", "secret"):
        assert secret not in blob
    for banned in ("xai_api_key", "openai_api_key", "anthropic_api_key", "ccapi_api_key"):
        assert banned not in body
    keys = body.get("has_api_key")
    assert keys["xai"] is True
    assert keys["openai"] is True
    assert keys["anthropic"] is True
    assert keys["ccapi"] is True
    assert body.get("ccapi_base_url") == "https://api.ccapi.ai/v1"
    assert body.get("provider") == "openai"
    assert body.get("model") == "gpt-5.6"

    pub = public_settings()
    dumped = json.dumps(pub)
    assert "xai-secret-aaa" not in dumped
    assert "sk-secret-bbb" not in dumped
    assert "sk-ant-secret-ccc" not in dumped
    assert "ccapi-secret-ddd" not in dumped

    again = client.get("/api/settings")
    assert again.status_code == 200
    again_blob = json.dumps(again.json())
    assert "xai-secret-aaa" not in again_blob
    assert "sk-secret-bbb" not in again_blob
    assert "sk-ant-secret-ccc" not in again_blob
    assert "ccapi-secret-ddd" not in again_blob


def test_chat_missing_openai_key_mentions_provider(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    save_settings({"provider": "openai", "model": "gpt-5.6"})
    from app.main import app

    client = TestClient(app)
    res = client.post("/api/chat", json={"message": "你好", "provider": "openai"})
    assert res.status_code == 503
    detail = res.json().get("detail") or ""
    assert "OpenAI" in detail
    assert any("\u4e0d" in detail or "未" in detail or "密钥" in detail for _ in (0,))
    assert "密钥" in detail or "API" in detail


def test_chat_missing_anthropic_key_mentions_provider(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    save_settings({"provider": "anthropic", "model": "claude-sonnet-5"})
    from app.main import app

    client = TestClient(app)
    res = client.post("/api/chat", json={"message": "你好", "provider": "anthropic"})
    assert res.status_code == 503
    detail = res.json().get("detail") or ""
    assert "Anthropic" in detail
    assert "密钥" in detail or "API" in detail


def test_openai_history_converts_to_anthropic_tools():
    from app.providers import openai_history_to_anthropic, openai_tools_to_anthropic

    tools = openai_tools_to_anthropic(
        [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "search",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            }
        ]
    )
    assert tools[0]["name"] == "web_search"
    assert tools[0]["input_schema"]["properties"]["query"]["type"] == "string"

    system, msgs = openai_history_to_anthropic(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query":"x"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
        ]
    )
    assert "sys" in system
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][0]["type"] == "tool_use"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0]["type"] == "tool_result"
    assert msgs[2]["content"][0]["tool_use_id"] == "call_1"


def test_chat_missing_ccapi_key_mentions_provider(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    save_settings({"provider": "ccapi", "model": "gpt-5.6"})
    from app.main import app

    client = TestClient(app)
    res = client.post("/api/chat", json={"message": "你好", "provider": "ccapi"})
    assert res.status_code == 503
    detail = res.json().get("detail") or ""
    assert "中转站" in detail
    assert "密钥" in detail or "API" in detail


def test_default_provider_is_ccapi_unless_saved(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    from app.settings import get_provider

    assert get_provider() == "ccapi"
    save_settings({"provider": "openai", "model": "gpt-5.6"})
    assert get_provider() == "openai"
    save_settings({"provider": "xai", "model": "grok-4.6"})
    assert get_provider() == "xai"
    save_settings({"provider": "anthropic", "model": "claude-sonnet-5"})
    assert get_provider() == "anthropic"


def test_ccapi_live_catalog_merges_when_mocked(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    save_settings({"ccapi_api_key": "sk-test-not-real", "provider": "ccapi", "model": "gpt-5.6"})
    monkeypatch.setattr("app.settings._skip_remote_model_fetch", lambda: False)

    def fake_fetch(base_url, api_key, *, timeout=8.0):
        assert "ccapi" in base_url
        assert api_key == "sk-test-not-real"
        return ["openai/gpt-5.2", "gpt-5.6"]

    monkeypatch.setattr("app.settings.fetch_ccapi_models", fake_fetch)
    from app.settings import models_catalog

    body = models_catalog()
    ccapi = next(row for row in body["providers"] if row["id"] == "ccapi")
    assert "gpt-5.6" in ccapi["models"]
    assert "openai/gpt-5.2" in ccapi["models"]
    assert json.dumps(body).count("sk-test-not-real") == 0


def test_fetch_ccapi_models_uses_mock_not_network(monkeypatch):
    from app.providers import fetch_ccapi_models, parse_openai_model_ids

    class DummyResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "deepseek-v3.2"}, {"id": "gpt-5.6"}]}

    class DummyClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            assert url.endswith("/models")
            assert headers["Authorization"] == "Bearer test-key"
            return DummyResp()

    monkeypatch.setattr("httpx.Client", DummyClient)
    ids = fetch_ccapi_models("https://api.ccapi.ai/v1", "test-key")
    assert "deepseek-v3.2" in ids
    assert parse_openai_model_ids({"models": ["a", "a", "b"]}) == ["a", "b"]


def test_fetch_ccapi_models_failure_returns_empty(monkeypatch):
    from app.providers import fetch_ccapi_models

    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            raise RuntimeError("no network")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("httpx.Client", BoomClient)
    assert fetch_ccapi_models("https://api.ccapi.ai/v1", "test-key") == []

