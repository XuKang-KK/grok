#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ ! -d venv ]]; then
  echo "创建虚拟环境…"
  python3 -m venv venv
fi

# shellcheck disable=SC1091
source venv/bin/activate

echo "安装依赖…"
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已复制 .env.example 为 .env。请编辑 .env 或在网页设置中填入 XAI_API_KEY。"
fi

if ! python -c "
from playwright.sync_api import sync_playwright
import os
p = sync_playwright().start()
try:
    path = p.chromium.executable_path
    ok = bool(path and os.path.exists(path))
finally:
    p.stop()
raise SystemExit(0 if ok else 1)
" >/dev/null 2>&1; then
  echo "安装 Playwright Chromium…"
  python -m playwright install chromium
fi

echo "启动 Grok 助手：http://127.0.0.1:8000"
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
