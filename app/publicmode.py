"""Public-internet helpers: visitor cookie, rate limit, security headers."""

from __future__ import annotations

import ipaddress
import os
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.settings import get_allowed_hosts, is_public_mode

PUBLIC_TOOLS = frozenset({"web_search", "fetch_url", "generate_image"})

VID_COOKIE = "kk_vid"
VID_RE = re.compile(r"^[0-9a-f]{32}$")
COOKIE_MAX_AGE = 60 * 60 * 24 * 30

ADMIN_SETTING_FIELDS = frozenset(
    {
        "xai_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "ccapi_api_key",
        "ccapi_base_url",
        "allow_local_browser",
        "access_token",
        "clear_access_token",
        "provider",
    }
)

_DOCS_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})

_visitor_id: ContextVar[str] = ContextVar("kk_vid", default="")

_rate_lock = threading.Lock()
_rate_hits: dict[tuple[str, str], list[float]] = {}


def cookie_should_be_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return proto == "https"


def set_current_visitor(vid: str) -> None:
    _visitor_id.set(vid or "")


def current_visitor() -> str:
    return _visitor_id.get() or ""


def get_visitor_id(request: Request) -> str:
    existing = getattr(request.state, "kk_vid", None)
    if isinstance(existing, str) and VID_RE.fullmatch(existing):
        return existing
    raw = (request.cookies.get(VID_COOKIE) or "").strip()
    if VID_RE.fullmatch(raw):
        request.state.kk_vid = raw
        request.state.kk_vid_new = False
        return raw
    vid = uuid.uuid4().hex
    request.state.kk_vid = vid
    request.state.kk_vid_new = True
    return vid


def attach_visitor_cookie(response: Response, request: Request) -> None:
    if not is_public_mode():
        return
    vid = get_visitor_id(request)
    if not getattr(request.state, "kk_vid_new", False):
        return
    response.set_cookie(
        key=VID_COOKIE,
        value=vid,
        httponly=True,
        samesite="lax",
        secure=cookie_should_be_secure(request),
        path="/",
        max_age=COOKIE_MAX_AGE,
    )
    request.state.kk_vid_new = False


def session_owner(request: Request) -> str | None:
    """Owner filter for the session store. None = local (no isolation)."""
    if not is_public_mode():
        return None
    return get_visitor_id(request)


def settings_body_requires_admin(fields: dict[str, Any]) -> bool:
    for key in ADMIN_SETTING_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "clear_access_token" and not value:
            continue
        if value is None:
            continue
        return True
    return False


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return n if n > 0 else default


def reset_rate_limits() -> None:
    with _rate_lock:
        _rate_hits.clear()


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def trusted_proxy() -> bool:
    return _env_truthy("KK_TRUSTED_PROXY")


def _looks_like_ip(value: str) -> bool:
    raw = (value or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    try:
        ipaddress.ip_address(raw)
        return True
    except ValueError:
        return False


def client_ip(request: Request) -> str:
    """If KK_TRUSTED_PROXY, use the rightmost X-Forwarded-For hop (proxy-set)."""
    if trusted_proxy():
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            parts = [part.strip() for part in xff.split(",") if part.strip()]
            if parts:
                candidate = parts[-1]
                if _looks_like_ip(candidate):
                    if candidate.startswith("[") and candidate.endswith("]"):
                        return candidate[1:-1]
                    return candidate
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _allow(bucket: str, key: str, limit: int, window: float) -> bool:
    now = time.monotonic()
    with _rate_lock:
        arr = _rate_hits.setdefault((bucket, key), [])
        arr[:] = [t for t in arr if now - t < window]
        if len(arr) >= limit:
            return False
        arr.append(now)
        return True


def _rate_limited_response(request: Request, bucket: str) -> JSONResponse:
    try:
        from app.audit import write_audit

        write_audit(
            "rate_limit",
            vid=get_visitor_id(request) if is_public_mode() else "",
            path=request.url.path,
            ip=client_ip(request),
            detail=bucket,
        )
    except Exception:
        pass
    return JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)


def check_rate_limit(request: Request) -> Response | None:
    if not is_public_mode():
        return None
    method = request.method.upper()
    path = request.url.path
    ip = client_ip(request)
    vid = get_visitor_id(request) or "unknown"
    window = float(_int_env("KK_CHAT_WINDOW_SEC", 600))
    if method == "POST" and path in {"/api/chat", "/api/chat/stream"}:
        limit = _int_env("KK_CHAT_RATE", 20)
        ip_ok = _allow("chat-ip", ip, limit, window)
        vid_ok = _allow("chat-vid", vid, limit, window)
        if not ip_ok or not vid_ok:
            return _rate_limited_response(request, "chat")
    elif method == "POST" and path == "/api/upload":
        limit = _int_env("KK_UPLOAD_RATE", 10)
        ip_ok = _allow("upload-ip", ip, limit, window)
        vid_ok = _allow("upload-vid", vid, limit, window)
        if not ip_ok or not vid_ok:
            return _rate_limited_response(request, "upload")
    elif method == "POST" and path == "/api/login":
        limit = _int_env("KK_LOGIN_RATE", 10)
        ip_ok = _allow("login-ip", ip, limit, window)
        vid_ok = _allow("login-vid", vid, limit, window)
        if not ip_ok or not vid_ok:
            return _rate_limited_response(request, "login")
    return None


CSP_HEADER = (
    "default-src 'self'; img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'"
)


def apply_security_headers(request: Request, response: Response) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = CSP_HEADER
    if cookie_should_be_secure(request):
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/media"):
        response.headers["Cache-Control"] = "no-store"


def _docs_blocked(path: str) -> bool:
    if path in _DOCS_PATHS:
        return True
    return path.startswith("/docs/") or path.startswith("/redoc/")


def _is_protected_write(method: str, path: str) -> bool:
    if method == "POST" and path in {
        "/api/chat",
        "/api/chat/stream",
        "/api/upload",
        "/api/sessions",
        "/api/clear",
    }:
        return True
    if method == "POST" and path.startswith("/api/sessions/"):
        return True
    if method == "DELETE" and path.startswith("/api/sessions/"):
        return True
    return False


def _banned_write_blocked(request: Request, vid: str) -> JSONResponse | None:
    if not vid:
        return None
    from app.audit import is_banned, write_audit

    if not is_banned(vid):
        return None
    if not _is_protected_write(request.method.upper(), request.url.path):
        return None
    write_audit(
        "ban_hit",
        vid=vid,
        path=request.url.path,
        ip=client_ip(request),
        detail="已封禁",
    )
    return JSONResponse({"detail": "已封禁"}, status_code=403)


class PublicHardeningMiddleware(BaseHTTPMiddleware):
    """Visitor cookie, rate limit, security headers, public /docs lock."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if is_public_mode() and _docs_blocked(path):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        hosts = get_allowed_hosts()
        if hosts:
            raw_host = (request.headers.get("host") or "").strip()
            host_only = raw_host.split(":")[0].strip()
            if raw_host not in hosts and host_only not in hosts:
                return JSONResponse({"detail": "Invalid host header"}, status_code=400)

        if is_public_mode():
            vid = get_visitor_id(request)
            set_current_visitor(vid)
            banned = _banned_write_blocked(request, vid)
            if banned is not None:
                apply_security_headers(request, banned)
                attach_visitor_cookie(banned, request)
                return banned
        else:
            set_current_visitor("")

        limited = check_rate_limit(request)
        if limited is not None:
            apply_security_headers(request, limited)
            return limited

        response = await call_next(request)
        apply_security_headers(request, response)
        attach_visitor_cookie(response, request)
        return response
