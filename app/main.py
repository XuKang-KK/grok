"""FastAPI app: chat UI + tool-calling agent."""

from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import __version__

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

from app.agent import run_turn  # noqa: E402
from app.providers import missing_key_message, normalize_provider  # noqa: E402
from app.approvals import store as approval_store  # noqa: E402
from app.memory import ensure_data_dir  # noqa: E402
from app.mcp_client import mcp_status  # noqa: E402
from app.routines import (  # noqa: E402
    create_routine,
    delete_routine,
    list_routines,
    set_paused,
    start_scheduler,
    stop_scheduler,
)
from app.sessions import Session, SessionStore  # noqa: E402
from app.auth import (  # noqa: E402
    COOKIE_NAME,
    AccessTokenMiddleware,
    SameOriginCORSMiddleware,
    tokens_match,
    warn_if_lan_unprotected,
)
from app.browser import chromium_available  # noqa: E402
from app.settings import (  # noqa: E402
    get_access_token,
    get_api_key,
    get_bind,
    get_host,
    get_model,
    get_port,
    get_provider,
    has_any_provider_key,
    models_catalog,
    public_settings,
    save_settings,
    strip_secrets,
)
from app.tools import IMAGE_EXTS, MAX_UPLOAD_BYTES, WORKSPACE, save_upload  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"
MEDIA_FOLDERS = {"generated", "uploads", "screenshots"}

ensure_data_dir()
store = SessionStore()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if "pytest" not in sys.modules:
        warn_if_lan_unprotected()
        start_scheduler()
    yield
    stop_scheduler()
    try:
        from app.browser import shutdown_browser

        shutdown_browser()
    except Exception:
        pass
    try:
        from app.mcp_client import shutdown_mcp

        shutdown_mcp()
    except Exception:
        pass


app = FastAPI(title="KK AI助手", version=__version__, lifespan=lifespan)
app.add_middleware(AccessTokenMiddleware)
app.add_middleware(SameOriginCORSMiddleware)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str | None = None
    provider: str | None = None
    model: str | None = None


class SettingsUpdate(BaseModel):
    xai_api_key: str | None = None
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ccapi_api_key: str | None = None
    ccapi_base_url: str | None = None
    provider: str | None = None
    model: str | None = None
    grok_model: str | None = None
    image_model: str | None = None
    allow_local_browser: bool | None = None
    language: str | None = None
    access_token: str | None = None
    clear_access_token: bool | None = None


class LoginRequest(BaseModel):
    token: str = Field(default="", max_length=512)


class ModelSelect(BaseModel):
    provider: str | None = None
    model: str | None = None
    session_id: str | None = None


class ApprovalDecision(BaseModel):
    decision: str = Field(..., pattern="^(approve|deny)$")


class RoutineCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    cron: str = Field(..., min_length=5, max_length=80)
    prompt: str = Field(..., min_length=1, max_length=4000)


def _status_payload(sess: Session | None = None) -> dict[str, Any]:
    active = sess or store.active()
    pub = public_settings()
    provider = (getattr(active, "provider", None) or "").strip() or pub["provider"]
    model = (getattr(active, "model", None) or "").strip() or pub["model"]
    return {
        "has_api_key": pub["has_api_key"],
        "provider": provider,
        "model": model,
        "image_model": pub["image_model"],
        "allow_local_browser": pub["allow_local_browser"],
        "language": pub.get("language") or "zh",
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
        **_status_payload(sess),
        "session_id": sess.id,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
async def web_manifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js")
async def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


def _runtime_payload() -> dict[str, Any]:
    host = get_host()
    return {
        "version": __version__,
        "host": host,
        "bind": get_bind(),
        "auth_required": bool(get_access_token()),
        "has_any_provider_key": has_any_provider_key(),
        "chromium": chromium_available(),
    }


def _ready_payload() -> dict[str, Any]:
    host = get_host()
    issues: list[str] = []
    if not has_any_provider_key():
        issues.append("missing_provider_key")
    if not chromium_available():
        issues.append("missing_chromium")
    if host in {"127.0.0.1", "localhost", "::1"}:
        issues.append("listening_only_on_localhost")
    if host in {"0.0.0.0", "::", "[::]"} and not get_access_token():
        issues.append("auth_missing_on_lan")
    return {"ok": not issues, "issues": issues}


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, **_status_payload(), **_runtime_payload()}


@app.get("/api/ready")
async def ready() -> dict[str, Any]:
    return _ready_payload()


@app.post("/api/login")
async def login(req: LoginRequest, response: Response) -> dict[str, Any]:
    expected = get_access_token()
    if not expected:
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True, "auth_required": False}
    provided = (req.token or "").strip()
    if not tokens_match(provided, expected):
        raise HTTPException(status_code=401, detail="口令不正确")
    response.set_cookie(
        key=COOKIE_NAME,
        value=provided,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=60 * 60 * 24 * 30,
    )
    return {"ok": True, "auth_required": True}


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    payload = {**public_settings(), "ok": True, "version": __version__}
    return strip_secrets(payload)


