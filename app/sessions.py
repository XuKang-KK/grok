"""On-disk multi-session store under data/sessions/."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
ACTIVE_FILE = DATA_DIR / "active_session.json"

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_id(sid: str) -> bool:
    return bool(sid and _ID_RE.fullmatch(sid))


def new_system_messages() -> list[dict[str, Any]]:
    # Imported lazily to avoid circular imports at module load.
    from app.agent import build_system_prompt

    return [{"role": "system", "content": build_system_prompt()}]


class Session:
    def __init__(self, data: dict[str, Any]) -> None:
        self.id: str = data["id"]
        self.title: str = data.get("title") or "新对话"
        self.created_at: str = data.get("created_at") or _now()
        self.updated_at: str = data.get("updated_at") or self.created_at
        self.messages: list[dict[str, Any]] = list(data.get("messages") or [])
        self.ui_turns: list[dict[str, Any]] = list(data.get("ui_turns") or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "ui_turns": self.ui_turns,
        }

    def meta(self) -> dict[str, Any]:
        preview = ""
        for turn in reversed(self.ui_turns):
            if turn.get("role") == "user" and turn.get("content"):
                preview = str(turn["content"]).replace("\n", " ").strip()[:80]
                break
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "preview": preview,
        }

    def refresh_system_prompt(self) -> None:
        from app.agent import build_system_prompt

        prompt = build_system_prompt()
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = prompt
        else:
            self.messages.insert(0, {"role": "system", "content": prompt})

    def reset(self) -> None:
        self.messages = new_system_messages()
        self.ui_turns = []
        self.title = "新对话"
        self.updated_at = _now()

    def maybe_title_from_user(self, text: str) -> None:
        if self.title and self.title != "新对话":
            return
        if any(t.get("role") == "user" for t in self.ui_turns):
            return
        title = (text or "").strip().replace("\n", " ")
        if title:
            self.title = title[:28]


class SessionStore:
    def __init__(self) -> None:
        ensure_dirs()
        self._active_id = self._read_active()
        if not self._active_id or self.get(self._active_id) is None:
            sess = self.create()
            self.select(sess.id)

    def _path(self, sid: str) -> Path:
        return SESSIONS_DIR / f"{sid}.json"

    def _read_active(self) -> str | None:
        if not ACTIVE_FILE.exists():
            return None
        try:
            data = json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))
            sid = data.get("id") if isinstance(data, dict) else None
            return sid if isinstance(sid, str) and _safe_id(sid) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_active(self, sid: str) -> None:
        ensure_dirs()
        ACTIVE_FILE.write_text(
            json.dumps({"id": sid}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def save(self, session: Session) -> None:
        ensure_dirs()
        session.updated_at = _now()
        self._path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get(self, sid: str) -> Session | None:
        if not _safe_id(sid):
            return None
        path = self._path(sid)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("id") != sid:
            return None
        return Session(data)

    def list(self) -> list[dict[str, Any]]:
        ensure_dirs()
        items: list[dict[str, Any]] = []
        for path in SESSIONS_DIR.glob("*.json"):
            sid = path.stem
            sess = self.get(sid)
            if sess:
                items.append(sess.meta())
        items.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
        return items

    def create(self, title: str = "新对话") -> Session:
        ensure_dirs()
        sid = uuid.uuid4().hex
        sess = Session(
            {
                "id": sid,
                "title": title or "新对话",
                "created_at": _now(),
                "updated_at": _now(),
                "messages": new_system_messages(),
                "ui_turns": [],
            }
        )
        self.save(sess)
        return sess

    def select(self, sid: str) -> Session | None:
        sess = self.get(sid)
        if sess is None:
            return None
        self._active_id = sid
        self._write_active(sid)
        return sess

    def active(self) -> Session:
        sess = self.get(self._active_id) if self._active_id else None
        if sess is not None:
            return sess
        created = self.create()
        self.select(created.id)
        return created

    def delete(self, sid: str) -> bool:
        if not _safe_id(sid):
            return False
        path = self._path(sid)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        if self._active_id == sid:
            remaining = self.list()
            if remaining:
                self.select(remaining[0]["id"])
            else:
                created = self.create()
                self.select(created.id)
        return True
