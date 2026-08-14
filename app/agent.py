"""Grok tool-calling loop via OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

from openai import APIError, AuthenticationError, OpenAI, RateLimitError

from app.tools import TOOLS, TOOL_LABELS, execute_tool, summarize_args

DEFAULT_MODEL = "grok-4.6"
MAX_TOOL_ITERS = 8
XAI_BASE_URL = "https://api.x.ai/v1"

SYSTEM_PROMPT = """你是一个运行在用户本机上的 Grok 风格助手。请用简体中文回答，除非用户使用其他语言。

你可以使用以下工具：
- web_search：搜索公开网页（文档、新闻、API 说明）。
- read_file：读取工作目录 workspace/ 内的文本文件（例如 sample.txt）。
- run_command：在 workspace/ 下执行 shell 命令（列目录、创建文件等）。

原则：
1. 需要外部或最新信息时先 web_search，不要编造链接或文档细节。
2. 用户提到工作目录 / sample.txt / 列出文件时，使用 read_file 或 run_command。
3. 创建文件请用 run_command（例如 printf 或 cat 重定向），cwd 已是 workspace。
4. 工具失败时根据错误说明原因，不要假装成功。
5. 最终回复简洁、可读；需要时用 Markdown。引用搜索结果时带上链接。
6. 不要声称你能访问用户本机全部磁盘——只能通过上述工具访问 workspace/。
"""


def get_api_key() -> str:
    return (os.getenv("XAI_API_KEY") or "").strip()


def get_model() -> str:
    return (os.getenv("GROK_MODEL") or os.getenv("XAI_MODEL") or DEFAULT_MODEL).strip()


def make_client() -> OpenAI:
    key = get_api_key()
    if not key:
        raise RuntimeError(
            "未配置 XAI_API_KEY。请复制 .env.example 为 .env，"
            "并在 https://console.x.ai 创建密钥后填入。"
        )
    return OpenAI(api_key=key, base_url=XAI_BASE_URL, timeout=90.0)


def _assistant_message_dict(message: Any) -> dict[str, Any]:
    tool_calls = getattr(message, "tool_calls", None) or []
    payload: dict[str, Any] = {
        "role": "assistant",
        "content": message.content or None,
    }
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ]
    return payload


def run_turn(messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Execute one user turn. `messages` is mutated in place (OpenAI history).

    Yields event dicts:
      {"type": "tool_start", ...}
      {"type": "tool_end", ...}
      {"type": "message", "content": "..."}
      {"type": "error", "message": "..."}
    """
    try:
        client = make_client()
    except RuntimeError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    model = get_model()

    for _step in range(MAX_TOOL_ITERS):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
        except AuthenticationError:
            yield {
                "type": "error",
                "message": "xAI API 鉴权失败。请检查 .env 中的 XAI_API_KEY 是否有效。",
            }
            return
        except RateLimitError:
            yield {
                "type": "error",
                "message": "xAI API 触发限流，请稍后再试。",
            }
            return
        except APIError as exc:
            detail = getattr(exc, "message", None) or str(exc)
            yield {"type": "error", "message": f"模型接口错误：{detail}"}
            return
        except Exception as exc:  # noqa: BLE001
            yield {
                "type": "error",
                "message": f"请求失败：{type(exc).__name__}: {exc}",
            }
            return

        choice = resp.choices[0]
        message = choice.message
        tool_calls = message.tool_calls or []

        if tool_calls:
            messages.append(_assistant_message_dict(message))
            for tc in tool_calls:
                name = tc.function.name
                args_json = tc.function.arguments or "{}"
                try:
                    args_obj = json.loads(args_json)
                    if not isinstance(args_obj, dict):
                        args_obj = {}
                except json.JSONDecodeError:
                    args_obj = {}
                yield {
                    "type": "tool_start",
                    "name": name,
                    "label": TOOL_LABELS.get(name, name),
                    "args_summary": summarize_args(name, args_obj),
                }
                result, ui = execute_tool(name, args_json)
                yield {"type": "tool_end", **ui}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    }
                )
            continue

        content = (message.content or "").strip()
        messages.append({"role": "assistant", "content": content})
        yield {"type": "message", "content": content}
        return

    yield {
        "type": "error",
        "message": f"已达到最大工具轮次（{MAX_TOOL_ITERS}），已停止以免循环。",
    }
