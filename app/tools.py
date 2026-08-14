"""Local tools for the Grok assistant.

Sandbox root is ./workspace relative to the project.
This is a local-dev sandbox, NOT a security jail: it blocks a few
obviously dangerous command patterns and confines file reads, but
does not isolate the process, network, or host filesystem.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = (PROJECT_ROOT / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = 120_000
MAX_CMD_OUTPUT = 16_000
CMD_TIMEOUT_SEC = 15
SEARCH_RESULTS = 5

# Whole-command scans. Not exhaustive — local-dev only.
_DANGEROUS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bsudo\b",
        r"\bmkfs(\.\w+)?\b",
        r"\bdd\b.+\bif=",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\bhalt\b",
        r"\binit\s+0\b",
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*;\s*\}",
        r"\brm\s+(-[a-zA-Z]*\s+)*-r[a-zA-Z]*f[a-zA-Z]*\s+(/|\*|/home|/etc|/usr|/var|/boot|~)",
        r"\brm\s+(-[a-zA-Z]*\s+)*-f[a-zA-Z]*r[a-zA-Z]*\s+(/|\*|/home|/etc|/usr|/var|/boot|~)",
        r"\brm\s+-rf\s+/\s*$",
        r"\bchmod\s+(-R\s+)?777\s+/",
        r"\bchown\s+-R\b.+\s+/",
        r">\s*/dev/sd[a-z]",
        r"\b(curl|wget)\b.+\|\s*(ba)?sh\b",
        r"\biptables\b",
        r"\bkill\s+-9\s+1\b",
        r"/proc/sys",
        r"\bmount\b",
        r"\bumount\b",
        r"\buserdel\b",
        r"\bpasswd\b",
        r"\bcrontab\b",
    ]
]


def _truncate(text: str, limit: int = MAX_CMD_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…(已截断，共 {len(text)} 字符)"


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def resolve_workspace_path(user_path: str) -> Path:
    if not user_path or not str(user_path).strip():
        raise ValueError("路径不能为空")
    raw = Path(str(user_path).strip())
    parts = list(raw.parts)
    if parts and parts[0] in (".",):
        parts = parts[1:]
    if parts and parts[0] == "workspace":
        parts = parts[1:]
        raw = Path(*parts) if parts else Path(".")
    elif not raw.is_absolute():
        raw = Path(*parts) if parts else Path(".")

    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (WORKSPACE / raw).resolve()

    if not resolved.is_relative_to(WORKSPACE):
        raise PermissionError("路径越界：只能访问项目下的 workspace/ 目录")
    return resolved


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

def _ddgs_search(query: str) -> list[dict[str, str]]:
    from ddgs import DDGS

    items: list[dict[str, str]] = []
    with DDGS() as ddgs:
        for row in ddgs.text(query, max_results=SEARCH_RESULTS) or []:
            items.append(
                {
                    "title": (row.get("title") or "").strip(),
                    "url": (row.get("href") or row.get("url") or "").strip(),
                    "snippet": (row.get("body") or row.get("snippet") or "").strip(),
                }
            )
    return items


class _DDGHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        cls = ad.get("class") or ""
        if tag == "a" and "result__a" in cls:
            self._in_title = True
            self._title_parts = []
            href = ad.get("href") or ""
            self._href = _unwrap_ddg_href(href)
        elif tag in ("a", "td") and "result__snippet" in cls:
            self._in_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
            title = "".join(self._title_parts).strip()
            if title and self._href:
                self.results.append(
                    {"title": title, "url": self._href, "snippet": ""}
                )
        if tag in ("a", "td") and self._in_snippet:
            self._in_snippet = False
            snippet = "".join(self._snippet_parts).strip()
            if self.results and not self.results[-1]["snippet"]:
                self.results[-1]["snippet"] = snippet

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_snippet:
            self._snippet_parts.append(data)


def _unwrap_ddg_href(href: str) -> str:
    if not href:
        return href
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        if "uddg" in qs:
            return unquote(qs["uddg"][0])
    except Exception:
        pass
    return href


def _html_search(query: str) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
    }
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resp = client.post(url, data={"q": query, "b": ""})
        resp.raise_for_status()
        parser = _DDGHTMLParser()
        parser.feed(resp.text)
    return parser.results[:SEARCH_RESULTS]


def web_search(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)
    last_err = None
    results: list[dict[str, str]] = []
    for fn in (_ddgs_search, _html_search):
        try:
            results = fn(query)
            if results:
                break
        except Exception as exc:  # noqa: BLE001 — surface to the model
            last_err = f"{type(exc).__name__}: {exc}"
    if not results:
        payload = {"query": query, "results": [], "error": last_err or "没有搜到结果"}
        return json.dumps(payload, ensure_ascii=False)
    return json.dumps({"query": query, "results": results}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def read_file(path: str) -> str:
    try:
        target = resolve_workspace_path(path)
    except (ValueError, PermissionError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if not target.exists():
        return json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False)
    if target.is_dir():
        entries = sorted(os.listdir(target))
        return json.dumps(
            {"error": "这是目录，不是文件", "entries": entries[:50]},
            ensure_ascii=False,
        )
    try:
        size = target.stat().st_size
    except OSError as exc:
        return json.dumps({"error": f"无法读取: {exc}"}, ensure_ascii=False)
    if size > MAX_FILE_BYTES:
        return json.dumps(
            {"error": f"文件过大（{size} 字节），上限 {MAX_FILE_BYTES}"},
            ensure_ascii=False,
        )
    raw = target.read_bytes()
    if b"\x00" in raw:
        return json.dumps({"error": "拒绝读取二进制文件"}, ensure_ascii=False)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            return json.dumps(
                {"error": "无法按 UTF-8/GB18030 解码，可能是二进制文件"},
                ensure_ascii=False,
            )
    rel = target.relative_to(WORKSPACE).as_posix()
    return json.dumps({"path": rel, "content": text}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------

def _is_dangerous(command: str) -> str | None:
    for pat in _DANGEROUS:
        if pat.search(command):
            return "命令被拦截：包含危险模式（如 sudo / rm -rf / / mkfs）。这是本地开发沙箱，不是安全隔离。"
    return None


def run_command(command: str) -> str:
    command = (command or "").strip()
    if not command:
        return json.dumps({"error": "command 不能为空"}, ensure_ascii=False)
    blocked = _is_dangerous(command)
    if blocked:
        return json.dumps({"error": blocked, "blocked": True}, ensure_ascii=False)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE),
            capture_output=True,
            text=True,
            timeout=CMD_TIMEOUT_SEC,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except subprocess.TimeoutExpired:
        return json.dumps(
            {
                "error": f"命令超时（{CMD_TIMEOUT_SEC}s）",
                "command": command,
                "cwd": "workspace",
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"执行失败: {exc}"}, ensure_ascii=False)

    return json.dumps(
        {
            "command": command,
            "cwd": "workspace",
            "exit_code": proc.returncode,
            "stdout": _truncate(proc.stdout or ""),
            "stderr": _truncate(proc.stderr or ""),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible tool schema + dispatch
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "在公开互联网上搜索，返回若干条 title/url/snippet。"
                "适合查阅文档、新闻、API 用法等需要外部信息的问题。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，建议使用具体、可检索的短语",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取工作目录 workspace/ 内的文本文件。"
                "路径相对于 workspace，例如 sample.txt；"
                "也可写 workspace/sample.txt。禁止路径穿越。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的文件路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "在工作目录 workspace/ 中执行一条 shell 命令，捕获 stdout/stderr/exit code。"
                "用于列目录、创建/编辑文本文件、简单脚本等。"
                "会拦截 rm -rf /、sudo、mkfs 等危险模式。"
                "注意：这是本地开发沙箱，不是安全隔离环境。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
            },
        },
    },
]

_DISPATCH: dict[str, Callable[..., str]] = {
    "web_search": web_search,
    "read_file": read_file,
    "run_command": run_command,
}

TOOL_LABELS = {
    "web_search": "网络搜索",
    "read_file": "读取文件",
    "run_command": "执行命令",
}


def summarize_args(name: str, args: dict[str, Any]) -> str:
    if name == "web_search":
        return str(args.get("query") or "")[:120]
    if name == "read_file":
        return str(args.get("path") or "")[:120]
    if name == "run_command":
        return str(args.get("command") or "")[:120]
    return json.dumps(args, ensure_ascii=False)[:120]


def summarize_result(name: str, raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:80]
    if isinstance(data, dict) and data.get("error"):
        return f"失败：{str(data['error'])[:80]}"
    if name == "web_search":
        n = len(data.get("results") or [])
        return f"找到 {n} 条结果"
    if name == "read_file":
        content = data.get("content") or ""
        return f"已读取 {data.get('path', '')}（{len(content)} 字符）"
    if name == "run_command":
        code = data.get("exit_code")
        out = (data.get("stdout") or "").strip().splitlines()
        preview = out[0][:60] if out else ""
        tail = f"：{preview}" if preview else ""
        return f"exit {code}{tail}"
    return "完成"


def execute_tool(name: str, arguments_json: str) -> tuple[str, dict[str, Any]]:
    """Run a tool. Returns (result_json, ui_event)."""
    try:
        args = json.loads(arguments_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("参数必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as exc:
        err = json.dumps({"error": f"参数解析失败: {exc}"}, ensure_ascii=False)
        return err, {
            "name": name,
            "label": TOOL_LABELS.get(name, name),
            "args_summary": arguments_json[:120],
            "ok": False,
            "summary": f"参数错误：{exc}",
        }

    fn = _DISPATCH.get(name)
    args_summary = summarize_args(name, args)
    if fn is None:
        err = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        return err, {
            "name": name,
            "label": name,
            "args_summary": args_summary,
            "ok": False,
            "summary": f"未知工具: {name}",
        }
    try:
        result = fn(**args)
    except TypeError as exc:
        result = json.dumps({"error": f"参数不匹配: {exc}"}, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        result = json.dumps(
            {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
        )

    ok = True
    try:
        parsed = json.loads(result)
        if isinstance(parsed, dict) and parsed.get("error"):
            ok = False
    except json.JSONDecodeError:
        pass

    ui = {
        "name": name,
        "label": TOOL_LABELS.get(name, name),
        "args_summary": args_summary,
        "ok": ok,
        "summary": summarize_result(name, result),
    }
    return result, ui
