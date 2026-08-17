"""Grok tool-calling loop via OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from typing import Any

from openai import APIError, AuthenticationError, OpenAI, RateLimitError

from app.approvals import APPROVAL_TIMEOUT_SEC, store as approval_store
from app.settings import get_api_key, get_model
from app.tools import (
    classify_command,
    execute_tool,
    get_tool_schemas,
    summarize_args,
    tool_label,
)

DEFAULT_MODEL = "grok-4.6"
MAX_TOOL_ITERS = 8
MAX_SUBAGENT_ITERS = 5
SUBAGENT_CONCURRENCY = 2
XAI_BASE_URL = "https://api.x.ai/v1"

SYSTEM_PROMPT = """你是一个运行在用户本机上的 Grok 风格助手。请用简体中文回答，除非用户使用其他语言。

你可以使用以下工具：
- web_search：搜索公开网页（文档、新闻、API 说明）。
- fetch_url：抓取指定公开 http(s) URL 并提取正文（不能访问 localhost / 内网 / file://）。
- list_dir：列出 workspace/ 下的文件和目录。
- read_file：读取 workspace/ 内的文本文件（例如 sample.txt，或用户上传的 uploads/foo.txt）。
- write_file：在 workspace/ 内创建或覆盖文本文件（写文件请优先用这个）。
- run_command：在 workspace/ 下执行 shell 命令。明显危险命令会被硬拦截；rm / chmod / 写入沙箱外等中风险命令需要用户批准。
- memory_write：把用户偏好、姓名、长期事实写入持久记忆（跨会话保留）。
- memory_read：检索已保存的记忆；query 为空则返回最近条目。
- browser_open / browser_snapshot / browser_click / browser_type / browser_screenshot：用 Chromium 浏览公开网页。先 open，再 snapshot 获取 ref，然后 click/type。
- generate_image：根据文字生成图片，保存到 workspace/generated/。
- delegate_task：把可拆开的子任务交给子助手（子助手不能再委派）。
- 以及用户在 data/mcp.json 里启用的 MCP 工具。

