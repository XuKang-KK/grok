"""In-memory dangerous-command approvals (SSE + POST)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

APPROVAL_TIMEOUT_SEC = 60


@dataclass
class Approval:
    id: str
    command: str
    reason: str
    created_at: float = field(default_factory=time.time)
    decision: bool | None = None
    event: threading.Event = field(default_factory=threading.Event)

    def wait(self, timeout: float = APPROVAL_TIMEOUT_SEC) -> bool:
        self.event.wait(timeout)
        return self.decision is True

    def resolve(self, approved: bool) -> None:
        if self.decision is not None:
            return
        self.decision = bool(approved)
        self.event.set()

    def expired(self) -> bool:
        return (time.time() - self.created_at) > APPROVAL_TIMEOUT_SEC and self.decision is None

    def to_event(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "reason": self.reason,
            "timeout": APPROVAL_TIMEOUT_SEC,
        }


class ApprovalStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, Approval] = {}

    def create(self, command: str, reason: str) -> Approval:
        self._purge()
        rec = Approval(id=uuid.uuid4().hex, command=command, reason=reason)
        with self._lock:
            self._items[rec.id] = rec
        return rec

    def get(self, aid: str) -> Approval | None:
        with self._lock:
            rec = self._items.get(aid)
        if rec is None:
            return None
        if rec.expired() and rec.decision is None:
            rec.resolve(False)
        return rec

    def decide(self, aid: str, approved: bool) -> Approval | None:
        rec = self.get(aid)
        if rec is None:
            return None
        rec.resolve(approved)
        return rec

    def _purge(self) -> None:
        now = time.time()
        with self._lock:
            dead = [
                k
                for k, v in self._items.items()
                if (now - v.created_at) > APPROVAL_TIMEOUT_SEC * 4
            ]
            for k in dead:
                rec = self._items.pop(k, None)
                if rec and rec.decision is None:
                    rec.resolve(False)


store = ApprovalStore()
