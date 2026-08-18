"""On-disk multi-session store under data/sessions/."""

from __future__ import annotations

import json
import os
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


def session_max_messages() -> int:
    raw = (os.getenv("KK_SESSION_MAX_MESSAGES") or "").strip()
    if not raw:
        return 40
    try:
        return int(raw)
    except ValueError:
        return 40


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
        self.pending_images: list[dict[str, Any]] = list(data.get("pending_images") or [])
        self.provider: str = str(data.get("provider") or "")
        self.model: str = str(data.get("model") or "")
        self.owner: str = str(data.get("owner") or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "ui_turns": self.ui_turns,
            "pending_images": self.pending_images,
            "provider": self.provider,
            "model": self.model,
            "owner": self.owner,
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
            "provider": self.provider,
            "model": self.model,
            "owner": self.owner,
        }

    def trim(self, max_messages: int | None = None) -> None:
        """Keep the system message plus the last N non-system messages / ui_turns."""
        limit = session_max_messages() if max_messages is None else int(max_messages)
        if limit <= 0:
            return
        system = [m for m in self.messages if m.get("role") == "system"][:1]
        others = [m for m in self.messages if m.get("role") != "system"]
        if len(others) > limit:
            others = others[-limit:]
        self.messages = system + others
        if len(self.ui_turns) > limit:
            self.ui_turns = self.ui_turns[-limit:]

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
        self.pending_images = []
        self.title = "新对话"
        from app.settings import get_model, get_provider

        self.provider = get_provider()
        self.model = get_model(self.provider)
        self.updated_at = _now()

    def attach_pending_image(self, path: str, mime: str, filename: str) -> None:
        self.pending_images.append(
            {"path": path, "mime": mime, "filename": filename}
        )

    def take_user_content(self, text: str) -> str | list[dict[str, Any]]:
        """Build the next user message, attaching pending images as vision parts."""
        images = list(self.pending_images)
        self.pending_images = []
        if not images:
            return text
        from app.tools import file_to_data_url

        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for img in images:
            url = file_to_data_url(str(img.get("path") or ""))
            if url:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": url, "detail": "high"},
                    }
                )
        if len(parts) == 1:
            return text
        return parts

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

    def _meta_path(self, sid: str) -> Path:
        return SESSIONS_DIR / f"{sid}.meta.json"

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
        session.trim()
        session.updated_at = _now()
        self._path(session.id).write_text(
            json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            self._meta_path(session.id).write_text(
                json.dumps(session.meta(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass

    def get(self, sid: str, owner: str | None = None) -> Session | None:
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
        sess = Session(data)
        if owner is not None:
            if not sess.owner or sess.owner != owner:
                return None
        return sess

    def _meta_from_mapping(self, data: dict[str, Any], sid: str) -> dict[str, Any]:
        preview = str(data.get("preview") or "")
        if not preview:
            turns = data.get("ui_turns") if isinstance(data.get("ui_turns"), list) else []
            for turn in reversed(turns):
                if isinstance(turn, dict) and turn.get("role") == "user" and turn.get("content"):
                    preview = str(turn["content"]).replace("\n", " ").strip()[:80]
                    break
        return {
            "id": str(data.get("id") or sid),
            "title": str(data.get("title") or "新对话"),
            "created_at": str(data.get("created_at") or ""),
            "updated_at": str(data.get("updated_at") or ""),
            "preview": preview,
            "provider": str(data.get("provider") or ""),
            "model": str(data.get("model") or ""),
            "owner": str(data.get("owner") or ""),
        }

    def list(self, owner: str | None = None) -> list[dict[str, Any]]:
        ensure_dirs()
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in SESSIONS_DIR.glob("*.meta.json"):
            name = path.name
            if not name.endswith(".meta.json"):
                continue
            sid = name[: -len(".meta.json")]
            if not _safe_id(sid):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            meta = self._meta_from_mapping(raw, sid)
            if owner is not None:
                if not meta.get("owner") or meta["owner"] != owner:
                    continue
            items.append(meta)
            seen.add(sid)
        for path in SESSIONS_DIR.glob("*.json"):
            if path.name.endswith(".meta.json"):
                continue
            sid = path.stem
            if not _safe_id(sid) or sid in seen:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict) or raw.get("id") != sid:
                continue
            meta = self._meta_from_mapping(raw, sid)
            # Do not keep messages — drop the raw mapping after extracting keys.
            if owner is not None:
                if not meta.get("owner") or meta["owner"] != owner:
                    continue
            items.append(meta)
            seen.add(sid)
        items.sort(key=lambda m: m.get("updated_at") or "", reverse=True)
        return items

    def create(self, title: str = "新对话", owner: str = "") -> Session:
        ensure_dirs()
        sid = uuid.uuid4().hex
        from app.settings import get_model, get_provider

        provider = get_provider()
        sess = Session(
            {
                "id": sid,
                "title": title or "新对话",
                "created_at": _now(),
                "updated_at": _now(),
                "messages": new_system_messages(),
                "ui_turns": [],
                "provider": provider,
                "model": get_model(provider),
                "owner": owner or "",
            }
        )
        self.save(sess)
        return sess

    def select(self, sid: str, owner: str | None = None) -> Session | None:
        sess = self.get(sid, owner=owner)
        if sess is None:
            return None
        if owner is None:
            self._active_id = sid
            self._write_active(sid)
        return sess

    def active(self, owner: str | None = None) -> Session:
        if owner is not None:
            items = self.list(owner=owner)
            if items:
                sess = self.get(items[0]["id"], owner=owner)
                if sess is not None:
                    return sess
            return self.create(owner=owner)
        sess = self.get(self._active_id) if self._active_id else None
        if sess is not None:
            return sess
        created = self.create()
        self.select(created.id)
        return created

    def delete(self, sid: str, owner: str | None = None) -> bool:
        sess = self.get(sid, owner=owner)
        if sess is None:
            return False
        path = self._path(sid)
        if not path.exists():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        try:
            meta = self._meta_path(sid)
            if meta.exists():
                meta.unlink()
        except OSError:
            pass
        if owner is None and self._active_id == sid:
            remaining = self.list()
            if remaining:
                self.select(remaining[0]["id"])
            else:
                created = self.create()
                self.select(created.id)
        return True