@app.put("/api/settings")
async def put_settings(req: SettingsUpdate) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if req.xai_api_key is not None:
        updates["xai_api_key"] = req.xai_api_key
    if req.openai_api_key is not None:
        updates["openai_api_key"] = req.openai_api_key
    if req.anthropic_api_key is not None:
        updates["anthropic_api_key"] = req.anthropic_api_key
    if req.ccapi_api_key is not None:
        updates["ccapi_api_key"] = req.ccapi_api_key
    if req.ccapi_base_url is not None:
        updates["ccapi_base_url"] = req.ccapi_base_url
    if req.provider is not None:
        updates["provider"] = normalize_provider(req.provider)
    if req.model is not None:
        updates["model"] = req.model
    if req.grok_model is not None:
        updates["grok_model"] = req.grok_model
    if req.image_model is not None:
        updates["image_model"] = req.image_model
    if req.allow_local_browser is not None:
        updates["allow_local_browser"] = req.allow_local_browser
    if req.language is not None:
        updates["language"] = req.language
    if req.access_token is not None:
        updates["access_token"] = req.access_token
    if req.clear_access_token:
        updates["clear_access_token"] = True
    save_settings(updates)
    if "provider" in updates or "model" in updates or "grok_model" in updates:
        sess = store.active()
        sess.provider = get_provider()
        sess.model = get_model(sess.provider)
        store.save(sess)
    return strip_secrets({**public_settings(), "ok": True})


@app.get("/api/models")
async def get_models() -> dict[str, Any]:
    payload = models_catalog()
    payload["version"] = __version__
    return strip_secrets(payload)


@app.put("/api/model")
async def put_model(req: ModelSelect) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if req.provider is not None:
        updates["provider"] = normalize_provider(req.provider)
    if req.model is not None and str(req.model).strip():
        updates["model"] = str(req.model).strip()
    if updates:
        save_settings(updates)
    sess = _resolve_session(req.session_id) if req.session_id else store.active()
    store.select(sess.id)
    sess.provider = get_provider()
    sess.model = get_model(sess.provider)
    store.save(sess)
    payload = {
        **models_catalog(),
        "ok": True,
        "session_id": sess.id,
        "provider": sess.provider,
        "model": sess.model,
    }
    return strip_secrets(payload)


@app.get("/api/mcp")
async def get_mcp() -> dict[str, Any]:
    return {"ok": True, **mcp_status()}


@app.get("/api/routines")
async def get_routines() -> dict[str, Any]:
    return {"ok": True, "routines": list_routines(), "timezone": "Asia/Shanghai"}


@app.post("/api/routines")
async def post_routine(req: RoutineCreate) -> dict[str, Any]:
    try:
        item = create_routine(req.name, req.cron, req.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "routine": item, "routines": list_routines()}


@app.post("/api/routines/{routine_id}/pause")
async def pause_routine(routine_id: str) -> dict[str, Any]:
    item = set_paused(routine_id, True)
    if item is None:
        raise HTTPException(status_code=404, detail="例程不存在")
    return {"ok": True, "routine": item, "routines": list_routines()}


@app.post("/api/routines/{routine_id}/resume")
async def resume_routine(routine_id: str) -> dict[str, Any]:
    item = set_paused(routine_id, False)
    if item is None:
        raise HTTPException(status_code=404, detail="例程不存在")
    return {"ok": True, "routine": item, "routines": list_routines()}


@app.delete("/api/routines/{routine_id}")
async def remove_routine(routine_id: str) -> dict[str, Any]:
    if not delete_routine(routine_id):
        raise HTTPException(status_code=404, detail="例程不存在")
    return {"ok": True, "routines": list_routines()}


@app.post("/api/approvals/{approval_id}")
async def decide_approval(approval_id: str, req: ApprovalDecision) -> dict[str, Any]:
    rec = approval_store.decide(approval_id, req.decision == "approve")
    if rec is None:
        raise HTTPException(status_code=404, detail="批准请求不存在或已过期")
    return {
        "ok": True,
        "id": rec.id,
        "decision": "approve" if rec.decision else "deny",
    }


@app.get("/api/media/{folder}/{name}")
async def media(folder: str, name: str) -> FileResponse:
    if folder not in MEDIA_FOLDERS:
        raise HTTPException(status_code=404, detail="不支持的目录")
    if "/" in name or "\\" in name or name in (".", "..") or not name:
        raise HTTPException(status_code=400, detail="非法文件名")
    base = (WORKSPACE / folder).resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target)


@app.get("/api/history")
async def history(session_id: str | None = None) -> dict[str, Any]:
    sess = _resolve_session(session_id)
    return {"messages": list(sess.ui_turns), **_status_payload(sess), "session_id": sess.id}


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


def _require_api_key(provider: str | None = None) -> str:
    pid = normalize_provider(provider or get_provider())
    if not get_api_key(pid):
        raise HTTPException(status_code=503, detail=missing_key_message(pid))
    return pid


