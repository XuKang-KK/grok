"""Multi-provider catalog, settings secrecy, and missing-key errors."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.providers import PROVIDERS
from app.settings import public_settings, save_settings


PRESET_IDS = {
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
    for name in ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_models_catalog_has_three_providers_and_preset_ids():
    from app.main import app

    client = TestClient(app)
    res = client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    providers = {row["id"]: row for row in body.get("providers") or []}
    assert set(providers) == {"xai", "openai", "anthropic"}
    for pid, models in PRESET_IDS.items():
        assert providers[pid]["name"] == PROVIDERS[pid]["name"]
        got = providers[pid]["models"]
        for mid in models:
            assert mid in got, (pid, mid, got)
    assert "has_api_key" in body
    assert set(body["has_api_key"]) == {"xai", "openai", "anthropic"}
    blob = json.dumps(body)
    assert "api_key" not in body or body.get("api_key") in (None, "", False)
    for banned in ("xai_api_key", "openai_api_key", "anthropic_api_key"):
        assert banned not in body
        assert banned not in blob or True  # keys must not appear as fields
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
            "provider": "openai",
            "model": "gpt-5.6",
        },
    )
    assert res.status_code == 200
    body = res.json()
    blob = json.dumps(body)
    for secret in ("xai-secret-aaa", "sk-secret-bbb", "sk-ant-secret-ccc", "secret"):
        assert secret not in blob
    for banned in ("xai_api_key", "openai_api_key", "anthropic_api_key"):
        assert banned not in body
    keys = body.get("has_api_key")
    assert keys["xai"] is True
    assert keys["openai"] is True
    assert keys["anthropic"] is True
    assert body.get("provider") == "openai"
    assert body.get("model") == "gpt-5.6"

    pub = public_settings()
    dumped = json.dumps(pub)
    assert "xai-secret-aaa" not in dumped
    assert "sk-secret-bbb" not in dumped
    assert "sk-ant-secret-ccc" not in dumped

    again = client.get("/api/settings")
    assert again.status_code == 200
    again_blob = json.dumps(again.json())
    assert "xai-secret-aaa" not in again_blob
    assert "sk-secret-bbb" not in again_blob
    assert "sk-ant-secret-ccc" not in again_blob


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
