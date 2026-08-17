"""Multi-provider catalog and message/tool conversions.

CCAPI / xAI / OpenAI use OpenAI-compatible Chat Completions.
Anthropic uses the Messages API (not OpenAI-compatible).
"""

from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_PROVIDER = "ccapi"
DEFAULT_IMAGE_MODEL = "grok-imagine-image-2.0"

XAI_BASE_URL = "https://api.x.ai/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Official CCAPI host (api.ccapi.us does not resolve on some networks).
CCAPI_BASE_URL = "https://api.ccapi.ai/v1"
CCAPI_BASE_URL_US = "https://api.ccapi.us/v1"
CCAPI_BASE_PRESETS = (CCAPI_BASE_URL, CCAPI_BASE_URL_US)
CCAPI_PRICING_URL = "https://ccapi.us/pricing/"

PROVIDER_IDS = ("ccapi", "xai", "openai", "anthropic")

PROVIDERS: dict[str, dict[str, Any]] = {
    "ccapi": {
        "id": "ccapi",
        "name": "中转站",
        "base_url": CCAPI_BASE_URL,
        "default_model": "gpt-5.6",
        "models": [
            "gpt-5.6",
            "gpt-5",
            "claude-sonnet-5",
            "claude-opus-5",
            "grok-4.6",
            "deepseek-v3.2",
            "gemini-2.5-flash",
        ],
        "key_field": "ccapi_api_key",
        "env_key": "CCAPI_API_KEY",
        "compat": "openai",
        "configurable_base": True,
    },
    "xai": {
        "id": "xai",
        "name": "xAI",
        "base_url": XAI_BASE_URL,
        "default_model": "grok-4.6",
        "models": ["grok-4.6", "grok-4.5"],
        "key_field": "xai_api_key",
        "env_key": "XAI_API_KEY",
        "compat": "openai",
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "base_url": OPENAI_BASE_URL,
        "default_model": "gpt-5.6",
        "models": ["gpt-5.6", "gpt-5", "gpt-5-mini", "gpt-5-chat-latest"],
        "key_field": "openai_api_key",
        "env_key": "OPENAI_API_KEY",
        "compat": "openai",
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "base_url": ANTHROPIC_BASE_URL,
        "default_model": "claude-sonnet-5",
        "models": [
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-fable-5",
            "claude-haiku-4-5",
        ],
        "key_field": "anthropic_api_key",
        "env_key": "ANTHROPIC_API_KEY",
        "compat": "anthropic",
    },
}

_DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpeg|jpg|gif|webp));base64,(.+)$",
    re.IGNORECASE | re.DOTALL,
)


class VisionUnsupported(ValueError):
    """Raised when a message contains images that cannot be sent to Anthropic."""


def normalize_provider(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in PROVIDERS:
        return raw
    aliases = {
        "x.ai": "xai",
        "grok": "xai",
        "gpt": "openai",
        "claude": "anthropic",
        "relay": "ccapi",
        "zhongzhuan": "ccapi",
        "中转站": "ccapi",
        "ccapi.us": "ccapi",
        "ccapi.ai": "ccapi",
    }
    if raw in aliases:
        return aliases[raw]
    return DEFAULT_PROVIDER


def provider_meta(provider: str | None) -> dict[str, Any]:
    return PROVIDERS[normalize_provider(provider)]


def default_model_for(provider: str | None) -> str:
    return str(provider_meta(provider)["default_model"])


def preset_models(provider: str | None) -> list[str]:
    return list(provider_meta(provider)["models"])


def missing_key_message(provider: str | None) -> str:
    meta = provider_meta(provider)
    name = meta["name"]
    env_key = meta["env_key"]
    return (
        f"未配置 {name} 的 API 密钥（{env_key}）。"
        f"请打开设置面板填写 {name} 密钥，或写入 data/settings.json / .env。"
    )


def catalog_payload(
    *,
    has_api_key: dict[str, bool],
    provider: str,
    model: str,
) -> dict[str, Any]:
    rows = []
    for pid in PROVIDER_IDS:
        meta = PROVIDERS[pid]
        rows.append(
            {
                "id": pid,
                "name": meta["name"],
                "models": list(meta["models"]),
                "default_model": meta["default_model"],
                "has_key": bool(has_api_key.get(pid)),
                "compat": meta["compat"],
                "base_url": meta["base_url"],
                "configurable_base": bool(meta.get("configurable_base")),
            }
        )
    return {
        "ok": True,
        "providers": rows,
        "provider": normalize_provider(provider),
        "model": model,
        "has_api_key": {pid: bool(has_api_key.get(pid)) for pid in PROVIDER_IDS},
        "ccapi_base_url": CCAPI_BASE_URL,
        "ccapi_base_presets": list(CCAPI_BASE_PRESETS),
    }


def normalize_ccapi_base_url(value: str | None) -> str:
    """Accept a host or full URL; default to the official api.ccapi.ai /v1."""
    raw = (value or "").strip()
    if not raw:
        return CCAPI_BASE_URL
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    raw = raw.rstrip("/")
    if not re.search(r"/v\d+[a-z]*$", raw, re.IGNORECASE):
        raw = raw + "/v1"
    return raw


def parse_openai_model_ids(data: Any) -> list[str]:
    if isinstance(data, dict):
        items = data.get("data")
        if items is None:
            items = data.get("models")
    else:
        items = data
    if not isinstance(items, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        mid = ""
        if isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
        elif isinstance(item, str):
            mid = item.strip()
        if mid and mid not in seen:
            out.append(mid)
            seen.add(mid)
    return out


def merge_model_ids(presets: list[str], extra: list[str] | None) -> list[str]:
    merged = list(presets)
    seen = set(presets)
    for mid in extra or []:
        name = str(mid or "").strip()
        if name and name not in seen:
            merged.append(name)
            seen.add(name)
    return merged


def fetch_ccapi_models(base_url: str, api_key: str, *, timeout: float = 8.0) -> list[str]:
    """GET {base}/models. Never raises; returns [] on any failure. No live calls in tests."""
    if not base_url or not api_key:
        return []
    url = base_url.rstrip("/") + "/models"
    try:
        import httpx

        headers = {"Authorization": f"Bearer {api_key}"}
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except Exception:
        return []
    return parse_openai_model_ids(payload)



def openai_tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for item in tools:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict):
            if isinstance(item, dict) and item.get("name"):
                fn = item
            else:
                continue
        params = fn.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        converted.append(
            {
                "name": str(fn.get("name") or ""),
                "description": str(fn.get("description") or ""),
                "input_schema": params,
            }
        )
    return [t for t in converted if t["name"]]


def data_url_to_anthropic_image(url: str) -> dict[str, Any] | None:
    raw = (url or "").strip()
    match = _DATA_URL_RE.match(raw)
    if not match:
        return None
    media = match.group(1).lower()
    if media == "image/jpg":
        media = "image/jpeg"
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media,
            "data": match.group(2),
        },
    }