def _resolve_selection(
    sess: Session,
    provider: str | None = None,
    model: str | None = None,
    *,
    persist: bool = True,
) -> tuple[str, str]:
    pid = normalize_provider(provider or sess.provider or get_provider())
    mid = (model or sess.model or get_model(pid) or "").strip() or get_model(pid)
    if persist:
        changed = False
        if provider or not sess.provider:
            if sess.provider != pid:
                sess.provider = pid
                changed = True
        if model or not sess.model:
            if sess.model != mid:
                sess.model = mid
                changed = True
        if provider or model:
            save_settings({"provider": pid, "model": mid})
            sess.provider = pid
            sess.model = mid
            changed = True
        if changed:
            store.save(sess)
    return pid, mid


def _user_text_and_images(sess: Session, user_text: str) -> tuple[Any, list[str]]:
    image_paths = [str(img.get("path") or "") for img in sess.pending_images if img.get("path")]
    content = sess.take_user_content(user_text)
    return content, image_paths


def _collect_turn(
    sess: Session,
    user_text: str,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Run the agent loop and persist UI + model history."""
    pid, mid = _resolve_selection(sess, provider, model)
    _require_api_key(pid)
    store.select(sess.id)
    sess.refresh_system_prompt()
    sess.maybe_title_from_user(user_text)
    content, image_paths = _user_text_and_images(sess, user_text)
    sess.messages.append({"role": "user", "content": content})
    tools_ui: list[dict[str, Any]] = []
    reply = ""
    error = None

    for event in run_turn(
        sess.messages,
        auto_deny_approvals=True,
        provider=pid,
        model=mid,
    ):
        kind = event.get("type")
        if kind == "tool_end":
            tools_ui.append(
                {
                    "name": event.get("name"),
                    "label": event.get("label"),
                    "args_summary": event.get("args_summary"),
                    "ok": event.get("ok"),
                    "summary": event.get("summary"),
                    "image_url": event.get("image_url"),
                    "source": event.get("source"),
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
    user_turn: dict[str, Any] = {"role": "user", "content": user_text}
    if image_paths:
        user_turn["images"] = image_paths
    sess.ui_turns.append(user_turn)
    sess.ui_turns.append(assistant_turn)
    store.save(sess)
    return {
        "reply": reply,
        "tools": tools_ui,
        "error": error,
        "session_id": sess.id,
        **_status_payload(sess),
    }


@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    session_id: str | None = None,
) -> dict[str, Any]:
    """Save a small file into workspace/uploads/."""
    filename = file.filename or ""
    declared = getattr(file, "size", None)
    if declared is not None and declared > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件过大，上限约 5MB")
    raw = await file.read()
    try:
        payload = json.loads(save_upload(filename, raw))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"上传失败: {exc}") from exc
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=str(payload["error"]))
    ext = Path(payload.get("filename") or filename).suffix.lower()
    if ext in IMAGE_EXTS:
        sess = _resolve_session(session_id)
        sess.attach_pending_image(
            str(payload.get("path") or ""),
            IMAGE_EXTS[ext],
            str(payload.get("filename") or ""),
        )
        store.save(sess)
        payload["is_image"] = True
        payload["media_url"] = f"/api/media/uploads/{payload.get('filename')}"
    else:
        payload["is_image"] = False
    return {"ok": True, **payload}


@app.post("/api/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    sess = _resolve_session(req.session_id)
    return _collect_turn(sess, text, provider=req.provider, model=req.model)


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    text = req.message.strip()
    if not text:
        raise HTTPException(status_code=400, detail="消息不能为空")
    sess = _resolve_session(req.session_id)
    pid, mid = _resolve_selection(sess, req.provider, req.model)
    _require_api_key(pid)

    def gen():
        def sse(event: str, data: dict[str, Any]) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        store.select(sess.id)
        sess.refresh_system_prompt()
        sess.maybe_title_from_user(text)
        content, image_paths = _user_text_and_images(sess, text)
        sess.messages.append({"role": "user", "content": content})
        tools_ui: list[dict[str, Any]] = []
        reply = ""
        error = None
        yielded_any = False

        try:
            for event in run_turn(sess.messages, provider=pid, model=mid):
                kind = event.get("type")
                if kind == "tool_start":
                    yielded_any = True
                    yield sse(
                        "tool_start",
                        {
                            "name": event.get("name"),
                            "label": event.get("label"),
                            "args_summary": event.get("args_summary"),
                            "source": event.get("source"),
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
                        "image_url": event.get("image_url"),
                        "source": event.get("source"),
                    }
                    tools_ui.append(item)
                    yield sse("tool_end", item)
                elif kind == "approval_required":
                    yielded_any = True
                    yield sse(
                        "approval_required",
                        {
                            "id": event.get("id"),
                            "command": event.get("command"),
                            "reason": event.get("reason"),
                            "timeout": event.get("timeout"),
                        },
                    )
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
            user_turn: dict[str, Any] = {"role": "user", "content": text}
            if image_paths:
                user_turn["images"] = image_paths
            sess.ui_turns.append(user_turn)
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

    host = get_host()
    port = get_port()
    warn_if_lan_unprotected(host)
    print(f"启动 KK AI助手：http://{host}:{port}", flush=True)
    uvicorn.run("app.main:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
