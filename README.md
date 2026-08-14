# Grok 助手（本地 MVP）

一个可在本机运行的 Grok 风格对话助手：浏览器里聊天，模型可以循环调用工具（网页搜索、读文件、执行命令），直到给出最终回答。

- 后端：FastAPI + xAI Grok API（OpenAI 兼容 Chat Completions）
- 前端：单页中文聊天界面
- 工具在项目下的 `workspace/` 目录执行（本地开发沙箱，**不是**安全隔离环境）

## 获取 API Key

1. 打开 [xAI Console](https://console.x.ai) 注册 / 登录
2. 创建 API Key
3. 复制 `.env.example` 为 `.env`，填入密钥：

```bash
cp .env.example .env
# 编辑 .env，写入：
# XAI_API_KEY=xai-...
```

默认模型为 `grok-4.6`（支持 function calling）。如需更换，在 `.env` 中设置 `GROK_MODEL`，例如 `grok-4`、`grok-4.5`、`grok-3`。

API 基址：`https://api.x.ai/v1`

## 安装

需要 Python 3.11+。

```bash
cd grok-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 运行

```bash
cd grok-assistant
source venv/bin/activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开：http://127.0.0.1:8000

也可以：`python -m app.main`

## 内置工具

| 工具 | 作用 |
|------|------|
| `web_search` | 公开网页搜索，返回标题 / 链接 / 摘要（`ddgs`，失败则抓取 DuckDuckGo HTML） |
| `read_file` | 读取 `workspace/` 内文本文件；越界、缺失、二进制会报错 |
| `run_command` | 在 `workspace/` 下执行 shell，超时 15s，捕获 stdout/stderr/退出码 |

`run_command` 会拦截 `rm -rf /`、`sudo`、`mkfs` 等明显危险模式。这只是本地开发保护，**不是 jail**：进程、网络、宿主机并未隔离。

工具循环最多 8 轮。界面会显示「正在调用工具…」以及每个工具的简要结果。

## 示例提示词

打开页面后可以直接点卡片，或粘贴：

1. **帮我搜一下 xAI Grok API 怎么做 function calling**  
   模型应调用 `web_search`，再根据结果说明。

2. **读一下 workspace 里的 sample.txt**  
   模型应调用 `read_file`（路径如 `sample.txt`）。

3. **在工作目录里列出文件并创建一个 hello.txt**  
   模型应调用 `run_command`（例如 `ls`，再用重定向写入 `hello.txt`）。

## 项目结构

```
grok-assistant/
  README.md
  requirements.txt
  .env.example
  .gitignore
  app/main.py          # FastAPI：页面 + /api/chat + SSE
  app/agent.py         # Grok 工具循环
  app/tools.py         # web_search / read_file / run_command
  app/static/index.html
  workspace/sample.txt # 演示用中文文本
```

会话历史只存在内存中（单会话）。刷新页面会从服务端恢复当前会话；点「新对话」清空。

## 常见问题

- **未配置 XAI_API_KEY**：服务能启动，但发消息会返回明确错误。按上面步骤配置 `.env` 后重启。
- **鉴权失败 / 模型不存在**：检查 key 是否有效，或把 `GROK_MODEL` 改成控制台里可用的模型。
- **搜索为空**：DuckDuckGo 偶发限流，可换关键词重试。
