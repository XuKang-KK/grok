"""Daily token / cost quota and a fail-fast circuit for public mode."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.settings import DATA_DIR, is_public_mode

USAGE_NAME = "usage.json"

_lock = threading.Lock()
_circuit = {"global": False, "visitors": set()}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _usage_path() -> Path:
    return DATA_DIR / USAGE_NAME


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def visitor_token_limit() -> int:
    return _int_env("KK_VISITOR_TOKEN_LIMIT", 200_000)


def global_token_limit() -> int:
    return _int_env("KK_GLOBAL_TOKEN_LIMIT", 2_000_000)


def visitor_cost_limit() -> int:
    return _int_env("KK_VISITOR_COST_CENTS", 0)


def global_cost_limit() -> int:
    return _int_env("KK_GLOBAL_COST_CENTS", 0)


def estimate_tokens(*texts: str) -> int:
    total = 0
    for text in texts:
        total += max(1, len(text or "") // 4)
    return max(1, total)


def tokens_from_usage(usage: Any) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        try:
            return int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(getattr(usage, "total_tokens", 0) or 0)
    except (TypeError, ValueError):
        return 0


def reset_quota() -> None:
    with _lock:
        _circuit['global'] = False
        _circuit['visitors'].clear()
        path = _usage_path()
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass


def _empty(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "global_tokens": 0,
        "global_cost_cents": 0,
        "visitors": {},
    }


def _load_unlocked() -> dict[str, Any]:
    day = _today()
    path = _usage_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (OSError, json.JSONDecodeError):
            data = {}
    if str(data.get("day") or "") != day:
        _circuit['global'] = False
        _circuit['visitors'].clear()
        return _empty(day)
    data.setdefault("day", day)
    data.setdefault("global_tokens", 0)
    data.setdefault("global_cost_cents", 0)
    visitors = data.get("visitors")
    if not isinstance(visitors, dict):
        data["visitors"] = {}
    return data


def _save_unlocked(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _usage_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _visitor_row(data: dict[str, Any], vid: str) -> dict[str, int]:
    visitors = data.setdefault("visitors", {})
    row = visitors.get(vid) if vid else None
    if not isinstance(row, dict):
        row = {"tokens": 0, "cost_cents": 0}
        if vid:
            visitors[vid] = row
    row.setdefault("tokens", 0)
    row.setdefault("cost_cents", 0)
    return row


def _over_limits(data: dict[str, Any], vid: str) -> bool:
    row = _visitor_row(data, vid) if vid else {"tokens": 0, "cost_cents": 0}
    v_tok = visitor_token_limit()
    g_tok = global_token_limit()
    v_cost = visitor_cost_limit()
    g_cost = global_cost_limit()
    if v_tok > 0 and int(row.get("tokens") or 0) >= v_tok:
        return True
    if g_tok > 0 and int(data.get("global_tokens") or 0) >= g_tok:
        return True
    if v_cost > 0 and int(row.get("cost_cents") or 0) >= v_cost:
        return True
    if g_cost > 0 and int(data.get("global_cost_cents") or 0) >= g_cost:
        return True
    return False


def _quota_error() -> HTTPException:
    return HTTPException(status_code=429, detail="用量已达上限")


def check_quota(vid: str) -> HTTPException | None:
    """Return a 429 if this visitor / the global bucket is over. Local mode: None."""
    if not is_public_mode():
        return None
    key = (vid or "").strip()
    with _lock:
        if _circuit['global'] or (key and key in _circuit['visitors']):
            return _quota_error()
        data = _load_unlocked()
        if _over_limits(data, key):
            if key:
                _circuit['visitors'].add(key)
            if _over_limits(data, ""):
                _circuit['global'] = True
            return _quota_error()
    return None


def add_usage(vid: str, tokens: int, cost_cents: int = 0) -> None:
    key = (vid or "").strip()
    try:
        tokens_n = int(tokens)
    except (TypeError, ValueError):
        tokens_n = 0
    try:
        cost_n = int(cost_cents)
    except (TypeError, ValueError):
        cost_n = 0
    if tokens_n < 0:
        tokens_n = 0
    if cost_n < 0:
        cost_n = 0
    if tokens_n == 0 and cost_n == 0:
        return
    with _lock:
        data = _load_unlocked()
        data["global_tokens"] = int(data.get("global_tokens") or 0) + tokens_n
        data["global_cost_cents"] = int(data.get("global_cost_cents") or 0) + cost_n
        if key:
            row = _visitor_row(data, key)
            row["tokens"] = int(row.get("tokens") or 0) + tokens_n
            row["cost_cents"] = int(row.get("cost_cents") or 0) + cost_n
        try:
            _save_unlocked(data)
        except OSError:
            pass
        if is_public_mode() and _over_limits(data, key):
            if key:
                _circuit['visitors'].add(key)
            if _over_limits(data, ""):
                _circuit['global'] = True
