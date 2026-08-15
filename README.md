# Grok 助手（本地 v0.3）

一个可在本机运行的 Grok 风格对话助手：浏览器里聊天，模型可以循环调用工具（搜索、读/写文件、抓取网页、记忆），直到给出最终回答。v0.3 支持把本地文件上传到 `workspace/uploads/`，并用 `./start.sh` 一键启动。

- 后端：FastAPI + xAI Grok API（OpenAI 兼容 Chat Completions）
- 前端：单页中文聊天界面（左侧多会话）
- 工具在项目下的 `workspace/` 目录执行（本地开发沙箱，**不是**安全隔离环境）
- 会话与记忆持久化在 `data/`（运行时自动创建，默认不入库）
- 上传文件保存在 `workspace/uploads/`（已 gitignore）

## 获取 API Key

1. 打开 [xAI Console](https://console.x.ai) 注册 / 登录
2. 创建 API Key
3. 复制 `.env.example` 为 `.env`（`./start.sh` 在缺失时会自动复制），填入密钥：

```bash
cp .env.example .env
# 编辑 .env，写入：
# XAI_API_KEY=xai-...
```

默认模型为 `grok-4.6`（支持 function calling）。如需更换，在 `.env` 中设置 `GROK_MODEL`，例如 `grok-4`、`grok-4.5`、`grok-3`。

API 基址：`https://api.x.ai/v1`

## 运行（推荐）

需要 Python 3.11+。在项目根目录执行：

```bash
cd grok-assistant
./start.sh
```

`start.sh` 会：没有 venv 时创建、安装依赖、缺少 `.env` 时从 `.env.example` 复制，然后在 `127.0.0.1:8000` 启动 uvicorn。

浏览器打开：http://127.0.0.1:8000

也可以：

```bash
python -m app
```

或手动：

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## 上传文件

输入框左侧的回形针可把本地小文件上传到沙箱 `workspace/uploads/`（约 5MB 上限）。

- 文本文件可随后让模型用 `read_file` 读取（路径形如 `uploads/foo.txt`）
- `pdf` / `png` / `jpg` 仅作为二进制保存，不解析图片内容
- 文件名中的 `../` 等路径穿越会被拒绝
- 上传成功后界面显示芯片，并自动插入一句 `已上传 uploads/foo.txt`，发送后模型即可看到路径

接口：`POST /api/upload`（multipart 字段名 `file`）

## 内置工具

| 工具 | 作用 |
|------|------|
| `web_search` | 公开网页搜索，返回标题 / 链接 / 摘要（`ddgs`，失败则抓取 DuckDuckGo HTML） |
| `fetch_url` | GET 公开 http(s) URL，去掉 script/style 后返回正文（约 30k 字符）；拒绝 localhost / 内网 / `file://` |
| `list_dir` | 列出 `workspace/` 下的文件和子目录 |
| `read_file` | 读取 `workspace/` 内文本文件；越界、缺失、二进制会报错 |
| `write_file` | 在 `workspace/` 内创建或覆盖文本文件（约 200KB 上限）；禁止路径穿越 |
| `run_command` | 在 `workspace/` 下执行 shell，超时 15s，捕获 stdout/stderr/退出码 |
| `memory_write` | 把用户偏好 / 长期事实写入 `data/memory.json`（跨会话） |
| `memory_read` | 检索记忆；`query` 为空返回最近若干条 |

`run_command` 会拦截 `rm -rf /`、`sudo`、`mkfs` 等明显危险模式。这只是本地开发保护，**不是 jail**：进程、网络、宿主机并未隔离。

写文件请优先让模型调用 `write_file`，而不是用 shell 重定向。

工具循环最多 8 轮。界面会显示「正在调用工具…」以及每个工具的简要结果（默认走 `/api/chat/stream`）。

## 多会话

会话保存在 `data/sessions/<id>.json`（模型消息 + 界面回合）。

- 左侧边栏列出历史对话，点「新对话」创建并切换
- `GET /api/sessions` 列出；`POST /api/sessions` 创建
- `POST /api/sessions/{id}/select` 切换当前会话
- `DELETE /api/sessions/{id}` 删除
- 发消息时可带 `session_id`；不带则使用当前会话
- `POST /api/clear` 清空**当前**会话内容（不删除会话文件）

刷新页面会从磁盘恢复当前会话。

## 记忆

`data/memory.json` 保存一个简短事实列表。每轮对话会把最近约 20 条注入系统提示；模型也可主动调用 `memory_write` / `memory_read`。记忆跨会话共享。

## 示例提示词

打开页面后可以直接点卡片，或粘贴：

1. **帮我搜一下 xAI Grok API 怎么做 function calling**  
   模型应调用 `web_search`，再根据结果说明。

2. **读一下 workspace 里的 sample.txt**  
   模型应调用 `read_file`（路径如 `sample.txt`）。

3. **在 workspace 里写一个 notes.txt，写上三行待办事项**  
   模型应调用 `write_file`。

4. **记住：我叫小康，喜欢简洁的中文回答**  
   模型应调用 `memory_write`。之后新对话里也应能用到这条记忆。

5. **抓取 https://example.com 的页面摘要**  
   模型应调用 `fetch_url`。

6. **（先点回形针上传一个 txt）请读一下我刚上传的文件**  
   模型应调用 `read_file`，路径形如 `uploads/foo.txt`。

## 运行测试

```bash
cd grok-assistant
source venv/bin/activate
pytest
```

测试覆盖：路径穿越拦截、`write_file`/`read_file` 往返、危险命令拦截、`fetch_url` 拒绝 localhost / 内网、上传保存路径必须落在 `workspace/uploads/`、拒绝 `../` 文件名。

## 项目结构

```
grok-assistant/
  README.md
  start.sh             # 一键启动（推荐）
  requirements.txt
  pytest.ini
  .env.example
  .gitignore
  app/__main__.py      # python -m app
  app/main.py          # FastAPI：页面 + 会话 API + 上传 + /api/chat + SSE
  app/agent.py         # Grok 工具循环
  app/tools.py         # 搜索 / 文件 / 命令 / 抓取网页 / 上传
  app/memory.py        # 持久记忆
  app/sessions.py      # 多会话落盘
  app/static/index.html
  tests/test_tools.py
  workspace/sample.txt # 演示用中文文本
  workspace/uploads/   # 用户上传（已 gitignore）
  data/                # 运行时创建（已 gitignore）
```

## 常见问题

- **未配置 XAI_API_KEY**：服务能启动，但发消息会返回明确错误。按上面步骤配置 `.env` 后重启。
- **鉴权失败 / 模型不存在**：检查 key 是否有效，或把 `GROK_MODEL` 改成控制台里可用的模型。
- **搜索为空**：DuckDuckGo 偶发限流，可换关键词重试。
- **data/ 目录**：首次启动或首次写记忆/会话时自动创建，无需手工建目录。
- **上传失败**：确认文件名不含 `../`，体积不超过约 5MB；二进制除 pdf/png/jpg 外会被拒绝。
