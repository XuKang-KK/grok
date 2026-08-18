"""Terms of Service and Privacy Policy (product template, not lawyer-reviewed)."""

from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Any

LEGAL_UPDATED = "2026-08-18"
STATIC_DIR = Path(__file__).resolve().parent / "static"

_SECTION_RE = re.compile(r"^<!--\s*section:(terms|privacy)\s*-->\s*$", re.MULTILINE)


def get_operator_email() -> str:
    return (os.getenv("KK_OPERATOR_EMAIL") or "").strip()


def get_public_site() -> str:
    raw = (os.getenv("KK_PUBLIC_SITE") or "").strip().rstrip("/")
    return raw or "https://kkaiagent.com"


def operator_contact(lang: str = "zh") -> str:
    email = get_operator_email()
    if email:
        return email
    return "站点管理员" if lang != "en" else "the site administrator"


def _normalize_lang(lang: str | None) -> str:
    raw = (lang or "").strip().lower().replace("_", "-")
    return "en" if raw in {"en", "en-us", "en-gb", "english"} else "zh"


def load_legal_markdown(lang: str = "zh") -> dict[str, str]:
    code = _normalize_lang(lang)
    path = STATIC_DIR / f"legal-{code}.md"
    if not path.is_file():
        path = STATIC_DIR / "legal-zh.md"
    text = path.read_text(encoding="utf-8")
    parts: dict[str, str] = {"terms": "", "privacy": ""}
    current: str | None = None
    chunks: dict[str, list[str]] = {"terms": [], "privacy": []}
    for line in text.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            current = m.group(1)
            continue
        if current:
            chunks[current].append(line)
    for key, lines in chunks.items():
        parts[key] = "\n".join(lines).strip()
    return parts


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def md_to_html(markdown: str, *, contact: str) -> str:
    body = (markdown or "").replace("{{contact}}", contact)
    lines = body.splitlines()
    out: list[str] = []
    para: list[str] = []
    list_items: list[str] = []

    def flush_para() -> None:
        nonlocal para
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in list_items) + "</ul>")
            list_items = []

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            flush_para()
            flush_list()
            continue
        if line.startswith("## "):
            flush_para()
            flush_list()
            out.append(f"<h2>{_inline(line[3:].strip())}</h2>")
            continue
        if line.startswith("# "):
            flush_para()
            flush_list()
            out.append(f"<h1>{_inline(line[2:].strip())}</h1>")
            continue
        if line.startswith("- "):
            flush_para()
            list_items.append(_inline(line[2:].strip()))
            continue
        flush_list()
        para.append(line.strip())
    flush_para()
    flush_list()
    return "\n".join(out)


def legal_payload(lang: str | None = None) -> dict[str, Any]:
    code = _normalize_lang(lang)
    contact = operator_contact(code)
    docs = load_legal_markdown(code)
    return {
        "ok": True,
        "terms": md_to_html(docs["terms"], contact=contact),
        "privacy": md_to_html(docs["privacy"], contact=contact),
        "operator_email": get_operator_email(),
        "updated": LEGAL_UPDATED,
        "language": code,
        "contact": contact,
        "site": get_public_site(),
    }