原则：
1. 需要外部或最新信息时先 web_search；已有具体 URL 时用 fetch_url。不要编造链接或文档细节。
2. 用户提到工作目录 / 列出文件 / 已上传文件时，使用 list_dir 或 read_file（上传文件在 uploads/ 下）。
3. 创建或修改文本文件请用 write_file，不要用 run_command 做重定向写文件。
4. 用户说出偏好、身份、长期应注意的事实时，调用 memory_write 记住。
5. 工具失败时根据错误说明原因，不要假装成功。
6. 最终回复简洁、可读；需要时用 Markdown。引用搜索结果时带上链接。
7. 不要声称你能访问用户本机全部磁盘——只能通过上述工具访问 workspace/。
8. 系统提示中已注入最近若干条记忆；需要精确查找时再调用 memory_read。
9. 用户消息可能包含图片（vision）。请直接理解图像内容，不必再 read_file 二进制图。
10. 打开网页用 browser_open，再 snapshot；不要用 fetch_url 代替需要交互的页面。
11. 生成图片必须调用 generate_image，不要假装已经画好。
12. 复杂可拆分的任务可用 delegate_task。
"""

SUBAGENT_PROMPT = """你是主助手派出的子助手。请用简体中文完成交给你的目标。
你可以使用主助手的工具，但不能再调用 delegate_task，也不能要求用户批准之外的交互。
完成后给出简洁结论；需要写文件时写入 workspace/。
"""

_sub_sema = threading.BoundedSemaphore(SUBAGENT_CONCURRENCY)
_tls = threading.local()


def build_system_prompt(*, subagent: bool = False) -> str:
    from app.memory import memory_summary

    base = SUBAGENT_PROMPT if subagent else SYSTEM_PROMPT
    extra = memory_summary(20)
    if not extra:
        return base
    return (
        base
        + "\n\n以下是此前记住的用户事实（来自 memory 工具，按时间最近若干条）：\n"
        + extra
        + "\n请在回答时自然参考这些事实，不必主动复述整份列表。"
    )


def make_client() -> OpenAI:
    key = get_api_key()
    if not key:
        raise RuntimeError(
            "未配置 XAI_API_KEY。请在设置面板填写，或写入 data/settings.json / .env。"
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


def _label(name: str, source: str) -> str:
    base = tool_label(name)
    if source == "subagent" and name != "delegate_task":
        return f"子助手 · {base}"
    return base


def _parse_args(args_json: str) -> dict[str, Any]:
    try:
        args_obj = json.loads(args_json or "{}")
        if not isinstance(args_obj, dict):
            return {}
        return args_obj
    except json.JSONDecodeError:
        return {}


def delegate_task_impl(goal: str, context: str = "") -> str:
    """Runtime guard used by tests and as a last-resort dispatch."""
    if getattr(_tls, "in_subagent", False):
        return json.dumps({"error": "子助手不能再委派任务"}, ensure_ascii=False)
    return json.dumps({"error": "请通过主循环调用 delegate_task"}, ensure_ascii=False)


def _run_subagent(goal: str, context: str = "") -> Iterator[dict[str, Any]]:
    if getattr(_tls, "in_subagent", False):
        yield {
            "type": "error",
            "message": "子助手不能再委派任务",
        }
        return
    if not _sub_sema.acquire(blocking=False):
        yield {
            "type": "tool_end",
            "name": "delegate_task",
            "label": "子助手",
            "args_summary": (goal or "")[:120],
            "ok": False,
            "summary": f"子助手并发已满（上限 {SUBAGENT_CONCURRENCY}）",
        }
        return
    _tls.in_subagent = True
    reply = ""
    error = None
    try:
        user = f"目标：{goal or ''}"
        if context:
            user += f"\n\n上下文：{context}"
        messages: list[dict[str, Any]] = [{"role": "user", "content": user}]
        for event in run_turn(
            messages,
            max_iters=MAX_SUBAGENT_ITERS,
            allow_delegate=False,
            source="subagent",
            auto_deny_approvals=True,
        ):
            if event.get("type") == "message":
                reply = event.get("content") or ""
            elif event.get("type") == "error":
                error = event.get("message")
            yield event
    finally:
        _tls.in_subagent = False
        _sub_sema.release()
    result = {
        "ok": not error,
        "reply": reply,
        "error": error,
        "goal": goal,
    }
    yield {
        "type": "_delegate_result",
        "result": json.dumps(result, ensure_ascii=False),
        "reply": reply,
        "error": error,
    }


def run_turn(
    messages: list[dict[str, Any]],
    *,
    max_iters: int | None = None,
    allow_delegate: bool = True,
    source: str = "",
    auto_deny_approvals: bool = False,
) -> Iterator[dict[str, Any]]:
    """Execute one user turn. `messages` is mutated in place (OpenAI history).

    Yields event dicts:
      {"type": "tool_start", ...}
      {"type": "tool_end", ...}
      {"type": "approval_required", ...}
      {"type": "message", "content": "..."}
      {"type": "error", "message": "..."}
    """
    try:
        client = make_client()
    except RuntimeError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    model = get_model()
    tools = get_tool_schemas(allow_delegate=allow_delegate)
    limit = max_iters if max_iters is not None else MAX_TOOL_ITERS

    prompt = build_system_prompt(subagent=(source == "subagent"))
    if messages and messages[0].get("role") == "system":
        messages[0]["content"] = prompt
    else:
        messages.insert(0, {"role": "system", "content": prompt})

    for _step in range(limit):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
        except AuthenticationError:
            yield {
                "type": "error",
                "message": "xAI API 鉴权失败。请检查设置中的 XAI_API_KEY 是否有效。",
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
                args_obj = _parse_args(args_json)
                yield {
                    "type": "tool_start",
                    "name": name,
                    "label": _label(name, source),
                    "args_summary": summarize_args(name, args_obj),
                    "source": source or "main",
                }

                if name == "delegate_task":
                    if not allow_delegate or getattr(_tls, "in_subagent", False):
                        result = json.dumps(
                            {"error": "子助手不能再委派任务"},
                            ensure_ascii=False,
                        )
                        ui = {
                            "name": name,
                            "label": "子助手",
                            "args_summary": summarize_args(name, args_obj),
                            "ok": False,
                            "summary": "子助手不能再委派任务",
                            "source": source or "main",
                        }
                        yield {"type": "tool_end", **ui}
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            }
                        )
                        continue
                    nested_result = json.dumps(
                        {"error": "子助手没有返回"},
                        ensure_ascii=False,
                    )
                    for ev in _run_subagent(
                        str(args_obj.get("goal") or ""),
                        str(args_obj.get("context") or ""),
                    ):
                        if ev.get("type") == "_delegate_result":
                            nested_result = ev.get("result") or nested_result
                            continue
                        yield ev
                    ok = True
                    try:
                        parsed = json.loads(nested_result)
                        if isinstance(parsed, dict) and parsed.get("error") and not parsed.get("reply"):
                            ok = False
                    except json.JSONDecodeError:
                        pass
                    yield {
                        "type": "tool_end",
                        "name": name,
                        "label": "子助手",
                        "args_summary": summarize_args(name, args_obj),
                        "ok": ok,
                        "summary": "子助手已完成" if ok else "子助手失败",
                        "source": source or "main",
                    }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": nested_result,
                        }
                    )
                    continue

                if name == "run_command":
                    level, reason = classify_command(str(args_obj.get("command") or ""))
                    if level == "deny":
                        result = json.dumps(
                            {"error": reason, "blocked": True},
                            ensure_ascii=False,
                        )
                        yield {
                            "type": "tool_end",
                            "name": name,
                            "label": _label(name, source),
                            "args_summary": summarize_args(name, args_obj),
                            "ok": False,
                            "summary": f"失败：{reason[:80]}",
                            "source": source or "main",
                        }
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            }
                        )
                        continue
                    if level == "approve":
                        if auto_deny_approvals:
                            result = json.dumps(
                                {
                                    "error": "中风险命令未获批准（非交互环境已默认拒绝）",
                                    "blocked": True,
                                    "denied": True,
                                },
                                ensure_ascii=False,
                            )
                            yield {
                                "type": "tool_end",
                                "name": name,
                                "label": _label(name, source),
                                "args_summary": summarize_args(name, args_obj),
                                "ok": False,
                                "summary": "未获批准，已拒绝",
                                "source": source or "main",
                            }
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": result,
                                }
                            )
                            continue
                        rec = approval_store.create(
                            str(args_obj.get("command") or ""),
                            reason,
                        )
                        yield {
                            "type": "approval_required",
                            **rec.to_event(),
                        }
                        approved = rec.wait(APPROVAL_TIMEOUT_SEC)
                        if not approved:
                            result = json.dumps(
                                {
                                    "error": "用户拒绝或等待超时，命令未执行",
                                    "blocked": True,
                                    "denied": True,
                                },
                                ensure_ascii=False,
                            )
                            yield {
                                "type": "tool_end",
                                "name": name,
                                "label": _label(name, source),
                                "args_summary": summarize_args(name, args_obj),
                                "ok": False,
                                "summary": "用户拒绝或超时",
                                "source": source or "main",
                            }
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.id,
                                    "content": result,
                                }
                            )
                            continue

                result, ui = execute_tool(name, args_json)
                ui["label"] = _label(name, source)
                ui["source"] = source or "main"
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
        "message": f"已达到最大工具轮次（{limit}），已停止以免循环。",
    }
