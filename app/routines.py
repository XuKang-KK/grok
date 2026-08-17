"""Cron routines in Asia/Shanghai; persist to data/routines.json."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ROUTINES_FILE = DATA_DIR / "routines.json"

_CRON_BOUNDS = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # weekday (0=Monday in our parser? cron is 0=Sunday)
)

# Standard cron: 0=Sunday ... 6=Saturday. Also accept 7 as Sunday.
_lock = threading.Lock()
_stop = threading.Event()
_thread: threading.Thread | None = None
_last_fired: dict[str, str] = {}  # id -> "YYYY-MM-DD HH:MM"


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _expand_field(field: str, lo: int, hi: int, *, sunday_seven: bool = False) -> set[int]:
    field = (field or "").strip()
    if not field:
        raise ValueError("cron 字段为空")
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError("cron 字段无效")
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            if not step_s.isdigit() or int(step_s) < 1:
                raise ValueError(f"非法步长: {part}")
            step = int(step_s)
            part = base or "*"
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                raise ValueError(f"非法范围: {part}")
            start, end = int(a), int(b)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise ValueError(f"非法 cron 字段: {part}")
        if sunday_seven and end == 7:
            # 7 is Sunday alias. "*" / "0-7" mean the whole week 0-6.
            if start in (0, 7) and (part == "*" or start == 0):
                start = 0
                end = 6
            elif start == 7:
                start = end = 0
            else:
                values.add(0)
                end = 6
        if start > end:
            raise ValueError(f"非法范围: {start}-{end}")
        if start < lo or end > (6 if sunday_seven else hi):
            if not (sunday_seven and 0 <= start <= 6 and 0 <= end <= 6):
                raise ValueError(f"cron 值超出范围 {lo}-{hi}: {start}-{end}")
        cur = start
        while cur <= end:
            values.add(0 if sunday_seven and cur == 7 else cur)
            cur += step
    if not values:
        raise ValueError("cron 字段没有有效取值")
    return values


def parse_cron(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    """Parse a 5-field cron expression (min hour dom month dow).

    Timezone is not part of the expression; matching uses Asia/Shanghai.
    """
    expr = (expr or "").strip()
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron 必须是 5 段：分 时 日 月 周（Asia/Shanghai）")
    minute = _expand_field(parts[0], 0, 59)
    hour = _expand_field(parts[1], 0, 23)
    dom = _expand_field(parts[2], 1, 31)
    month = _expand_field(parts[3], 1, 12)
    dow = _expand_field(parts[4], 0, 7, sunday_seven=True)
    return minute, hour, dom, month, dow


def cron_matches(expr: str, when: datetime | None = None) -> bool:
    """Return True if `when` (default now in Asia/Shanghai) matches the cron."""
    minute, hour, dom, month, dow = parse_cron(expr)
    if when is None:
        when = datetime.now(TZ)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=TZ)
    else:
        when = when.astimezone(TZ)
    # cron weekday: 0=Sunday ... 6=Saturday. datetime.weekday(): 0=Monday.
    cron_dow = (when.weekday() + 1) % 7
    if when.minute not in minute:
        return False
    if when.hour not in hour:
        return False
    if when.month not in month:
        return False
    # If both DOM and DOW are restricted (not *), standard cron ORs them.
    # parse_cron already expanded; we treat fully-populated field as "*".
    dom_star = len(dom) == 31
    dow_star = len(dow) == 7
    day_ok = when.day in dom
    dow_ok = cron_dow in dow
    if not dom_star and not dow_star:
        return day_ok or dow_ok
    return day_ok and dow_ok


def _load() -> list[dict[str, Any]]:
    _ensure()
    if not ROUTINES_FILE.exists():
        return []
    try:
        data = json.loads(ROUTINES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [row for row in data if isinstance(row, dict) and row.get("id")]


def _save(items: list[dict[str, Any]]) -> None:
    _ensure()
    ROUTINES_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_routines() -> list[dict[str, Any]]:
    with _lock:
        return list(_load())


def create_routine(name: str, cron: str, prompt: str) -> dict[str, Any]:
    name = (name or "").strip()
    cron = (cron or "").strip()
    prompt = (prompt or "").strip()
    if not name:
        raise ValueError("名称不能为空")
    if not prompt:
        raise ValueError("提示词不能为空")
    parse_cron(cron)  # validate
    item = {
        "id": uuid.uuid4().hex,
        "name": name[:80],
        "cron": cron,
        "prompt": prompt[:4000],
        "paused": False,
        "created_at": datetime.now(TZ).isoformat(),
        "last_run_at": None,
        "last_status": None,
        "last_reply": None,
        "last_error": None,
    }
    with _lock:
        items = _load()
        items.append(item)
        _save(items)
    return item


def set_paused(rid: str, paused: bool) -> dict[str, Any] | None:
    with _lock:
        items = _load()
        for row in items:
            if row.get("id") == rid:
                row["paused"] = bool(paused)
                _save(items)
                return row
    return None


def delete_routine(rid: str) -> bool:
    with _lock:
        items = _load()
        kept = [r for r in items if r.get("id") != rid]
        if len(kept) == len(items):
            return False
        _save(kept)
    return True


def _update_result(rid: str, **fields: Any) -> None:
    with _lock:
        items = _load()
        for row in items:
            if row.get("id") == rid:
                row.update(fields)
                _save(items)
                return


def _run_one(item: dict[str, Any]) -> None:
    prompt = str(item.get("prompt") or "")
    rid = str(item.get("id"))
    try:
        from app.agent import run_turn

        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        reply = ""
        error = None
        for event in run_turn(messages, auto_deny_approvals=True):
            kind = event.get("type")
            if kind == "message":
                reply = event.get("content") or ""
            elif kind == "error":
                error = event.get("message")
        _update_result(
            rid,
            last_run_at=datetime.now(TZ).isoformat(),
            last_status="error" if error and not reply else "ok",
            last_reply=(reply or "")[:2000],
            last_error=error,
        )
    except Exception as exc:  # noqa: BLE001
        _update_result(
            rid,
            last_run_at=datetime.now(TZ).isoformat(),
            last_status="error",
            last_reply=None,
            last_error=f"{type(exc).__name__}: {exc}",
        )


def _tick() -> None:
    now = datetime.now(TZ)
    slot = now.strftime("%Y-%m-%d %H:%M")
    with _lock:
        items = list(_load())
    for item in items:
        if item.get("paused"):
            continue
        rid = str(item.get("id"))
        try:
            if not cron_matches(str(item.get("cron") or ""), now):
                continue
        except ValueError:
            continue
        if _last_fired.get(rid) == slot:
            continue
        _last_fired[rid] = slot
        threading.Thread(
            target=_run_one,
            args=(item,),
            name=f"routine-{rid[:8]}",
            daemon=True,
        ).start()


def _loop() -> None:
    while not _stop.wait(20):
        try:
            _tick()
        except Exception:
            pass


def start_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="routines", daemon=True)
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()
