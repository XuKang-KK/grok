"""i18n dictionary and language setting."""

from __future__ import annotations

import json
from pathlib import Path

from app.settings import get_language, public_settings, save_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent
I18N_FILE = PROJECT_ROOT / "app" / "static" / "i18n.json"

CORE_KEYS = (
    "app.name",
    "settings",
    "routines",
    "model",
    "newChat",
    "empty.title",
    "composer.placeholder",
    "lang.switchToEn",
    "lang.switchToZh",
    "family.gpt",
    "family.claude",
    "family.grok",
    "family.gemini",
    "rail.refresh",
    "rail.noRelayKey",
    "settings.advanced",
    "settings.ccapiKey",
)


def test_i18n_dict_has_zh_and_en_for_core_keys():
    data = json.loads(I18N_FILE.read_text(encoding="utf-8"))
    assert "zh" in data and "en" in data
    assert set(data["zh"]) == set(data["en"])
    for key in CORE_KEYS:
        assert key in data["zh"] and str(data["zh"][key]).strip()
        assert key in data["en"] and str(data["en"][key]).strip()
    assert data["zh"]["app.name"] == "KK AI助手"
    assert data["en"]["app.name"] == "KK AI助手"
    assert data["zh"]["settings"] != data["en"]["settings"]
    for fam, brand in (("family.gpt", "GPT"), ("family.claude", "Claude"), ("family.grok", "Grok"), ("family.gemini", "Gemini")):
        assert data["zh"][fam] == brand
        assert data["en"][fam] == brand


def test_language_setting_defaults_zh_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.settings.ENV_FILE", tmp_path / "no.env")
    assert get_language() == "zh"
    assert public_settings()["language"] == "zh"
    save_settings({"language": "en"})
    assert get_language() == "en"
    assert public_settings()["language"] == "en"
    save_settings({"language": "fr"})
    assert get_language() == "zh"


def test_pwa_manifest_and_service_worker_exist():
    static = PROJECT_ROOT / "app" / "static"
    manifest = json.loads((static / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "KK AI助手"
    assert manifest["lang"] == "zh-CN"
    assert (static / "sw.js").is_file()
    assert (static / "icons" / "icon-192.png").is_file()
    assert (static / "icons" / "icon-512.png").is_file()


def test_pwa_routes_and_settings_language(tmp_path, monkeypatch):
    monkeypatch.setattr("app.settings.SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr("app.settings.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.settings.ENV_FILE", tmp_path / "no.env")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    man = client.get("/manifest.webmanifest")
    assert man.status_code == 200
    body = man.json()
    assert body["name"] == "KK AI助手"
    assert body["lang"] == "zh-CN"
    sw = client.get("/sw.js")
    assert sw.status_code == 200
    assert "serviceWorker" in sw.text or "CACHE" in sw.text
    res = client.put("/api/settings", json={"language": "en"})
    assert res.status_code == 200
    assert res.json().get("language") == "en"
    again = client.get("/api/settings")
    assert again.json().get("language") == "en"