def _as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
            elif item:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def convert_user_content_for_anthropic(
    content: Any,
    *,
    allow_vision: bool = True,
) -> list[dict[str, Any]] | str:
    """Convert OpenAI user content (string or parts) to Anthropic blocks."""
    if isinstance(content, str) or content is None:
        return content or ""
    if not isinstance(content, list):
        return str(content)

    blocks: list[dict[str, Any]] = []
    saw_image = False
    for part in content:
        if not isinstance(part, dict):
            if part:
                blocks.append({"type": "text", "text": str(part)})
            continue
        ptype = part.get("type")
        if ptype == "text" or (ptype is None and "text" in part):
            text = str(part.get("text") or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        if ptype == "image_url" or "image_url" in part:
            saw_image = True
            if not allow_vision:
                raise VisionUnsupported(
                    "当前 Anthropic 模型无法处理这张图片，请改用 xAI / OpenAI，或发送纯文本。"
                )
            url = ""
            image = part.get("image_url")
            if isinstance(image, dict):
                url = str(image.get("url") or "")
            elif isinstance(image, str):
                url = image
            converted = data_url_to_anthropic_image(url)
            if converted is None:
                raise VisionUnsupported(
                    "Anthropic 仅支持 data URL 图片。请改用 xAI / OpenAI，或发送纯文本。"
                )
            blocks.append(converted)
            continue
        if ptype == "image":
            blocks.append(part)
    if not blocks:
        return ""
    if len(blocks) == 1 and blocks[0].get("type") == "text" and not saw_image:
        return str(blocks[0].get("text") or "")
    return blocks


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _merge_content(existing: Any, incoming: Any) -> Any:
    if existing in (None, "", []):
        return incoming
    if incoming in (None, "", []):
        return existing
    if isinstance(existing, str) and isinstance(incoming, str):
        return existing + "\n" + incoming
    left = existing if isinstance(existing, list) else [{"type": "text", "text": str(existing)}]
    right = incoming if isinstance(incoming, list) else [{"type": "text", "text": str(incoming)}]
    return list(left) + list(right)


def openai_history_to_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split OpenAI-style history into Anthropic system + messages."""
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending_tools: list[dict[str, Any]] = []

    def flush_tools() -> None:
        nonlocal pending_tools
        if not pending_tools:
            return
        if out and out[-1].get("role") == "user":
            prev = out[-1]["content"]
            if isinstance(prev, str):
                blocks: list[dict[str, Any]] = [{"type": "text", "text": prev}] if prev else []
            elif isinstance(prev, list):
                blocks = list(prev)
            else:
                blocks = [{"type": "text", "text": str(prev)}]
            blocks.extend(pending_tools)
            out[-1]["content"] = blocks
        else:
            out.append({"role": "user", "content": list(pending_tools)})
        pending_tools = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            text = _as_text(msg.get("content"))
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            pending_tools.append(
                {
                    "type": "tool_result",
                    "tool_use_id": str(msg.get("tool_call_id") or ""),
                    "content": _as_text(msg.get("content")),
                }
            )
            continue
        if role == "user":
            flush_tools()
            content = convert_user_content_for_anthropic(msg.get("content"))
            if out and out[-1].get("role") == "user":
                out[-1]["content"] = _merge_content(out[-1].get("content"), content)
            else:
                out.append({"role": "user", "content": content})
            continue
        if role == "assistant":
            flush_tools()
            blocks: list[dict[str, Any]] = []
            text = _as_text(msg.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                name = str((fn or {}).get("name") or tc.get("name") or "")
                args = _parse_tool_args((fn or {}).get("arguments") or tc.get("input"))
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(tc.get("id") or ""),
                        "name": name,
                        "input": args,
                    }
                )
            if not blocks:
                continue
            if out and out[-1].get("role") == "assistant":
                prev = out[-1]["content"]
                if isinstance(prev, list):
                    prev.extend(blocks)
                else:
                    out[-1]["content"] = (
                        [{"type": "text", "text": str(prev)}] if prev else []
                    ) + blocks
            else:
                out.append({"role": "assistant", "content": blocks})

    flush_tools()
    if out and out[0].get("role") != "user":
        out.insert(0, {"role": "user", "content": "（继续）"})
    return "\n\n".join(system_parts), out


def anthropic_text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)
