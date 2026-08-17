"""xAI / OpenAI-compatible image generation -> workspace/generated/."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import httpx

from app.settings import get_image_model
from app.tools import WORKSPACE

GENERATED_DIRNAME = "generated"
MAX_PROMPT = 2000


def _slug(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (text or "").strip())
    text = text.strip("_")[:24] or "image"
    return text


def generate_image(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return json.dumps({"error": "prompt 不能为空"}, ensure_ascii=False)
    if len(prompt) > MAX_PROMPT:
        return json.dumps(
            {"error": f"prompt 过长（{len(prompt)}），上限 {MAX_PROMPT}"},
            ensure_ascii=False,
        )
    from app.agent import make_client

    try:
        client = make_client()
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)

    model = get_image_model()
    raw: bytes | None = None
    last_err = None
    try:
        resp = client.images.generate(
            model=model,
            prompt=prompt,
            response_format="b64_json",
        )
        b64 = resp.data[0].b64_json
        if b64:
            raw = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        last_err = f"{type(exc).__name__}: {exc}"
        try:
            resp = client.images.generate(model=model, prompt=prompt)
            url = getattr(resp.data[0], "url", None)
            if not url:
                raise RuntimeError("图像接口未返回 url 或 b64_json")
            with httpx.Client(timeout=60.0, follow_redirects=True) as http:
                r = http.get(url)
                r.raise_for_status()
                raw = r.content
        except Exception as exc2:  # noqa: BLE001
            last_err = f"{last_err}; fallback: {type(exc2).__name__}: {exc2}"

    if not raw:
        return json.dumps(
            {
                "error": last_err or "图像生成失败",
                "model": model,
            },
            ensure_ascii=False,
        )

    dest_dir = (WORKSPACE / GENERATED_DIRNAME).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not dest_dir.is_relative_to(WORKSPACE.resolve()):
        return json.dumps({"error": "路径越界"}, ensure_ascii=False)
    name = f"img_{time.strftime('%Y%m%d_%H%M%S')}_{_slug(prompt)}.png"
    target = (dest_dir / name).resolve()
    if not target.is_relative_to(dest_dir):
        return json.dumps({"error": "路径越界"}, ensure_ascii=False)
    try:
        target.write_bytes(raw)
    except OSError as exc:
        return json.dumps({"error": f"保存失败: {exc}"}, ensure_ascii=False)
    rel = f"{GENERATED_DIRNAME}/{name}"
    return json.dumps(
        {
            "ok": True,
            "path": rel,
            "url": f"/api/media/{GENERATED_DIRNAME}/{name}",
            "bytes": len(raw),
            "model": model,
            "prompt": prompt,
        },
        ensure_ascii=False,
    )
