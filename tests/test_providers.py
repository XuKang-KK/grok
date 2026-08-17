"""Multi-provider catalog, settings secrecy, and missing-key errors."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.providers import FALLBACK_MODEL_IDS, family_for_model, model_label, strip_price_fields
from app.settings import public_settings, save_settings


PRICE_WORDS = ("price", "pricing", "cost", "fee")


def _assert_no_price_keys(obj, path="$"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            low = str(key).lower()
            assert low not in PRICE_WORDS and not low.endswith(("_price", "_pricing", "_cost", "_fee")), (path, key)
            _assert_no_price_keys(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _assert_no_price_keys(value, f"{path}[{i}]")


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


def test_family_for_model_grouping():
    assert family_for_model("gpt-5.6-terra") == "gpt"
    assert family_for_model("openai/gpt-5.2") == "gpt"
    assert family_for_model("gpt-5-mini") == "gpt"
    assert family_for_model("chatgpt-4o") == "gpt"
    assert family_for_model("o1-preview") == "gpt"
    assert family_for_model("o3-mini") == "gpt"
    assert family_for_model("o4-mini") == "gpt"
    assert family_for_model("claude-sonnet-5") == "claude"
    assert family_for_model("anthropic/claude-opus-5") == "claude"
    assert family_for_model("cursor-opus-4-8") == "claude"
    assert family_for_model("vendor/cursor-opus-4-8") == "claude"
    assert family_for_model("grok-4.5") == "grok"
    assert family_for_model("xai/grok-4.5") == "grok"
    assert family_for_model("deepseek-v3.2") is None
    assert family_for_model("gemini-2.5-flash") is None
    assert family_for_model("kimi-k2") is None
    assert family_for_model("grok-imagine-image-2.0") is None
    assert family_for_model("sora-2") is None
    assert family_for_model("") is None
    assert model_label("gpt-5.6-terra") == "GPT-5.6 Terra"
    assert model_label("gpt-5.4-mini") == "GPT-5.4 Mini"
    assert model_label("openai/gpt-5.2") == "GPT-5.2"
    assert model_label("cursor-opus-4-8") == "Cursor Opus-4-8"
    dirty = {
        "id": "gpt-5.6-terra",
        "price": 1.2,
        "pricing": {"input": 1},
        "cost": 3,
        "fee": 4,
        "ok": True,
        "nested": {"unit_cost": 9, "id": "x"},
    }
    clean = strip_price_fields(dirty)
    assert clean["id"] == "gpt-5.6-terra"
    assert clean["ok"] is True
    assert "price" not in clean
    assert "pricing" not in clean
    assert "cost" not in clean
    assert "fee" not in clean
    assert "unit_cost" not in clean["nested"]
    assert clean["nested"]["id"] == "x"


def test_models_catalog_has_families_and_no_prices(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = client.get("/api/models")
    assert res.status_code == 200
    body = res.json()
    assert body.get("ok") is True
    assert body.get("source") == "fallback"
    assert body.get("provider") == "ccapi"
    assert body.get("family") in {"gpt", "claude", "grok"}
    assert body.get("has_relay_key") is False
    assert body.get("model") == "gpt-5.6-terra"
    families = body.get("families") or {}
    assert set(families) == {"gpt", "claude", "grok"}
    gpt_ids = [row["id"] for row in families["gpt"]]
    claude_ids = [row["id"] for row in families["claude"]]
    grok_ids = [row["id"] for row in families["grok"]]
    assert "gpt-5.6-terra" in gpt_ids
    assert "gpt-5.6" not in gpt_ids
    assert "claude-sonnet-5" in claude_ids
    assert "cursor-opus-4-8" in claude_ids
    assert "grok-4.5" in grok_ids
    blob = json.dumps(body)
    assert "gemini" not in blob.lower()
    assert "kimi" not in blob.lower()
    for mid in FALLBACK_MODEL_IDS:
        fam = family_for_model(mid)
        assert fam in families
        assert mid in [row["id"] for row in families[fam]]
    terra = next(row for row in families["gpt"] if row["id"] == "gpt-5.6-terra")
    assert terra["label"] == "GPT-5.6 Terra"
    assert "providers" not in body
    assert "ccapi_base_url" not in body
    assert "api_key" not in body or body.get("api_key") in (None, "", False)
    for banned in ("xai_api_key", "openai_api_key", "anthropic_api_key", "ccapi_api_key"):
        assert banned not in body
        assert f'"{banned}"' not in blob
    _assert_no_price_keys(body)


def test_models_fallback_list_works_without_network(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)

    def boom(*args, **kwargs):
        raise AssertionError("fallback path must not hit the network")

    monkeypatch.setattr("app.settings.fetch_ccapi_models", boom)
    from app.settings import models_catalog

    body = models_catalog()
    assert body["source"] == "fallback"
    assert body["has_relay_key"] is False
    assert body["families"]["gpt"]
    assert body["families"]["claude"]
    assert body["families"]["grok"]
    assert body["model"] == "gpt-5.6-terra"
    _assert_no_price_keys(body)


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
    save_settings({"provider": "ccapi", "model": "gpt-5.6-terra"})
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


def test_default_chat_model_is_terra(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    from app.providers import DEFAULT_CHAT_MODEL
    from app.settings import get_model, get_provider

    assert DEFAULT_CHAT_MODEL == "gpt-5.6-terra"
    assert get_provider() == "ccapi"
    assert get_model() == "gpt-5.6-terra"


def test_ccapi_live_catalog_groups_when_mocked(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    save_settings({"ccapi_api_key": "sk-test-not-real", "provider": "ccapi", "model": "gpt-5.6-terra"})
    monkeypatch.setattr("app.settings._skip_remote_model_fetch", lambda: False)

    def fake_fetch(base_url, api_key, *, timeout=8.0):
        assert "ccapi" in base_url
        assert api_key == "sk-test-not-real"
        return [
            "openai/gpt-5.2",
            "gpt-5.6-terra",
            "claude-sonnet-5",
            "grok-4.5",
            "deepseek-v3.2",
            "gemini-2.5-flash",
            "kimi-k2",
        ]

    monkeypatch.setattr("app.settings.fetch_ccapi_models", fake_fetch)
    from app.settings import models_catalog

    body = models_catalog()
    assert body["source"] == "live"
    assert body["has_relay_key"] is True
    assert body["provider"] == "ccapi"
    gpt_ids = [row["id"] for row in body["families"]["gpt"]]
    assert "gpt-5.6-terra" in gpt_ids
    assert "openai/gpt-5.2" in gpt_ids
    assert "claude-sonnet-5" in [row["id"] for row in body["families"]["claude"]]
    assert "grok-4.5" in [row["id"] for row in body["families"]["grok"]]
    blob = json.dumps(body)
    assert "deepseek-v3.2" not in blob
    assert "gemini-2.5-flash" not in blob
    assert "kimi-k2" not in blob
    assert blob.count("sk-test-not-real") == 0
    _assert_no_price_keys(body)


def test_put_model_family_stores_ccapi(tmp_path, monkeypatch):
    _isolate_settings(tmp_path, monkeypatch)
    from app.main import app

    client = TestClient(app)
    res = client.put("/api/model", json={"family": "claude", "model": "claude-sonnet-5"})
    assert res.status_code == 200
    body = res.json()
    assert body.get("provider") == "ccapi"
    assert body.get("model") == "claude-sonnet-5"
    assert body.get("family") == "claude"
    _assert_no_price_keys(body)
    from app.settings import get_model, get_provider

    assert get_provider() == "ccapi"
    assert get_model() == "claude-sonnet-5"


def test_fetch_ccapi_models_uses_mock_not_network(monkeypatch):
    from app.providers import fetch_ccapi_models, parse_openai_model_ids

    class DummyResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"id": "deepseek-v3.2", "price": 0.1, "pricing": {"in": 1}},
                    {"id": "gpt-5.6-terra", "cost": 2, "fee": 3},
                ]
            }

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
    assert "gpt-5.6-terra" in ids
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
