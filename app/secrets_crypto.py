"""Encrypt settings secrets at rest with Fernet (KK_SECRETS_KEY)."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("kk")

ENC_PREFIX = "enc:v1:"
_SALT = b"kk-ai-settings-v1"
_ITERATIONS = 200_000
_PLAINTEXT_WARNED = False


def _looks_like_fernet_key(raw: str) -> bool:
    """True if value is already a urlsafe-base64 32-byte Fernet key."""
    text = (raw or "").strip()
    if len(text) < 40 or len(text) > 64:
        return False
    try:
        decoded = base64.urlsafe_b64decode(text.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return len(decoded) == 32


def _derive_fernet_key(passphrase: str) -> bytes:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        _SALT,
        _ITERATIONS,
        dklen=32,
    )
    return base64.urlsafe_b64encode(digest)


def _raw_key() -> str:
    return (os.getenv("KK_SECRETS_KEY") or "").strip()


def get_fernet() -> Optional[Fernet]:
    raw = _raw_key()
    if not raw:
        return None
    try:
        if _looks_like_fernet_key(raw):
            return Fernet(raw.encode("ascii"))
        return Fernet(_derive_fernet_key(raw))
    except Exception:
        logger.warning("KK_SECRETS_KEY 无效，无法加密设置密钥")
        return None


def _warn_plaintext_once() -> None:
    global _PLAINTEXT_WARNED
    if _PLAINTEXT_WARNED:
        return
    try:
        from app.settings import is_public_mode
    except Exception:
        return
    if not is_public_mode():
        return
    _PLAINTEXT_WARNED = True
    logger.warning(
        "对外模式未设置 KK_SECRETS_KEY：API 密钥将以明文写入 data/settings.json"
    )


def encrypt_str(plain: str) -> str:
    """Encrypt a secret. Missing key → return plaintext (local DX)."""
    text = "" if plain is None else str(plain)
    if not text:
        return text
    if text.startswith(ENC_PREFIX):
        return text
    fernet = get_fernet()
    if fernet is None:
        _warn_plaintext_once()
        return text
    token = fernet.encrypt(text.encode("utf-8")).decode("ascii")
    return ENC_PREFIX + token


def decrypt_str(value: str) -> str:
    """Decrypt enc:v1: values; legacy plaintext returned as-is."""
    text = "" if value is None else str(value)
    if not text.startswith(ENC_PREFIX):
        return text
    payload = text[len(ENC_PREFIX) :]
    fernet = get_fernet()
    if fernet is None:
        return text
    try:
        return fernet.decrypt(payload.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        logger.warning("无法解密设置密钥（KK_SECRETS_KEY 可能已更换）")
        return text


def reset_crypto_warnings() -> None:
    """Test helper."""
    global _PLAINTEXT_WARNED
    _PLAINTEXT_WARNED = False
