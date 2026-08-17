"""Restart-free settings stored in gitignored data/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_MODEL = "grok-4.6"
DEFAULT_IMAGE_MODEL = "grok-imagine-image-2.0"

_SECRET_KEYS = frozenset({"xai_api_key"})


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
    for key, value in updates.items():
        if key in _SECRET_KEYS and (value is None or str(value).strip() == ""):
            continue
        if key == "xai_api_key":
            current[key] = str(value).strip()
        elif key == "grok_model":
            current[key] = str(value).strip()
        elif key == "image_model":
            current[key] = str(value).strip()
        elif key == "allow_local_browser":
            current[key] = bool(value)
        else:
            current[key] = value
    SETTINGS_FILE.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return current


def get_api_key() -> str:
    data = load_settings()
    return (
        str(data.get("xai_api_key") or "").strip()
        or (os.getenv("XAI_API_KEY") or "").strip()
    )


def get_model() -> str:
    data = load_settings()
    return (
        str(data.get("grok_model") or "").strip()
        or (os.getenv("GROK_MODEL") or os.getenv("XAI_MODEL") or DEFAULT_MODEL).strip()
        or DEFAULT_MODEL
    )


def get_image_model() -> str:
    data = load_settings()
    return (
        str(data.get("image_model") or "").strip()
        or (os.getenv("GROK_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL).strip()
        or DEFAULT_IMAGE_MODEL
    )


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


def public_settings() -> dict[str, Any]:
    """Safe view: never includes the raw API key."""
    return {
        "has_api_key": bool(get_api_key()),
        "model": get_model(),
        "image_model": get_image_model(),
        "allow_local_browser": allow_local_browser(),
    }
