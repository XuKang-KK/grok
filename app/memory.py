"""Persistent fact memory (JSON list on disk)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MEMORY_FILE = DATA_DIR / "memory.json"

MAX_FACT_CHARS = 500
MAX_FACTS_STORED = 200
PROMPT_FACTS = 20


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    ensure_data_dir()
    if not MEMORY_FILE.exists():
        return []
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    items: list[dict] = []
    for row in data:
        if isinstance(row, str):
            fact = row.strip()
            if fact:
                items.append({"fact": fact, "created_at": ""})
        elif isinstance(row, dict):
            fact = str(row.get("fact") or "").strip()
            if fact:
                items.append(
                    {
                        "fact": fact,
                        "created_at": str(row.get("created_at") or ""),
                    }
                )
    return items


def _save(facts: list[dict]) -> None:
    ensure_data_dir()
    MEMORY_FILE.write_text(
        json.dumps(facts, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def memory_write(fact: str) -> str:
    fact = (fact or "").strip()
    if not fact:
        return json.dumps({"error": "fact 不能为空"}, ensure_ascii=False)
    if len(fact) > MAX_FACT_CHARS:
        return json.dumps(
            {"error": f"事实过长（{len(fact)} 字符），上限 {MAX_FACT_CHARS}"},
            ensure_ascii=False,
        )
    facts = _load()
    facts.append({"fact": fact, "created_at": _now()})
    facts = facts[-MAX_FACTS_STORED:]
    _save(facts)
    return json.dumps(
        {"ok": True, "fact": fact, "count": len(facts)},
        ensure_ascii=False,
    )


def memory_read(query: str = "") -> str:
    facts = _load()
    needle = (query or "").strip().lower()
    if needle:
        matched = [f for f in facts if needle in f.get("fact", "").lower()]
    else:
        matched = facts[-PROMPT_FACTS:]
    return json.dumps(
        {"facts": matched, "total": len(facts), "query": query or ""},
        ensure_ascii=False,
    )


def memory_summary(limit: int = PROMPT_FACTS) -> str:
    facts = _load()
    recent = facts[-limit:]
    if not recent:
        return ""
    return "\n".join(f"- {item['fact']}" for item in recent)
