"""FastAPI app: chat UI + tool-calling agent."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from app.agent import SYSTEM_PROMPT, get_api_key, get_model, run_turn  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Grok 助手", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One in-memory session is enough for v1.
_messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
_ui_turns: list[dict[str, Any]] = []


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


def _reset_session() -> None:
    _messages.clear()
    _messages.append({"role": "system", "content": SYSTEM_PROMPT})
    _ui_turns.clear()


def _status_payload() -> dict[str, Any]:
    return {
        "has_api_key": bool(get_api_key()),
        "model": get_model(),
        "workspace": str((PROJECT_ROOT / "workspace").resolve()),
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, **_status_payload()}


@app.get("/api/history")
async def history() -> dict[str, Any]:
    return {"messages": list(_ui_turns), **_status_payload()}


@app.post("/api/clear")
async def clear() -> dict[str, Any]:
    _reset_session()
    return {"ok": True, "messages": [], **_status_payload()}


def _collect_turn(user_text: str) -> dict[str, Any]:
    """Run the agent loop and persist UI + model history."""
    if not get_api_key():
        raise HTTPException(
            status_code=503,
            detail=(
                "未配置 XAI_API_KEY。请复制 .env.example 为 .env，"
                "到 https://console.x.ai 创建密钥后填入，再重启服务。"
            ),
        )

    _messages.append({"role": "user", "content": user_text})
    tools_ui: list[dict[str, Any]] = []
    reply = ""
    error = None

    for event in run_turn(_messages):
        kind = event.get("type")
        if kind == "tool_end":
            tools_ui.append(
                {
                    "name": event.get("name"),
                    "label": event.get("label"),
                    "args_summary": event.get("args_summary"),
                    "ok": event.get("ok"),
                    "summary": event.get("summary"),
                }
            )
        elif kind == "message":
            reply = event.get("content") or ""
        elif kind == "error":
            error = event.get("message") or "未知错误"
            # roll back the user message if we never got a model turn stored
            # (run_turn may have already appended assistant/tool msgs)
            break

    if error and not reply and not tools_ui:
        # No model output at all — drop the dangling user message.
        if _messages and _messages[-1].get("role") == "user":
            _messages.pop()
        raise HTTPException(status_code=502, detail=error)

    assistant_turn = {
        "role": "assistant",
        "content": reply,
        "tools": tools_ui,
        "error": error,
    }
    _ui_turns.append({"role": "user", "content": user_text})
    _ui_turns.append(assistant_turn)
    return {
        "reply": reply,
        "tools": tools_ui,
        "error": error,
        **_status_payload(),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    return _collect_turn(text)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    if not get_api_key():
        raise HTTPException(
            status_code=503,
            detail=(
                "未配置 XAI_API_KEY。请复制 .env.example 为 .env，"
                "到 https://console.x.ai 创建密钥后填入，再重启服务。"
            ),
        )

    def gen():
        def sse(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        _messages.append({"role": "user", "content": text})
        tools_ui: list[dict[str, Any]] = []
        reply = ""
        error = None
        yielded_any = False

        try:
            for event in run_turn(_messages):
                kind = event.get("type")
                if kind == "tool_start":
                    yielded_any = True
                    yield sse(
                        "tool_start",
                        {
                            "name": event.get("name"),
                            "label": event.get("label"),
                            "args_summary": event.get("args_summary"),
                        },
                    )
                elif kind == "tool_end":
                    yielded_any = True
                    item = {
                        "name": event.get("name"),
                        "label": event.get("label"),
                        "args_summary": event.get("args_summary"),
                        "ok": event.get("ok"),
                        "summary": event.get("summary"),
                    }
                    tools_ui.append(item)
                    yield sse("tool_end", item)
                elif kind == "message":
                    yielded_any = True
                    reply = event.get("content") or ""
                    yield sse("message", {"content": reply})
                elif kind == "error":
                    error = event.get("message") or "未知错误"
                    yield sse("error", {"message": error})
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            yield sse("error", {"message": error})

        if error and not reply and not tools_ui and not yielded_any:
            if _messages and _messages[-1].get("role") == "user":
                _messages.pop()
        else:
            _ui_turns.append({"role": "user", "content": text})
            _ui_turns.append(
                {
                    "role": "assistant",
                    "content": reply,
                    "tools": tools_ui,
                    "error": error,
                }
            )
        yield sse("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
