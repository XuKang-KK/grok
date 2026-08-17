"""Lazy Playwright Chromium tools with the same URL sandbox as fetch_url."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from app.settings import allow_local_browser
from app.tools import WORKSPACE, validate_public_http_url

IDLE_SEC = 180
MAX_SNAPSHOT = 80
SCREENSHOT_DIRNAME = "screenshots"

_lock = threading.Lock()
_pw = None
_browser = None
_page = None
_last_used = 0.0
_idle_thread: threading.Thread | None = None
_refs: dict[str, str] = {}


def validate_browser_url(url: str, *, allow_local: bool | None = None) -> str | None:
    """Return an error string if the URL is not allowed, else None."""
    if allow_local is None:
        allow_local = allow_local_browser()
    err = validate_public_http_url(url, allow_private=bool(allow_local))
    return err


def _touch() -> None:
    global _last_used
    _last_used = time.time()


def _start_idle_watcher() -> None:
    global _idle_thread
    if _idle_thread and _idle_thread.is_alive():
        return

    def _loop() -> None:
        while True:
            time.sleep(15)
            with _lock:
                if _browser is None:
                    return
                if _last_used and (time.time() - _last_used) > IDLE_SEC:
                    _close_unlocked()
                    return

    _idle_thread = threading.Thread(target=_loop, name="browser-idle", daemon=True)
    _idle_thread.start()


def _close_unlocked() -> None:
    global _pw, _browser, _page, _refs
    _refs = {}
    page, browser, pw = _page, _browser, _pw
    _page = None
    _browser = None
    _pw = None
    for obj, closer in (
        (page, lambda p: p.close()),
        (browser, lambda b: b.close()),
        (pw, lambda p: p.stop()),
    ):
        if obj is None:
            continue
        try:
            closer(obj)
        except Exception:
            pass


def shutdown_browser() -> None:
    with _lock:
        _close_unlocked()


def _ensure_page() -> Any:
    global _pw, _browser, _page
    _touch()
    if _page is not None:
        try:
            if not _page.is_closed():
                return _page
        except Exception:
            _page = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "未安装 playwright。请执行 pip install playwright && python -m playwright install chromium"
        ) from exc
    if _pw is None:
        _pw = sync_playwright().start()
    if _browser is None:
        _browser = _pw.chromium.launch(headless=True)
    context = _browser.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=False,
    )
    _page = context.new_page()
    _start_idle_watcher()
    return _page


def _err(msg: str, **extra: Any) -> str:
    payload = {"error": msg, **extra}
    return json.dumps(payload, ensure_ascii=False)


def browser_open(url: str) -> str:
    url = (url or "").strip()
    err = validate_browser_url(url)
    if err:
        return _err(err, url=url)
    with _lock:
        try:
            page = _ensure_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            title = ""
            try:
                title = page.title() or ""
            except Exception:
                title = ""
            final = page.url
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}", url=url)
    ferr = validate_browser_url(final)
    if ferr:
        return _err(f"导航后的地址被拒绝：{ferr}", url=final)
    return json.dumps(
        {"ok": True, "url": final, "title": title},
        ensure_ascii=False,
    )


def _tag_snapshot(page: Any) -> list[dict[str, Any]]:
    script = """
    () => {
      const sel = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"], [contenteditable="true"]';
      const els = Array.from(document.querySelectorAll(sel));
      return els.slice(0, 80).map((el, i) => {
        const ref = 'e' + (i + 1);
        el.setAttribute('data-grok-ref', ref);
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
        return {
          ref,
          tag: (el.tagName || '').toLowerCase(),
          type: el.getAttribute('type') || '',
          role: el.getAttribute('role') || '',
          text,
          href: (el.getAttribute('href') || '').slice(0, 160),
        };
      });
    }
    """
    return list(page.evaluate(script) or [])


def browser_snapshot() -> str:
    with _lock:
        if _page is None:
            return _err("浏览器尚未打开，请先调用 browser_open")
        try:
            page = _ensure_page()
            items = _tag_snapshot(page)
            title = page.title() or ""
            url = page.url
            global _refs
            _refs = {str(it["ref"]): f'[data-grok-ref="{it["ref"]}"]' for it in items}
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")
    ferr = validate_browser_url(url)
    if ferr:
        return _err(f"当前页面地址被拒绝：{ferr}", url=url)
    lines = [f"[{it['ref']}] {it['tag']} {it['text']}".strip() for it in items]
    return json.dumps(
        {
            "ok": True,
            "url": url,
            "title": title,
            "items": items,
            "text": "\n".join(lines),
        },
        ensure_ascii=False,
    )


def _resolve_selector(ref_or_selector: str) -> str:
    raw = (ref_or_selector or "").strip()
    if not raw:
        raise ValueError("ref_or_selector 不能为空")
    if raw in _refs:
        return _refs[raw]
    if raw.startswith("e") and raw[1:].isdigit():
        return f'[data-grok-ref="{raw}"]'
    lowered = raw.lower()
    if lowered.startswith("javascript:") or "expression(" in lowered:
        raise ValueError("非法选择器")
    return raw


def browser_click(ref_or_selector: str) -> str:
    try:
        sel = _resolve_selector(ref_or_selector)
    except ValueError as exc:
        return _err(str(exc))
    with _lock:
        if _page is None:
            return _err("浏览器尚未打开，请先调用 browser_open")
        try:
            page = _ensure_page()
            page.locator(sel).first.click(timeout=10_000)
            url = page.url
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}", selector=sel)
    return json.dumps({"ok": True, "selector": sel, "url": url}, ensure_ascii=False)


def browser_type(ref_or_selector: str, text: str) -> str:
    try:
        sel = _resolve_selector(ref_or_selector)
    except ValueError as exc:
        return _err(str(exc))
    if text is None:
        text = ""
    with _lock:
        if _page is None:
            return _err("浏览器尚未打开，请先调用 browser_open")
        try:
            page = _ensure_page()
            loc = page.locator(sel).first
            loc.click(timeout=10_000)
            loc.fill(str(text), timeout=10_000)
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}", selector=sel)
    return json.dumps(
        {"ok": True, "selector": sel, "typed": len(str(text))},
        ensure_ascii=False,
    )


def browser_screenshot() -> str:
    dest_dir = (WORKSPACE / SCREENSHOT_DIRNAME).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not dest_dir.is_relative_to(WORKSPACE.resolve()):
        return _err("路径越界")
    name = time.strftime("shot_%Y%m%d_%H%M%S") + ".png"
    target = (dest_dir / name).resolve()
    if not target.is_relative_to(dest_dir):
        return _err("路径越界")
    with _lock:
        if _page is None:
            return _err("浏览器尚未打开，请先调用 browser_open")
        try:
            page = _ensure_page()
            page.screenshot(path=str(target), full_page=False)
            url = page.url
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")
    rel = f"{SCREENSHOT_DIRNAME}/{name}"
    return json.dumps(
        {
            "ok": True,
            "path": rel,
            "url": f"/api/media/{SCREENSHOT_DIRNAME}/{name}",
            "page_url": url,
        },
        ensure_ascii=False,
    )
