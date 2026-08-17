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
        "default_model": "gpt-5.6-terra",
        "models": [
            "gpt-5-mini",
            "gpt-5.1",
            "gpt-5.2",
            "gpt-5.3-codex",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            "gpt-5.5",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "claude-haiku-4-5-20251001",
            "claude-opus-4-6",
            "claude-opus-5",
            "claude-sonnet-4-6",
            "claude-sonnet-5",
            "cursor-opus-4-8",
            "grok-4.5",
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


FAMILY_IDS = ("gpt", "claude", "grok")
DEFAULT_CHAT_MODEL = "gpt-5.6-terra"

FALLBACK_MODEL_IDS = (
    "gpt-5-mini",
    "gpt-5.1",
    "gpt-5.2",
    "gpt-5.3-codex",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.5",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-6",
    "claude-opus-4-6-high",
    "claude-opus-4-6-low",
    "claude-opus-4-6-max",
    "claude-opus-4-6-medium",
    "claude-opus-4-6-thinking",
    "claude-opus-4-7",
    "claude-opus-4-7-high",
    "claude-opus-4-7-low",
    "claude-opus-4-7-max",
    "claude-opus-4-7-medium",
    "claude-opus-4-7-thinking",
    "claude-opus-4-7-xhigh",
    "claude-opus-4-8",
    "claude-opus-4-8-high",
    "claude-opus-4-8-low",
    "claude-opus-4-8-max",
    "claude-opus-4-8-xhigh",
    "claude-opus-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "cursor-opus-4-8",
    "grok-4.5",
)

_PRICE_KEYS = frozenset({"price", "pricing", "cost", "fee"})


def family_for_model(model_id: str | None) -> str | None:
    """Map a model id to a surface family, or None if it stays off the three tabs."""
    raw = str(model_id or "").strip().lower()
    if not raw:
        return None
    leaf = raw.rsplit("/", 1)[-1]
    # Chat families only — omit Gemini / Kimi / image / video ids from the rail.
    if any(token in raw for token in ("gemini", "kimi", "image", "imagine", "video", "sora")):
        return None
    if "gpt" in raw or "chatgpt" in raw or leaf.startswith(("o1", "o3", "o4")):
        return "gpt"
    if "claude" in raw or leaf.startswith("cursor-opus") or raw.startswith("cursor-opus"):
        return "claude"
    if "grok" in raw:
        return "grok"
    return None


def normalize_family(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in FAMILY_IDS:
        return raw
    aliases = {
        "openai": "gpt",
        "chatgpt": "gpt",
        "anthropic": "claude",
        "xai": "grok",
        "x.ai": "grok",
    }
    mapped = aliases.get(raw)
    if mapped:
        return mapped
    guessed = family_for_model(raw)
    return guessed if guessed in FAMILY_IDS else "gpt"


def model_label(model_id: str | None) -> str:
    """Friendly name from an id: gpt-5.6-terra → GPT-5.6 Terra."""
    raw = str(model_id or "").strip()
    leaf = raw.rsplit("/", 1)[-1] or raw
    if not leaf:
        return raw
    low = leaf.lower()
    brand = None
    rest = leaf
    for prefix, pretty in (
        ("chatgpt", "ChatGPT"),
        ("cursor-opus", "Cursor Opus"),
        ("gpt", "GPT"),
        ("claude", "Claude"),
        ("grok", "Grok"),
    ):
        if low == prefix:
            return pretty
        if low.startswith(prefix):
            brand = pretty
            rest = leaf[len(prefix) :].lstrip("-_")
            break
    if brand is None:
        if low.startswith(("o1", "o3", "o4")):
            head = leaf.split("-", 1)[0]
            brand = head[0].upper() + head[1:]
            rest = leaf[len(head) :].lstrip("-_")
        else:
            return leaf
    if not rest:
        return brand
    chunks = [brand]
    for part in (p for p in rest.split("-") if p):
        if any(ch.isdigit() for ch in part) and not any(ch.isalpha() for ch in part):
            chunks[-1] = chunks[-1] + "-" + part
        else:
            chunks.append(part[0].upper() + part[1:])
    return " ".join(chunks)


def strip_price_fields(obj: Any) -> Any:
    """Drop price / pricing / cost / fee keys anywhere in a JSON-like tree."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            if str(key).lower() in _PRICE_KEYS:
                continue
            out[key] = strip_price_fields(value)
        return out
    if isinstance(obj, list):
        return [strip_price_fields(item) for item in obj]
    return obj


def model_entry(model_id: str) -> dict[str, str]:
    return {"id": str(model_id), "label": model_label(model_id)}


def group_models_by_family(ids: list[str] | None) -> dict[str, list[dict[str, str]]]:
    families: dict[str, list[dict[str, str]]] = {fid: [] for fid in FAMILY_IDS}
    seen: dict[str, set[str]] = {fid: set() for fid in FAMILY_IDS}
    for mid in ids or []:
        name = str(mid or "").strip()
        fam = family_for_model(name)
        if fam not in families or name in seen[fam]:
            continue
        seen[fam].add(name)
        families[fam].append(model_entry(name))
    return families


def fallback_families() -> dict[str, list[dict[str, str]]]:
    return group_models_by_family(list(FALLBACK_MODEL_IDS))


def first_model_of(
    families: dict[str, list[dict[str, str]]] | None,
    family: str | None = "gpt",
) -> str:
    fam = normalize_family(family)
    rows = (families or {}).get(fam) or []
    if fam == "gpt":
        for row in rows:
            if str(row.get("id") or "") == DEFAULT_CHAT_MODEL:
                return DEFAULT_CHAT_MODEL
    if rows:
        return str(rows[0]["id"])
    for fid in FAMILY_IDS:
        extra = (families or {}).get(fid) or []
        if extra:
            return str(extra[0]["id"])
    return DEFAULT_CHAT_MODEL


def family_catalog_payload(
    *,
    model_ids: list[str] | None,
    source: str,
    provider: str,
    model: str,
    has_relay_key: bool,
    family: str | None = None,
) -> dict[str, Any]:
    ids = list(model_ids or FALLBACK_MODEL_IDS)
    families = group_models_by_family(ids)
    mid = str(model or "").strip()
    fam = family_for_model(mid) or (normalize_family(family) if family else "gpt")
    if fam not in FAMILY_IDS:
        fam = "gpt"
    if not mid:
        mid = first_model_of(families, fam)
    payload = {
        "ok": True,
        "source": "live" if source == "live" else "fallback",
        "family": fam,
        "families": families,
        "provider": "ccapi",
        "model": mid,
        "has_relay_key": bool(has_relay_key),
    }
    return strip_price_fields(payload)


def catalog_payload(
    *,
    has_api_key: dict[str, bool],
    provider: str,
    model: str,
    model_ids: list[str] | None = None,
    source: str = "fallback",
    family: str | None = None,
) -> dict[str, Any]:
    """Consumer catalog: GPT / Claude / Grok families. No prices or base URLs."""
    return family_catalog_payload(
        model_ids=model_ids,
        source=source,
        provider=provider,
        model=model,
        has_relay_key=bool((has_api_key or {}).get("ccapi")),
        family=family,
    )


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
            payload = strip_price_fields(resp.json())
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
