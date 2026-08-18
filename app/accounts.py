"""Visitor accounts: users.json, scrypt passwords, signed kk_user cookies."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import Response

from app.publicmode import cookie_should_be_secure
from app.settings import get_access_token, is_public_mode

USERS_NAME = "users.json"
SESSION_SECRET_NAME = "session_secret.txt"
USER_COOKIE = "kk_user"
USER_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_USER_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# scrypt params (stdlib)
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_lock = threading.Lock()


def _data_dir() -> Path:
    from app.settings import DATA_DIR

    return DATA_DIR


def _users_path() -> Path:
    return _data_dir() / USERS_NAME


def _session_secret_path() -> Path:
    return _data_dir() / SESSION_SECRET_NAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def allow_signup() -> bool:
    raw = os.getenv("KK_ALLOW_SIGNUP")
    if raw is None or str(raw).strip() == "":
        return True
    return _truthy(raw)


def require_account() -> bool:
    """Public mode defaults to requiring login; local mode does not."""
    raw = os.getenv("KK_REQUIRE_ACCOUNT")
    if raw is None or str(raw).strip() == "":
        return bool(is_public_mode())
    return _truthy(raw)


def normalize_username(username: str) -> str:
    return (username or "").strip()


def validate_username(username: str) -> str | None:
    raw = normalize_username(username)
    if not _USERNAME_RE.fullmatch(raw):
        return "用户名须为 3–32 位字母、数字、下划线或短横线"
    return None


def validate_password(password: str) -> str | None:
    if not isinstance(password, str) or len(password) < 8:
        return "密码至少 8 位"
    return None


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "scrypt$%d$%d$%d$%s$%s" % (
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = (stored or "").split("$", 5)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError, OSError):
        return False
    return hmac.compare_digest(digest, expected)


def _empty_store() -> dict[str, Any]:
    return {"users": {}}


def _flock_file():
    root = _data_dir()
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".users.lock"
    fh = lock_path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass
    return fh


def _load_unlocked() -> dict[str, Any]:
    path = _users_path()
    if not path.exists():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    users = raw.get("users")
    if not isinstance(users, dict):
        return _empty_store()
    return {"users": users}


def _save_unlocked(data: dict[str, Any]) -> None:
    root = _data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = _users_path()
    tmp = path.with_suffix(".json.tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def load_users() -> dict[str, Any]:
    with _lock:
        return _load_unlocked()


def find_user_by_username(username: str) -> dict[str, Any] | None:
    needle = normalize_username(username).lower()
    if not needle:
        return None
    with _lock:
        data = _load_unlocked()
        for user in data["users"].values():
            if not isinstance(user, dict):
                continue
            if str(user.get("username") or "").lower() == needle:
                return dict(user)
    return None


def get_user(user_id: str) -> dict[str, Any] | None:
    uid = (user_id or "").strip()
    if not _USER_ID_RE.fullmatch(uid):
        return None
    with _lock:
        data = _load_unlocked()
        user = data["users"].get(uid)
        if isinstance(user, dict):
            return dict(user)
    return None


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "id": str(user.get("id") or ""),
        "username": str(user.get("username") or ""),
    }


def register_user(username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    err = validate_username(username)
    if err:
        return None, err
    err = validate_password(password)
    if err:
        return None, err
    if not allow_signup():
        return None, "注册已关闭"
    name = normalize_username(username)
    with _lock:
        fh = _flock_file()
        try:
            data = _load_unlocked()
            for existing in data["users"].values():
                if not isinstance(existing, dict):
                    continue
                if str(existing.get("username") or "").lower() == name.lower():
                    return None, "用户名已被占用"
            uid = uuid.uuid4().hex
            row = {
                "id": uid,
                "username": name,
                "password_hash": hash_password(password),
                "created_at": _now_iso(),
                "disabled": False,
                "is_admin": False,
            }
            data["users"][uid] = row
            _save_unlocked(data)
            return dict(row), None
        finally:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            fh.close()


def authenticate(username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    user = find_user_by_username(username)
    if user is None:
        return None, "用户名或密码不正确"
    if user.get("disabled"):
        return None, "账号已禁用"
    if not verify_password(password or "", str(user.get("password_hash") or "")):
        return None, "用户名或密码不正确"
    return user, None


def session_signing_secret() -> str:
    dedicated = (os.getenv("KK_SESSION_SECRET") or "").strip()
    if dedicated:
        return dedicated
    secrets_key = (os.getenv("KK_SECRETS_KEY") or "").strip()
    if secrets_key:
        return secrets_key
    access = get_access_token()
    if access:
        return access
    path = _session_secret_path()
    with _lock:
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    return existing
            except OSError:
                pass
        _data_dir().mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(32)
        try:
            path.write_text(value + "\n", encoding="utf-8")
        except OSError:
            pass
        return value


def _sign(payload: str) -> str:
    dig = hmac.new(
        session_signing_secret().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return dig


def make_session_token(user_id: str, *, max_age: int = USER_COOKIE_MAX_AGE) -> str:
    expiry = int(time.time()) + int(max_age)
    payload = f"{user_id}|{expiry}"
    return f"{payload}|{_sign(payload)}"


def parse_session_token(token: str) -> str | None:
    raw = (token or "").strip()
    parts = raw.split("|")
    if len(parts) != 3:
        return None
    user_id, expiry_s, sig = parts
    if not _USER_ID_RE.fullmatch(user_id):
        return None
    try:
        expiry = int(expiry_s)
    except ValueError:
        return None
    if expiry < int(time.time()):
        return None
    payload = f"{user_id}|{expiry_s}"
    expected = _sign(payload)
    if not hmac.compare_digest(sig, expected):
        return None
    return user_id


def set_user_cookie(response: Response, request: Request, user_id: str) -> None:
    token = make_session_token(user_id)
    response.set_cookie(
        key=USER_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=cookie_should_be_secure(request),
        path="/",
        max_age=USER_COOKIE_MAX_AGE,
    )


def clear_user_cookie(response: Response) -> None:
    response.delete_cookie(USER_COOKIE, path="/")


def current_user(request: Request) -> dict[str, Any] | None:
    cached = getattr(request.state, "kk_user", None)
    if isinstance(cached, dict):
        return cached
    token = (request.cookies.get(USER_COOKIE) or "").strip()
    uid = parse_session_token(token)
    if not uid:
        request.state.kk_user = None
        return None
    user = get_user(uid)
    if user is None or user.get("disabled"):
        request.state.kk_user = None
        return None
    request.state.kk_user = user
    return user


def owner_key_for_user(user: dict[str, Any] | None) -> str | None:
    if not user:
        return None
    uid = str(user.get("id") or "").strip()
    if not _USER_ID_RE.fullmatch(uid):
        return None
    return f"user:{uid}"


def quota_key_for_request(request: Request, vid: str = "") -> str:
    user = current_user(request)
    if user:
        key = owner_key_for_user(user)
        if key:
            return key
    return (vid or "").strip()
