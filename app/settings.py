"""Restart-free settings stored in gitignored data/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.providers import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_PROVIDER,
    PROVIDER_IDS,
    PROVIDERS,
    catalog_payload,
    default_model_for,
    normalize_provider,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_MODEL = default_model_for(DEFAULT_PROVIDER)

_SECRET_KEYS = frozenset({"xai_api_key", "openai_api_key", "anthropic_api_key", "access_token"})
_STR_KEYS = frozenset({"provider", "model", "grok_model", "image_model", "language"})


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    """Read settings from disk on every call (no process-wide cache)."""
    load_dotenv(ENV_FILE, override=False)
    data: dict[str, Any] = {}
    if SETTINGS_FILE.exists():
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    return data


def save_settings(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge updates into data/settings.json. Empty secret strings mean 'keep'."""
    _ensure_data_dir()
    current = load_settings()
    if updates.get("clear_access_token"):
        current.pop("access_token", None)
        current.pop("kk_access_token", None)
    for key, value in updates.items():
        if key in {"clear_access_token"}:
            continue
        if key in _SECRET_KEYS and (value is None or str(value).strip() == ""):
            continue
        if key in _SECRET_KEYS:
            current[key] = str(value).strip()
        elif key == "provider":
            current[key] = normalize_provider(str(value) if value is not None else "")
        elif key == "language":
            raw = str(value or "").strip().lower().replace("_", "-")
            current[key] = "en" if raw in {"en", "en-us", "en-gb", "english"} else "zh"
        elif key in _STR_KEYS:
            current[key] = str(value).strip()
        elif key == "allow_local_browser":
            current[key] = bool(value)
        else:
            current[key] = value
    if "model" in updates and updates.get("model") not in (None, ""):
        current["model"] = str(updates["model"]).strip()
        if current.get("provider", DEFAULT_PROVIDER) == "xai" or "grok_model" not in current:
            current["grok_model"] = current["model"]
    elif "grok_model" in updates and updates.get("grok_model") not in (None, ""):
        current["grok_model"] = str(updates["grok_model"]).strip()
        current["model"] = current["grok_model"]
    SETTINGS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return current


def get_api_key(provider: str | None = None) -> str:
    """Return the key for `provider` (default: xAI, for image gen / compat)."""
    pid = normalize_provider(provider) if provider else "xai"
    meta = PROVIDERS[pid]
    data = load_settings()
    return (
        str(data.get(meta["key_field"]) or "").strip()
        or (os.getenv(meta["env_key"]) or "").strip()
    )


def get_provider() -> str:
    data = load_settings()
    raw = str(data.get("provider") or "").strip()
    if raw:
        return normalize_provider(raw)
    return DEFAULT_PROVIDER


def get_model(provider: str | None = None) -> str:
    pid = normalize_provider(provider) if provider else get_provider()
    data = load_settings()
    stored = (
        str(data.get("model") or "").strip()
        or str(data.get("grok_model") or "").strip()
    )
    if stored and (provider is None or pid == get_provider()):
        return stored
    if pid == "xai":
        env_model = (os.getenv("GROK_MODEL") or os.getenv("XAI_MODEL") or "").strip()
        if env_model and (provider is None or pid == get_provider()):
            return env_model
        if stored and pid == "xai":
            return stored
    return default_model_for(pid)


def get_image_model() -> str:
    data = load_settings()
    return (
        str(data.get("image_model") or "").strip()
        or (os.getenv("GROK_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL).strip()
        or DEFAULT_IMAGE_MODEL
    )


def get_language() -> str:
    data = load_settings()
    raw = str(data.get("language") or "").strip().lower()
    return "en" if raw == "en" else "zh"


def allow_local_browser() -> bool:
    data = load_settings()
    if "allow_local_browser" in data:
        return bool(data.get("allow_local_browser"))
    return (os.getenv("GROK_ALLOW_LOCAL_BROWSER") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def has_api_keys() -> dict[str, bool]:
    return {pid: bool(get_api_key(pid)) for pid in PROVIDER_IDS}


def public_settings() -> dict[str, Any]:
    """Safe view: never includes raw API keys."""
    provider = get_provider()
    return {
        "has_api_key": has_api_keys(),
        "provider": provider,
        "model": get_model(provider),
        "image_model": get_image_model(),
        "allow_local_browser": allow_local_browser(),
        "language": get_language(),
        "has_access_token": bool(get_access_token()),
        "auth_required": bool(get_access_token()),
    }


def models_catalog() -> dict[str, Any]:
    pub = public_settings()
    return catalog_payload(
        has_api_key=pub["has_api_key"],
        provider=pub["provider"],
        model=pub["model"],
    )


def strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    banned = (
        "xai_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "api_key",
        "key",
        "access_token",
        "kk_access_token",
        "ACCESS_TOKEN",
        "KK_ACCESS_TOKEN",
        "token",
        "kk_token",
    )
    for name in banned:
        payload.pop(name, None)
    return payload


def get_host() -> str:
    """Bind address. Default 127.0.0.1 — never 0.0.0.0 unless the user sets HOST."""
    load_dotenv(ENV_FILE, override=False)
    raw = (os.getenv("HOST") or "").strip()
    return raw or "127.0.0.1"


def get_port() -> int:
    load_dotenv(ENV_FILE, override=False)
    raw = (os.getenv("PORT") or "").strip() or "8000"
    try:
        port = int(raw)
    except ValueError:
        return 8000
    if port < 1 or port > 65535:
        return 8000
    return port


def get_bind() -> str:
    return f"{get_host()}:{get_port()}"


def get_access_token() -> str:
    """Optional LAN access token. Settings override env. Never log the value."""
    data = load_settings()
    stored = str(data.get("access_token") or data.get("kk_access_token") or "").strip()
    if stored:
        return stored
    return (os.getenv("KK_ACCESS_TOKEN") or os.getenv("ACCESS_TOKEN") or "").strip()


def get_cors_origins() -> list[str]:
    load_dotenv(ENV_FILE, override=False)
    raw = (os.getenv("KK_CORS_ORIGINS") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def has_any_provider_key() -> bool:
    return any(has_api_keys().values())
