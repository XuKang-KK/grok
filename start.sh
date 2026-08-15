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
  echo "已复制 .env.example 为 .env。请编辑 .env 填入 XAI_API_KEY 后再使用。"
fi

echo "启动 Grok 助手：http://127.0.0.1:8000"
exec uvicorn app.main:app --host 127.0.0.1 --port 8000
