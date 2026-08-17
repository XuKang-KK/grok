"""Anthropic Messages API tool loop (not OpenAI-compatible)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from app.agent import _ensure_system_prompt, _parse_args, run_one_tool
from app.providers import (
    ANTHROPIC_MESSAGES_URL,
    ANTHROPIC_VERSION,
    VisionUnsupported,
    anthropic_text_from_content,
    missing_key_message,
    openai_history_to_anthropic,
    openai_tools_to_anthropic,
    provider_meta,
)
from app.settings import get_api_key
from app.tools import get_tool_schemas


def _anthropic_error_message(status: int, body: str, provider_name: str) -> str:
    if status in (401, 403):
        return f"{provider_name} API 鉴权失败。请检查设置中的 ANTHROPIC_API_KEY 是否有效。"
    if status == 429:
        return f"{provider_name} API 触发限流，请稍后再试。"
    detail = body.strip()[:400] or f"HTTP {status}"
    try:
        parsed = json.loads(body)
        err = parsed.get("error") if isinstance(parsed, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or detail)
        elif isinstance(err, str):
            detail = err
    except json.JSONDecodeError:
        pass
    return f"模型接口错误：{detail}"


def _post_messages(
    *,
    api_key: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": 8192,
        "messages": messages,
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        raise RuntimeError(
            _anthropic_error_message(resp.status_code, resp.text, "Anthropic")
        )
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("模型接口错误：Anthropic 返回了无法解析的响应")
    return data


def _tool_uses(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            out.append(block)
    return out


def run_anthropic_turn(
    messages: list[dict[str, Any]],
    *,
    provider: str,
    model: str,
    max_iters: int,
    allow_delegate: bool,
    source: str,
    auto_deny_approvals: bool,
) -> Iterator[dict[str, Any]]:
    """Mutates `messages` in OpenAI history shape; talks to Anthropic Messages API."""
    key = get_api_key(provider)
    if not key:
        yield {"type": "error", "message": missing_key_message(provider)}
        return

    _ensure_system_prompt(messages, subagent=(source == "subagent"))
    openai_tools = get_tool_schemas(allow_delegate=allow_delegate)
    tools = openai_tools_to_anthropic(openai_tools)

    for _step in range(max_iters):
        try:
            system, anth_messages = openai_history_to_anthropic(messages)
        except VisionUnsupported as exc:
            yield {"type": "error", "message": str(exc)}
            return

        if not anth_messages:
            yield {"type": "error", "message": "没有可发送给 Anthropic 的消息。"}
            return

        try:
            data = _post_messages(
                api_key=key,
                model=model,
                system=system,
                messages=anth_messages,
                tools=tools,
            )
        except RuntimeError as exc:
            yield {"type": "error", "message": str(exc)}
            return
        except httpx.HTTPError as exc:
            yield {
                "type": "error",
                "message": f"请求失败：{type(exc).__name__}: {exc}",
            }
            return
        except Exception as exc:  # noqa: BLE001
            yield {
                "type": "error",
                "message": f"请求失败：{type(exc).__name__}: {exc}",
            }
            return

        content = data.get("content") or []
        uses = _tool_uses(content)
        text = anthropic_text_from_content(content).strip()

        if uses:
            assistant_payload: dict[str, Any] = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": str(block.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": json.dumps(
                                block.get("input")
                                if isinstance(block.get("input"), dict)
                                else {},
                                ensure_ascii=False,
                            ),
                        },
                    }
                    for block in uses
                ],
            }
            messages.append(assistant_payload)
            for block in uses:
                name = str(block.get("name") or "")
                raw_input = block.get("input")
                if isinstance(raw_input, dict):
                    args_obj = raw_input
                    args_json = json.dumps(raw_input, ensure_ascii=False)
                else:
                    args_json = json.dumps(raw_input or {}, ensure_ascii=False)
                    args_obj = _parse_args(args_json)
                result = json.dumps({"error": "工具未返回"}, ensure_ascii=False)
                for ev in run_one_tool(
                    name,
                    args_obj,
                    args_json,
                    source=source,
                    allow_delegate=allow_delegate,
                    auto_deny_approvals=auto_deny_approvals,
                ):
                    if ev.get("type") == "_tool_result":
                        result = ev.get("result") or result
                        continue
                    yield ev
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(block.get("id") or ""),
                        "content": result,
                    }
                )
            continue

        messages.append({"role": "assistant", "content": text})
        yield {"type": "message", "content": text}
        return

    yield {
        "type": "error",
        "message": f"已达到最大工具轮次（{max_iters}），已停止以免循环。",
    }


# imported for type checkers / tests; keep meta available
__all__ = ["run_anthropic_turn", "provider_meta"]
