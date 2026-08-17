"""Local tools for the Grok assistant.

Sandbox root is ./workspace relative to the project.
This is a local-dev sandbox, NOT a security jail: it blocks a few
obviously dangerous command patterns and confines file reads, but
does not isolate the process, network, or host filesystem.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import ipaddress
import socket
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from app.memory import memory_read as _memory_read
from app.memory import memory_write as _memory_write

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = (PROJECT_ROOT / "workspace").resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = 120_000
MAX_WRITE_BYTES = 200_000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CMD_OUTPUT = 16_000
MAX_FETCH_BYTES = 1_000_000
MAX_FETCH_CHARS = 30_000
CMD_TIMEOUT_SEC = 15
SEARCH_RESULTS = 5
FETCH_TIMEOUT_SEC = 20
UPLOADS_DIRNAME = "uploads"
BLOB_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

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


_MEDIUM = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\brm\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\b(curl|wget)\b.+\|\s*(ba)?sh\b",
        r"\b(curl|wget)\b.+\|\s*python",
        r"\btee\b",
        r"(^|[;&|]\s*)dd\b",
    ]
]


def _outside_workspace_write(command: str) -> bool:
    """True if the command looks like it writes outside workspace files."""
    pattern = re.compile(r"(>>?|tee(?:\s+-a)?)\s+(\S+)")
    for m in pattern.finditer(command or ""):
        dest = m.group(2).strip().strip("\"'")
        if not dest:
            continue
        if dest.startswith("/dev/"):
            continue
        if dest.startswith("/") or dest.startswith("~") or dest.startswith(".."):
            return True
        try:
            raw = Path(dest)
            if raw.is_absolute():
                return True
        except Exception:
            return True
    return False


def classify_command(command: str) -> tuple[str, str]:
    """Classify a shell command: ('deny'|'approve'|'ok', reason)."""
    command = (command or "").strip()
    if not command:
        return "ok", ""
    blocked = _is_dangerous(command)
    if blocked:
        return "deny", blocked
    if _outside_workspace_write(command):
        return "approve", "命令可能写入 workspace 以外的路径，需要确认。"
    for pat in _MEDIUM:
        if pat.search(command):
            return "approve", "中风险命令（如 rm / chmod / 管道执行），需要确认后才会运行。"
    return "ok", ""


_SECRET_ENV_NAMES = frozenset(
    {
        "CCAPI_API_KEY",
        "XAI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KK_ACCESS_TOKEN",
        "ACCESS_TOKEN",
    }
)


def _subprocess_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV_NAMES}
    env["PYTHONUNBUFFERED"] = "1"
    return env


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
            env=_subprocess_env(),
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
# list_dir
# ---------------------------------------------------------------------------

def list_dir(path: str = ".") -> str:
    raw_path = "." if not str(path or "").strip() else str(path).strip()
    try:
        target = resolve_workspace_path(raw_path)
    except (ValueError, PermissionError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if not target.exists():
        return json.dumps({"error": f"目录不存在: {raw_path}"}, ensure_ascii=False)
    if not target.is_dir():
        return json.dumps({"error": "不是目录", "path": raw_path}, ensure_ascii=False)
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            item: dict[str, Any] = {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
            }
            if child.is_file():
                try:
                    item["size"] = child.stat().st_size
                except OSError:
                    pass
            entries.append(item)
            if len(entries) >= 200:
                break
    except OSError as exc:
        return json.dumps({"error": f"无法列出: {exc}"}, ensure_ascii=False)
    rel = target.relative_to(WORKSPACE).as_posix()
    return json.dumps(
        {"path": rel, "entries": entries, "count": len(entries)},
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def write_file(path: str, content: str) -> str:
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        return json.dumps(
            {
                "error": f"内容过大（{len(encoded)} 字节），上限 {MAX_WRITE_BYTES}",
            },
            ensure_ascii=False,
        )
    try:
        target = resolve_workspace_path(path)
    except (ValueError, PermissionError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    if target.exists() and target.is_dir():
        return json.dumps({"error": "目标是目录，不能写入"}, ensure_ascii=False)
    parent = target.parent.resolve()
    if not parent.is_relative_to(WORKSPACE):
        return json.dumps({"error": "路径越界：只能访问项目下的 workspace/ 目录"}, ensure_ascii=False)
    try:
        parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return json.dumps({"error": f"写入失败: {exc}"}, ensure_ascii=False)
    rel = target.relative_to(WORKSPACE).as_posix()
    return json.dumps(
        {"ok": True, "path": rel, "bytes": len(encoded)},
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# upload (workspace/uploads)
# ---------------------------------------------------------------------------

def sanitize_upload_filename(filename: str) -> str:
    """Return a bare filename, or raise ValueError on path tricks."""
    raw = str(filename or "").strip()
    if not raw:
        raise ValueError("文件名不能为空")
    if "\x00" in raw:
        raise ValueError("非法文件名")
    normalized = raw.replace("\\", "/")
    if "/" in normalized or normalized in (".", ".."):
        raise ValueError("非法文件名：禁止路径穿越")
    name = Path(raw).name
    if name != raw or name in (".", "..") or not name:
        raise ValueError("非法文件名：禁止路径穿越")
    if len(name) > 200:
        raise ValueError("文件名过长")
    return name


def _unique_upload_path(directory: Path, name: str) -> Path:
    stem = Path(name).stem
    suffix = Path(name).suffix
    candidate = directory / name
    n = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{n}{suffix}"
        n += 1
        if n > 10_000:
            raise OSError("无法分配唯一文件名")
    return candidate


def save_upload(filename: str, content: bytes, owner: str = "") -> str:
    """Save an uploaded file into workspace/uploads/. Returns JSON."""
    if content is None:
        content = b""
    if not isinstance(content, (bytes, bytearray)):
        return json.dumps({"error": "内容必须是字节"}, ensure_ascii=False)
    content = bytes(content)
    if len(content) > MAX_UPLOAD_BYTES:
        return json.dumps(
            {
                "error": (
                    f"文件过大（{len(content)} 字节），上限 {MAX_UPLOAD_BYTES}"
                )
            },
            ensure_ascii=False,
        )
    try:
        name = sanitize_upload_filename(filename)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    ext = Path(name).suffix.lower()
    if b"\x00" in content and ext not in BLOB_EXTENSIONS:
        return json.dumps(
            {"error": "拒绝保存二进制文件（允许 pdf/png/jpg）"},
            ensure_ascii=False,
        )

    workspace = WORKSPACE.resolve()
    dest_dir = (workspace / UPLOADS_DIRNAME).resolve()
    owner = (owner or "").strip()
    if owner:
        if not re.fullmatch(r"[0-9a-f]{32}", owner):
            return json.dumps({"error": "非法访客标识"}, ensure_ascii=False)
        dest_dir = (dest_dir / owner).resolve()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return json.dumps({"error": f"无法创建上传目录: {exc}"}, ensure_ascii=False)
    uploads_root = (workspace / UPLOADS_DIRNAME).resolve()
    if not dest_dir.is_relative_to(uploads_root) or not dest_dir.is_relative_to(workspace):
        return json.dumps(
            {"error": "路径越界：只能写入 workspace/uploads/"},
            ensure_ascii=False,
        )

    try:
        target = _unique_upload_path(dest_dir, name).resolve()
    except OSError as exc:
        return json.dumps({"error": f"保存失败: {exc}"}, ensure_ascii=False)
    if not target.is_relative_to(dest_dir):
        return json.dumps(
            {"error": "路径越界：只能写入 workspace/uploads/"},
            ensure_ascii=False,
        )
    try:
        target.write_bytes(content)
    except OSError as exc:
        return json.dumps({"error": f"写入失败: {exc}"}, ensure_ascii=False)
    rel = target.relative_to(workspace).as_posix()
    return json.dumps(
        {
            "ok": True,
            "path": rel,
            "filename": target.name,
            "bytes": len(content),
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# fetch_url
# ---------------------------------------------------------------------------

_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "host.docker.internal",
}


def _ip_is_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _host_is_forbidden(host: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        return True
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return _ip_is_forbidden(ip)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    if not infos:
        return True
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, TypeError):
            return True
        if _ip_is_forbidden(ip):
            return True
    return False


def validate_public_http_url(url: str, *, allow_private: bool = False) -> str | None:
    """Return an error string if the URL is blocked, else None."""
    url = (url or "").strip()
    if not url:
        return "url 不能为空"
    try:
        parsed = urlparse(url)
    except Exception:
        return "无效的 URL"
    if parsed.scheme not in ("http", "https"):
        return "只允许 http(s) URL"
    if parsed.username or parsed.password:
        return "不允许带用户名/密码的 URL"
    host = parsed.hostname
    if not host:
        return "URL 缺少主机名"
    if not allow_private and _host_is_forbidden(host):
        return "拒绝访问本地或私有地址"
    return None


def _validate_public_http_url(url: str) -> str | None:
    return validate_public_http_url(url, allow_private=False)


class _HTMLTextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "template"}
    _BLOCK = {
        "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "blockquote", "pre", "ul", "ol", "table",
        "header", "footer", "nav",
    }

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if data:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def _extract_text(body: bytes, content_type: str) -> str:
    ctype = (content_type or "").lower()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    if "html" in ctype or text.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(text)
            parser.close()
            extracted = parser.text()
            if extracted:
                return extracted
        except Exception:
            pass
    return text


def fetch_url(url: str) -> str:
    url = (url or "").strip()
    err = _validate_public_http_url(url)
    if err:
        return json.dumps({"error": err, "url": url}, ensure_ascii=False)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; GrokAssistant/0.2; +https://x.ai) "
            "AppleWebKit/537.36 Chrome/128.0.0.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.1",
    }
    current = url
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT_SEC,
            follow_redirects=False,
            headers=headers,
        ) as client:
            for _hop in range(6):
                err = _validate_public_http_url(current)
                if err:
                    return json.dumps({"error": err, "url": current}, ensure_ascii=False)
                resp = client.get(current)
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        return json.dumps(
                            {"error": "重定向缺少 Location", "url": current},
                            ensure_ascii=False,
                        )
                    current = urljoin(current, loc)
                    continue
                if resp.status_code >= 400:
                    return json.dumps(
                        {
                            "error": f"HTTP {resp.status_code}",
                            "url": str(resp.url),
                        },
                        ensure_ascii=False,
                    )
                raw = resp.content[:MAX_FETCH_BYTES]
                if b"\x00" in raw[:1024] and "html" not in (resp.headers.get("content-type") or "").lower():
                    return json.dumps(
                        {"error": "拒绝读取二进制内容", "url": str(resp.url)},
                        ensure_ascii=False,
                    )
                extracted = _extract_text(raw, resp.headers.get("content-type") or "")
                truncated = len(extracted) > MAX_FETCH_CHARS
                if truncated:
                    extracted = extracted[:MAX_FETCH_CHARS]
                payload = {
                    "url": str(resp.url),
                    "status": resp.status_code,
                    "content_type": resp.headers.get("content-type") or "",
                    "text": extracted,
                }
                if truncated:
                    payload["truncated"] = True
                    payload["note"] = f"正文已截断至 {MAX_FETCH_CHARS} 字符"
                return json.dumps(payload, ensure_ascii=False)
            return json.dumps({"error": "重定向过多", "url": current}, ensure_ascii=False)
    except httpx.TimeoutException:
        return json.dumps(
            {"error": f"请求超时（{FETCH_TIMEOUT_SEC}s）", "url": url},
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(exc).__name__}: {exc}", "url": url},
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# memory wrappers (persist to data/memory.json)
# ---------------------------------------------------------------------------

def memory_write(fact: str) -> str:
    return _memory_write(fact)


def memory_read(query: str = "") -> str:
    return _memory_read(query or "")


# ---------------------------------------------------------------------------
# vision helper
# ---------------------------------------------------------------------------

def file_to_data_url(rel_path: str) -> str | None:
    """Encode a workspace image as a data URL for the vision API."""
    try:
        target = resolve_workspace_path(rel_path)
    except (ValueError, PermissionError):
        return None
    if not target.is_file():
        return None
    mime = IMAGE_EXTS.get(target.suffix.lower())
    if not mime:
        return None
    try:
        raw = target.read_bytes()
    except OSError:
        return None
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        return None
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


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
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "列出工作目录 workspace/ 下的文件和子目录。"
                "路径相对于 workspace，默认当前目录 ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的目录路径，默认 .",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "在 workspace/ 内创建或覆盖文本文件。"
                "路径相对于 workspace；禁止路径穿越。"
                "单次写入上限约 200KB。写文件请优先用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 workspace 的文件路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文本内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "GET 抓取一个公开 http(s) 网页并返回提取后的正文。"
                "会去掉 script/style；正文约 30k 字符封顶。"
                "禁止 localhost、内网、file:// 等非公开地址。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的 http(s) URL",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_write",
            "description": (
                "把用户偏好、姓名、长期事实写入持久记忆，跨会话保留。"
                "例如：用户名字、语言偏好、常用项目路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "要记住的一条简短事实",
                    }
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_read",
            "description": (
                "读取已保存的记忆。query 为空返回最近若干条；"
                "有 query 则按关键词过滤。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "可选过滤关键词，为空则返回最近记忆",
                    }
                },
                "required": [],
            },
        },
    },
]


_EXTRA_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "browser_open",
            "description": (
                "用内置 Chromium 打开一个公开 http(s) 网页。"
                "默认拒绝 localhost / 内网 / file://。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的 http(s) URL"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "读取当前页面的可交互元素快照，带 ref（如 e1）供 click/type 使用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "点击页面元素。优先用 snapshot 返回的 ref（如 e1），也可以是 CSS 选择器。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref_or_selector": {
                        "type": "string",
                        "description": "snapshot 的 ref 或 CSS 选择器",
                    }
                },
                "required": ["ref_or_selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "向输入框输入文本。ref 来自 browser_snapshot。",
            "parameters": {
                "type": "object",
                "properties": {
                    "ref_or_selector": {
                        "type": "string",
                        "description": "snapshot 的 ref 或 CSS 选择器",
                    },
                    "text": {"type": "string", "description": "要输入的文本"},
                },
                "required": ["ref_or_selector", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "对当前页面截图，保存到 workspace/screenshots/。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "根据文字提示生成一张图片（中转站走其 /images/generations，否则走 xAI），"
                "保存到 workspace/generated/ 并返回可在对话中展示的路径。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "图像描述（英文或中文）"}
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "把一个子任务交给子助手独立完成（最多 5 轮工具）。"
                "适合可以并行或拆开的搜索/整理任务。子助手不能再委派。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "子助手要完成的目标"},
                    "context": {
                        "type": "string",
                        "description": "可选背景信息",
                    },
                },
                "required": ["goal"],
            },
        },
    },
]

_DISPATCH: dict[str, Callable[..., str]] = {
    "web_search": web_search,
    "read_file": read_file,
    "run_command": run_command,
    "list_dir": list_dir,
    "write_file": write_file,
    "fetch_url": fetch_url,
    "memory_write": memory_write,
    "memory_read": memory_read,
}

TOOL_LABELS = {
    "web_search": "网络搜索",
    "read_file": "读取文件",
    "run_command": "执行命令",
    "list_dir": "列出目录",
    "write_file": "写入文件",
    "fetch_url": "抓取网页",
    "memory_write": "写入记忆",
    "memory_read": "读取记忆",
    "browser_open": "浏览器 · 打开",
    "browser_snapshot": "浏览器 · 快照",
    "browser_click": "浏览器 · 点击",
    "browser_type": "浏览器 · 输入",
    "browser_screenshot": "浏览器 · 截图",
    "generate_image": "生成图片",
    "delegate_task": "子助手",
}


def _lazy_dispatch(name: str) -> Callable[..., str] | None:
    if name in _DISPATCH:
        return _DISPATCH[name]
    if name == "generate_image":
        from app.images import generate_image as _gi

        return _gi
    if name.startswith("browser_"):
        from app import browser as _br

        return {
            "browser_open": _br.browser_open,
            "browser_snapshot": _br.browser_snapshot,
            "browser_click": _br.browser_click,
            "browser_type": _br.browser_type,
            "browser_screenshot": _br.browser_screenshot,
        }.get(name)
    if name.startswith("mcp_"):
        from app.mcp_client import mcp_call

        def _mcp(**kwargs: Any) -> str:
            return mcp_call(name, kwargs)

        return _mcp
    return None


PUBLIC_TOOLS = frozenset({"web_search", "fetch_url", "generate_image"})


def get_tool_schemas(*, allow_delegate: bool = True, public: bool | None = None) -> list[dict[str, Any]]:
    """All model-facing tools, including MCP (loaded from data/mcp.json)."""
    from app.settings import is_public_mode

    locked = is_public_mode() if public is None else bool(public)
    if locked:
        allowed: list[dict[str, Any]] = []
        for schema in list(TOOLS) + list(_EXTRA_SCHEMAS):
            if schema["function"]["name"] in PUBLIC_TOOLS:
                allowed.append(schema)
        return allowed
    tools = list(TOOLS)
    for schema in _EXTRA_SCHEMAS:
        fname = schema["function"]["name"]
        if fname == "delegate_task" and not allow_delegate:
            continue
        tools.append(schema)
    try:
        from app.mcp_client import mcp_tool_schemas

        tools.extend(mcp_tool_schemas())
    except Exception:
        pass
    return tools


def tool_label(name: str) -> str:
    if name in TOOL_LABELS:
        return TOOL_LABELS[name]
    if name.startswith("mcp_"):
        rest = name[4:]
        return "MCP · " + rest.replace("_", " / ", 1)
    return name


def summarize_args(name: str, args: dict[str, Any]) -> str:
    if name == "web_search":
        return str(args.get("query") or "")[:120]
    if name in ("read_file", "write_file", "list_dir"):
        return str(args.get("path") or "")[:120]
    if name == "run_command":
        return str(args.get("command") or "")[:120]
    if name in ("fetch_url", "browser_open"):
        return str(args.get("url") or "")[:120]
    if name in ("browser_click", "browser_type"):
        return str(args.get("ref_or_selector") or "")[:120]
    if name == "generate_image":
        return str(args.get("prompt") or "")[:120]
    if name == "delegate_task":
        return str(args.get("goal") or "")[:120]
    if name == "memory_write":
        return str(args.get("fact") or "")[:120]
    if name == "memory_read":
        return str(args.get("query") or "最近记忆")[:120]
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
    if name == "list_dir":
        n = data.get("count")
        if n is None:
            n = len(data.get("entries") or [])
        return f"列出 {n} 项（{data.get('path', '')}）"
    if name == "write_file":
        return f"已写入 {data.get('path', '')}（{data.get('bytes', 0)} 字节）"
    if name == "fetch_url":
        text = data.get("text") or ""
        return f"已抓取 {data.get('url', '')}（{len(text)} 字符）"
    if name == "memory_write":
        return f"已记住（共 {data.get('count', '?')} 条）"
    if name == "memory_read":
        n = len(data.get("facts") or [])
        return f"返回 {n} 条记忆"
    if name == "browser_open":
        return f"已打开 {data.get('title') or data.get('url') or ''}"
    if name == "browser_snapshot":
        n = len(data.get("items") or [])
        return f"快照 {n} 个元素"
    if name == "browser_click":
        return f"已点击 {data.get('selector', '')}"
    if name == "browser_type":
        return "已输入文本"
    if name in ("browser_screenshot", "generate_image"):
        return f"已保存 {data.get('path', '')}"
    if name == "delegate_task":
        return (data.get("reply") or data.get("summary") or "子助手已完成")[:80]
    if name.startswith("mcp_"):
        return "MCP 调用完成"
    return "完成"


def _media_from_result(name: str, raw: str) -> str | None:
    if name not in ("generate_image", "browser_screenshot"):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("url"):
        return str(data["url"])
    return None


def execute_tool(name: str, arguments_json: str) -> tuple[str, dict[str, Any]]:
    """Run a tool. Returns (result_json, ui_event)."""
    from app.settings import is_public_mode

    if is_public_mode() and name not in PUBLIC_TOOLS:
        err = json.dumps(
            {"error": "对外模式已禁用该工具", "ok": False},
            ensure_ascii=False,
        )
        return err, {
            "name": name,
            "label": tool_label(name),
            "args_summary": (arguments_json or "")[:120],
            "ok": False,
            "summary": "对外模式已禁用该工具",
        }
    try:
        args = json.loads(arguments_json or "{}")
        if not isinstance(args, dict):
            raise ValueError("参数必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as exc:
        err = json.dumps({"error": f"参数解析失败: {exc}"}, ensure_ascii=False)
        return err, {
            "name": name,
            "label": tool_label(name),
            "args_summary": arguments_json[:120],
            "ok": False,
            "summary": f"参数错误：{exc}",
        }

    fn = _lazy_dispatch(name)
    args_summary = summarize_args(name, args)
    if fn is None:
        err = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
        return err, {
            "name": name,
            "label": tool_label(name),
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
        "label": tool_label(name),
        "args_summary": args_summary,
        "ok": ok,
        "summary": summarize_result(name, result),
    }
    media = _media_from_result(name, result)
    if media:
        ui["image_url"] = media
    return result, ui
