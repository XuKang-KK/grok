"""Optional access token middleware and same-origin / extra-origin CORS."""

from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.settings import get_access_token, get_cors_origins, get_host, is_public_mode

logger = logging.getLogger("kk")

COOKIE_NAME = "kk_token"

LAN_WARNING = (
    "警告：HOST 为 0.0.0.0（或 ::），服务将暴露到局域网，但未设置访问口令。"
    "任何能访问该端口的人都可以调用 API、读写 workspace。"
    "请在 .env 中设置 KK_ACCESS_TOKEN，或在网页设置里填写「访问口令」。"
)

PUBLIC_NO_ADMIN_WARNING = (
    "警告：对外模式（KK_PUBLIC）已开启，但未设置管理员口令。"
    "访客可以聊天，但无人能改密钥或管理例程。请设置 KK_ACCESS_TOKEN。"
)

# GET /api/health is always public. Login must be reachable to set the cookie.
# GET /api/ready is a probe (no secrets) used by desktop / docs.
_PUBLIC_API = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("POST", "/api/login"),
}

# Visitor-reachable prefixes when KK_PUBLIC is on (chat stays open).
_VISITOR_EXACT = {
    ("GET", "/api/settings"),
    ("PUT", "/api/settings"),
    ("GET", "/api/models"),
    ("PUT", "/api/model"),
    ("GET", "/api/history"),
    ("GET", "/api/sessions"),
    ("POST", "/api/sessions"),
    ("POST", "/api/clear"),
    ("POST", "/api/chat"),
    ("POST", "/api/chat/stream"),
    ("POST", "/api/upload"),
}

_VISITOR_PREFIXES = (
    "/api/sessions/",
    "/api/media/",
    "/api/chat",
    "/api/upload",
)

_ADMIN_PREFIXES = (
    "/api/mcp",
    "/api/routines",
    "/api/approvals",
    "/api/bans",
)


def token_cookie_digest(raw: str) -> str:
    """Store a hash of the admin token in the login cookie, not the raw password."""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def tokens_match(provided: str, expected: str) -> bool:
    """Compare a raw secret to the expected password. Digest is not accepted."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode("utf-8")).digest(),
        hashlib.sha256(expected.encode("utf-8")).digest(),
    )


def cookie_digest_ok(provided: str, expected: str) -> bool:
    """Cookie value must be token_cookie_digest(expected), not the raw password."""
    if not provided or not expected:
        return False
    hashed = token_cookie_digest(expected)
    if len(provided) != len(hashed):
        return False
    return hmac.compare_digest(provided, hashed)


def credentials_ok(request, expected: str) -> bool:
    """Bearer matches the raw password only; cookie kk_token matches the digest only."""
    if not expected:
        return False
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return tokens_match(auth[7:].strip(), expected)
    cookie = (request.cookies.get(COOKIE_NAME) or "").strip()
    return cookie_digest_ok(cookie, expected)


def extract_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.cookies.get(COOKIE_NAME) or "").strip()


def is_admin(request: Request) -> bool:
    expected = get_access_token()
    if not expected:
        return False
    return credentials_ok(request, expected)


def is_public_api(method: str, path: str) -> bool:
    return (method.upper(), path) in _PUBLIC_API


def _is_admin_path(path: str) -> bool:
    for prefix in _ADMIN_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _is_visitor_path(method: str, path: str) -> bool:
    if is_public_api(method, path):
        return True
    if (method.upper(), path) in _VISITOR_EXACT:
        return True
    if path.startswith("/api/sessions/") or path.startswith("/api/media/"):
        return True
    return False


def _admin_denied(expected: str) -> JSONResponse:
    if not expected:
        return JSONResponse({"detail": "需要管理员口令"}, status_code=403)
    return JSONResponse({"detail": "需要访问口令"}, status_code=401)


class AccessTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()
        if method == "OPTIONS" or not path.startswith("/api/"):
            return await call_next(request)

        expected = get_access_token()

        if is_public_mode():
            if _is_admin_path(path):
                if not expected or not credentials_ok(request, expected):
                    return _admin_denied(expected)
                return await call_next(request)
            if _is_visitor_path(method, path) or is_public_api(method, path):
                return await call_next(request)
            # Unknown /api/* in public mode: require admin
            if not expected or not credentials_ok(request, expected):
                return _admin_denied(expected)
            return await call_next(request)

        if not expected or is_public_api(method, path):
            return await call_next(request)
        if credentials_ok(request, expected):
            return await call_next(request)
        return JSONResponse({"detail": "需要访问口令"}, status_code=401)


def origin_allowed(origin: str, request: Request) -> bool:
    if not origin:
        return False
    if origin in get_cors_origins():
        return True
    try:
        parsed = urlparse(origin)
        req_host = (request.headers.get("host") or "").strip()
        if parsed.netloc and req_host and parsed.netloc.lower() == req_host.lower():
            return True
    except Exception:
        return False
    return False


class SameOriginCORSMiddleware(BaseHTTPMiddleware):
    """Allow same-origin always; add KK_CORS_ORIGINS. Credentials on."""

    async def dispatch(self, request: Request, call_next):
        origin = (request.headers.get("origin") or "").strip()
        allowed = bool(origin) and origin_allowed(origin, request)
        if request.method.upper() == "OPTIONS" and origin and allowed:
            req_headers = (
                request.headers.get("access-control-request-headers")
                or "Authorization, Content-Type"
            )
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD",
                    "Access-Control-Allow-Headers": req_headers,
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
        response = await call_next(request)
        if origin and allowed:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response


def warn_if_lan_unprotected(host: str | None = None) -> None:
    if is_public_mode() and not get_access_token():
        logger.warning(PUBLIC_NO_ADMIN_WARNING)
        print(PUBLIC_NO_ADMIN_WARNING, flush=True)
    h = (host if host is not None else get_host()).strip()
    if h in {"0.0.0.0", "::", "[::]"} and not get_access_token():
        if is_public_mode():
            return
        logger.warning(LAN_WARNING)
        print(LAN_WARNING, flush=True)
