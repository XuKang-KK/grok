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

from app import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from app.agent import get_api_key, get_model, run_turn  # noqa: E402
from app.memory import ensure_data_dir  # noqa: E402
from app.sessions import Session, SessionStore  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

ensure_data_dir()
store = SessionStore()

app = FastAPI(title="Grok 助手", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = None


def _status_payload() -> dict[str, Any]:
    active = store.active()
    return {
        "has_api_key": bool(get_api_key()),
        "model": get_model(),
        "workspace": str((PROJECT_ROOT / "workspace").resolve()),
        "session_id": active.id,
        "version": __version__,
    }


def _resolve_session(session_id: str | None) -> Session:
    if session_id:
        sess = store.get(session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return sess
    return store.active()


def _session_view(sess: Session) -> dict[str, Any]:
    return {
        "ok": True,
        "session": sess.meta(),
        "messages": list(sess.ui_turns),
        **_status_payload(),
        "session_id": sess.id,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, **_status_payload()}


@app.get("/api/history")
async def history(session_id: str | None = None) -> dict[str, Any]:
    sess = _resolve_session(session_id)
    return {"messages": list(sess.ui_turns), **_status_payload(), "session_id": sess.id}


@app.get("/api/sessions")
async def list_sessions() -> dict[str, Any]:
    active = store.active()
    return {
        "sessions": store.list(),
        "active_id": active.id,
        **_status_payload(),
    }


@app.post("/api/sessions")
async def create_session() -> dict[str, Any]:
    sess = store.create()
    store.select(sess.id)
    return _session_view(sess)


@app.post("/api/sessions/{session_id}/select")
async def select_session(session_id: str) -> dict[str, Any]:
    sess = store.select(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return _session_view(sess)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    if not store.delete(session_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    active = store.active()
    return {
        "ok": True,
        "sessions": store.list(),
        "active_id": active.id,
        "messages": list(active.ui_turns),
        **_status_payload(),
        "session_id": active.id,
    }


@app.post("/api/clear")
async def clear() -> dict[str, Any]:
    sess = store.active()
    sess.reset()
    store.save(sess)
    return {"ok": True, "messages": [], **_status_payload(), "session_id": sess.id}


def _require_api_key() -> None:
    if not get_api_key():
        raise HTTPException(
            status_code=503,
            detail=(
                "未配置 XAI_API_KEY。请复制 .env.example 为 .env，"
                "到 https://console.x.ai 创建密钥后填入，再重启服务。"
            ),
        )


def _collect_turn(sess: Session, user_text: str) -> dict[str, Any]:
    """Run the agent loop and persist UI + model history."""
    _require_api_key()
    store.select(sess.id)
    sess.refresh_system_prompt()
    sess.maybe_title_from_user(user_text)
    sess.messages.append({"role": "user", "content": user_text})
    tools_ui: list[dict[str, Any]] = []
    reply = ""
    error = None

    for event in run_turn(sess.messages):
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
            break

    if error and not reply and not tools_ui:
        if sess.messages and sess.messages[-1].get("role") == "user":
            sess.messages.pop()
        store.save(sess)
        raise HTTPException(status_code=502, detail=error)

    assistant_turn = {
        "role": "assistant",
        "content": reply,
        "tools": tools_ui,
        "error": error,
    }
    sess.ui_turns.append({"role": "user", "content": user_text})
    sess.ui_turns.append(assistant_turn)
    store.save(sess)
    return {
        "reply": reply,
        "tools": tools_ui,
        "error": error,
        "session_id": sess.id,
        **_status_payload(),
    }


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    sess = _resolve_session(req.session_id)
    return _collect_turn(sess, text)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    _require_api_key()
    sess = _resolve_session(req.session_id)

    def gen():
        def sse(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        store.select(sess.id)
        sess.refresh_system_prompt()
        sess.maybe_title_from_user(text)
        sess.messages.append({"role": "user", "content": text})
        tools_ui: list[dict[str, Any]] = []
        reply = ""
        error = None
        yielded_any = False

        try:
            for event in run_turn(sess.messages):
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
            if sess.messages and sess.messages[-1].get("role") == "user":
                sess.messages.pop()
        else:
            sess.ui_turns.append({"role": "user", "content": text})
            sess.ui_turns.append(
                {
                    "role": "assistant",
                    "content": reply,
                    "tools": tools_ui,
                    "error": error,
                }
            )
        store.save(sess)
        yield sse("done", {"session_id": sess.id, "title": sess.title})

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
