"""Append-only public-mode audit log and visitor ban list. Never logs secrets."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app.settings import DATA_DIR

AUDIT_NAME = "audit.jsonl"
BANS_NAME = "bans.json"

_VID_RE = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.Lock()

_SECRET_HINTS = (
    "api_key",
    "access_token",
    "kk_access_token",
    "kk_token",
    "authorization",
    "cookie",
    "bearer ",
    "sk-",
    "xai-",
    "sk-ant-",
    "password",
    "password_hash",
)


def _audit_path() -> Path:
    return DATA_DIR / AUDIT_NAME


def _bans_path() -> Path:
    return DATA_DIR / BANS_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_detail(detail: Any) -> str:
    text = str(detail or "").replace("\n", " ").replace("\r", " ").strip()
    lower = text.lower()
    for hint in _SECRET_HINTS:
        if hint in lower:
            return "[redacted]"
    return text[:80]


def _safe_vid(vid: str) -> str:
    raw = (vid or "").strip()
    if raw.startswith("user:"):
        raw = raw[5:]
    return raw if _VID_RE.fullmatch(raw) else ""


def _safe_user(user: str) -> str:
    raw = (user or "").strip()
    if raw.startswith("user:"):
        raw = raw[5:]
    return raw if _VID_RE.fullmatch(raw) else ""


def write_audit(
    event: str,
    *,
    vid: str = "",
    user: str = "",
    admin: bool = False,
    path: str = "",
    tokens: int | None = None,
    tools: Iterable[str] | None = None,
    detail: str = "",
    ip: str = "",
) -> None:
    """Append one audit line. Never write secrets, tokens, cookies, or full text."""
    names: list[str] = []
    if tools:
        for name in tools:
            raw = str(name or "").strip()
            if raw and raw not in names:
                names.append(raw[:40])
    row: dict[str, Any] = {
        "ts": _now(),
        "event": str(event or "")[:40],
        "vid": _safe_vid(vid),
        "user": _safe_user(user),
        "admin": bool(admin),
        "path": str(path or "")[:120],
        "tokens": int(tokens) if tokens else 0,
        "tools": names,
        "detail": _safe_detail(detail),
        "ip": str(ip or "")[:64],
    }
    line = json.dumps(row, ensure_ascii=False)
    with _lock:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with _audit_path().open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def _clean_id_list(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item or "").strip()
        if raw.startswith("user:"):
            raw = raw[5:]
        if not _VID_RE.fullmatch(raw) or raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def _load_ban_file() -> dict[str, list[str]]:
    path = _bans_path()
    if not path.exists():
        return {"vids": [], "users": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"vids": [], "users": []}
    if not isinstance(raw, dict):
        return {"vids": [], "users": []}
    return {
        "vids": _clean_id_list(raw.get("vids")),
        "users": _clean_id_list(raw.get("users")),
    }


def _file_vids() -> list[str]:
    return _load_ban_file()["vids"]


def _file_users() -> list[str]:
    return _load_ban_file()["users"]


def _env_vids() -> list[str]:
    raw = (os.getenv("KK_BANNED_VIDS") or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        vid = part.strip()
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append(vid)
    return out


def list_bans() -> list[str]:
    """Effective ban list: file first, then env extras."""
    seen: set[str] = set()
    out: list[str] = []
    for vid in _file_vids() + _env_vids():
        if vid in seen:
            continue
        seen.add(vid)
        out.append(vid)
    return out


def list_banned_users() -> list[str]:
    return list(_file_users())


def save_bans(vids: Iterable[str], users: Iterable[str] | None = None) -> list[str]:
    cleaned = _clean_id_list(list(vids or []))
    existing = _load_ban_file()
    if users is None:
        cleaned_users = existing["users"]
    else:
        cleaned_users = _clean_id_list(list(users or []))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _bans_path().write_text(
        json.dumps({"vids": cleaned, "users": cleaned_users}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return list_bans()


def is_banned(vid: str = "", user_id: str = "") -> bool:
    raw = (vid or "").strip()
    uid = (user_id or "").strip()
    if raw.startswith("user:"):
        uid = uid or raw[5:]
        raw = ""
    if uid.startswith("user:"):
        uid = uid[5:]
    file_state = _load_ban_file()
    env = set(_env_vids())
    if raw and (raw in file_state["vids"] or raw in env):
        return True
    if uid and (uid in file_state["users"] or uid in file_state["vids"] or uid in env):
        return True
    return False
