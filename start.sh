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
  echo "已复制 .env.example 为 .env。请编辑 .env 或在网页设置中填入各提供商密钥。"
fi

if [[ -f .env ]]; then
  set -a
  set +u
  # shellcheck disable=SC1091
  source .env
  set -u
  set +a
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
TOKEN="${KK_ACCESS_TOKEN:-${ACCESS_TOKEN:-}}"

if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" || "$HOST" == "[::]" ]]; then
  if [[ -z "$TOKEN" ]]; then
    echo "警告：HOST=${HOST} 将把服务暴露到局域网，但未设置 KK_ACCESS_TOKEN。任何能访问该端口的人都可以调用 API、读写 workspace。请在 .env 中设置 KK_ACCESS_TOKEN，或在网页设置里填写访问口令。" >&2
  fi
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

echo "启动 KK AI助手：http://${HOST}:${PORT}"
if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" || "$HOST" == "[::]" ]]; then
  echo "手机 / 局域网请访问 http://<本机局域网IP>:${PORT}，并设置 KK_ACCESS_TOKEN。"
fi
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
