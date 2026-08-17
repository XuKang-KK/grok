"""xAI / CCAPI OpenAI-compatible image generation -> workspace/generated/."""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin

import httpx
from openai import OpenAI

from app.settings import get_api_key, get_ccapi_base_url, get_image_model, get_provider, is_public_mode
from app.tools import WORKSPACE, validate_public_http_url

GENERATED_DIRNAME = "generated"
MAX_PROMPT = 2000


def _slug(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "_", (text or "").strip())
    text = text.strip("_")[:24] or "image"
    return text


def _generate_with_client(client: OpenAI, model: str, prompt: str) -> tuple[bytes | None, str | None]:
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
            raw = _download_public_image(url)
        except Exception as exc2:  # noqa: BLE001
            last_err = f"{last_err}; fallback: {type(exc2).__name__}: {exc2}"
    return raw, last_err


def _xai_client() -> OpenAI:
    from app.agent import make_xai_client

    return make_xai_client()


def _download_public_image(url: str) -> bytes:
    """GET a remote image URL after SSRF checks. Never follow unchecked redirects."""
    current = (url or "").strip()
    with httpx.Client(timeout=60.0, follow_redirects=False) as http:
        for _hop in range(5):
            err = validate_public_http_url(current)
            if err:
                raise RuntimeError(err)
            r = http.get(current)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    raise RuntimeError("重定向缺少 Location")
                current = urljoin(current, loc)
                continue
            r.raise_for_status()
            return r.content
    raise RuntimeError("重定向过多")


def generate_image(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return json.dumps({"error": "prompt 不能为空"}, ensure_ascii=False)
    if len(prompt) > MAX_PROMPT:
        return json.dumps(
            {"error": f"prompt 过长（{len(prompt)}），上限 {MAX_PROMPT}"},
            ensure_ascii=False,
        )

    model = get_image_model()
    raw: bytes | None = None
    last_err = None
    errors: list[str] = []

    pid = get_provider()
    if pid == "ccapi":
        key = get_api_key("ccapi")
        if key:
            try:
                client = OpenAI(api_key=key, base_url=get_ccapi_base_url(), timeout=90.0)
                raw, last_err = _generate_with_client(client, model, prompt)
                if last_err:
                    errors.append(f"中转站: {last_err}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"中转站: {type(exc).__name__}: {exc}")
        if not raw and get_api_key("xai"):
            try:
                raw, last_err = _generate_with_client(_xai_client(), model, prompt)
                if last_err:
                    errors.append(f"xAI: {last_err}")
            except RuntimeError as exc:
                errors.append(str(exc))
        if not raw and not get_api_key("ccapi") and not get_api_key("xai"):
            return json.dumps(
                {
                    "error": "图像生成需要中转站或 xAI 密钥。请在设置中填写，或写入 data/settings.json / .env。",
                    "model": model,
                },
                ensure_ascii=False,
            )
    else:
        try:
            raw, last_err = _generate_with_client(_xai_client(), model, prompt)
            if last_err:
                errors.append(last_err)
        except RuntimeError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)

    if not raw:
        return json.dumps(
            {
                "error": "；".join(errors) or last_err or "图像生成失败",
                "model": model,
            },
            ensure_ascii=False,
        )

    dest_dir = (WORKSPACE / GENERATED_DIRNAME).resolve()
    url_name = None
    if is_public_mode():
        from app.publicmode import current_visitor

        vid = current_visitor()
        name = f"{uuid.uuid4().hex}.png"
        if vid:
            dest_dir = (dest_dir / vid).resolve()
            url_name = f"{vid}/{name}"
        else:
            url_name = name
    else:
        name = f"img_{time.strftime('%Y%m%d_%H%M%S')}_{_slug(prompt)}.png"
        url_name = name
    dest_dir.mkdir(parents=True, exist_ok=True)
    generated_root = (WORKSPACE / GENERATED_DIRNAME).resolve()
    if not dest_dir.is_relative_to(generated_root) or not dest_dir.is_relative_to(WORKSPACE.resolve()):
        return json.dumps({"error": "路径越界"}, ensure_ascii=False)
    target = (dest_dir / name).resolve()
    if not target.is_relative_to(dest_dir):
        return json.dumps({"error": "路径越界"}, ensure_ascii=False)
    try:
        target.write_bytes(raw)
    except OSError as exc:
        return json.dumps({"error": f"保存失败: {exc}"}, ensure_ascii=False)
    rel = target.relative_to(WORKSPACE.resolve()).as_posix()
    return json.dumps(
        {
            "ok": True,
            "path": rel,
            "url": f"/api/media/{GENERATED_DIRNAME}/{url_name}",
            "bytes": len(raw),
            "model": model,
            "prompt": prompt,
        },
        ensure_ascii=False,
    )
