"""Restart-free settings stored in gitignored data/settings.json."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.providers import (
    CCAPI_BASE_PRESETS,
    CCAPI_BASE_URL,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_PROVIDER,
    FALLBACK_MODEL_IDS,
    PROVIDER_IDS,
    PROVIDERS,
    family_catalog_payload,
    family_for_model,
    fetch_ccapi_models,
    fetch_ccapi_public_catalogs,
    merge_model_ids,
    model_label,
    normalize_ccapi_base_url,
    normalize_provider,
    default_model_for,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_MODEL = default_model_for(DEFAULT_PROVIDER)

logger = logging.getLogger("kk")
_PUBLIC_LOCK_WARNED = False

_SECRET_KEYS = frozenset(
    {"xai_api_key", "openai_api_key", "anthropic_api_key", "ccapi_api_key", "access_token"}
)
_STR_KEYS = frozenset({"provider", "model", "grok_model", "image_model", "language", "ccapi_base_url"})


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


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
        elif key == "ccapi_base_url":
            raw = str(value or "").strip()
            if raw:
                current[key] = normalize_ccapi_base_url(raw)
        elif key in _STR_KEYS:
            current[key] = str(value).strip()
        elif key == "allow_local_browser":
            current[key] = bool(value)
        elif key == "public_mode":
            wanted = _truthy(value)
            if not wanted and _env_public():
                # Env lock: do not persist a downgrade that would look like "off".
                continue
            current[key] = wanted
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


def get_ccapi_base_url() -> str:
    """Configured CCAPI base. Settings override env. Never a secret."""
    data = load_settings()
    stored = str(data.get("ccapi_base_url") or "").strip()
    env = (os.getenv("CCAPI_BASE_URL") or "").strip()
    return normalize_ccapi_base_url(stored or env or CCAPI_BASE_URL)


def get_provider_base_url(provider: str | None = None) -> str:
    pid = normalize_provider(provider) if provider else get_provider()
    if pid == "ccapi":
        return get_ccapi_base_url()
    return str(PROVIDERS[pid]["base_url"])


def get_language() -> str:
    data = load_settings()
    raw = str(data.get("language") or "").strip().lower()
    return "en" if raw == "en" else "zh"


def _env_public() -> bool:
    """True if KK_PUBLIC or PUBLIC_MODE is a truthy environment value."""
    for name in ("KK_PUBLIC", "PUBLIC_MODE"):
        raw = os.getenv(name)
        if raw is not None and str(raw).strip() != "" and _truthy(raw):
            return True
    return False


def _warn_public_env_lock() -> None:
    global _PUBLIC_LOCK_WARNED
    if _PUBLIC_LOCK_WARNED:
        return
    _PUBLIC_LOCK_WARNED = True
    logger.warning("对外模式由环境变量锁定，忽略 settings.json 的关闭")


def is_public_mode() -> bool:
    """True if any source is truthy: env KK_PUBLIC, env PUBLIC_MODE, or settings.public_mode.

    Fail-secure: a truthy env wins. settings.json ``public_mode: false`` cannot
    turn the process off when the environment already enabled public mode.
    """
    env_on = _env_public()
    data = load_settings()
    settings_on = "public_mode" in data and _truthy(data.get("public_mode"))
    if env_on:
        if "public_mode" in data and not _truthy(data.get("public_mode")):
            _warn_public_env_lock()
        return True
    return bool(settings_on)


def allow_local_browser() -> bool:
    if is_public_mode():
        return False
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


def public_settings(*, is_admin: bool = False) -> dict[str, Any]:
    """Safe view: never includes raw API keys."""
    provider = get_provider()
    model = get_model(provider)
    keys = has_api_keys()
    payload: dict[str, Any] = {
        "has_api_key": keys,
        "has_relay_key": bool(keys.get("ccapi")),
        "provider": provider,
        "family": family_for_model(model) or "gpt",
        "model": model,
        "model_label": model_label(model),
        "image_model": get_image_model(),
        "allow_local_browser": allow_local_browser(),
        "language": get_language(),
        "has_access_token": bool(get_access_token()),
        "auth_required": bool(get_access_token()),
        "ccapi_base_url": get_ccapi_base_url(),
        "ccapi_base_presets": list(CCAPI_BASE_PRESETS),
        "public_mode": is_public_mode(),
        "is_admin": bool(is_admin),
    }
    if is_public_mode() and not is_admin:
        payload.pop("ccapi_base_url", None)
        payload.pop("ccapi_base_presets", None)
        payload["allow_local_browser"] = False
        payload["is_admin"] = False
    return payload


def _skip_remote_model_fetch() -> bool:
    """Unit tests must never hit the live CCAPI network."""
    import sys

    return "pytest" in sys.modules


def models_catalog() -> dict[str, Any]:
    pub = public_settings()
    has_relay = bool(get_api_key("ccapi"))
    extra: list[str] = []
    source = "fallback"
    if not _skip_remote_model_fetch():
        if has_relay:
            extra = merge_model_ids(
                extra,
                fetch_ccapi_models(get_ccapi_base_url(), get_api_key("ccapi"), timeout=8.0),
            )
        extra = merge_model_ids(extra, fetch_ccapi_public_catalogs(timeout=8.0))
        if extra:
            source = "live"
    ids = list(FALLBACK_MODEL_IDS)
    if extra:
        ids = merge_model_ids(ids, extra)
    return family_catalog_payload(
        model_ids=ids,
        source=source,
        provider=pub["provider"],
        model=pub["model"],
        has_relay_key=has_relay,
        family=pub.get("family"),
    )


def strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    banned = (
        "xai_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "ccapi_api_key",
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


def get_allowed_hosts() -> list[str]:
    """Optional Host allow-list. Empty = no extra check."""
    load_dotenv(ENV_FILE, override=False)
    raw = (os.getenv("KK_ALLOWED_HOSTS") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def has_any_provider_key() -> bool:
    return any(has_api_keys().values())
