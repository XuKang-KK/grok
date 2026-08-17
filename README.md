# KK AI助手（本地 v1.2）

一个可在本机运行的完整 KK AI助手：浏览器里聊天，模型循环调用工具直到给出最终回答。v1.2 支持网页 / 桌面 / 手机（PWA），界面默认中文、可切换英文；右侧模型选择器支持 xAI / OpenAI / Anthropic。

- 后端：FastAPI；xAI / OpenAI 走 OpenAI 兼容 Chat Completions，Anthropic 走 Messages API + 工具循环（默认 `grok-4.6`）
- 前端：单页中文深色聊天界面（左侧多会话，右侧「模型」面板，设置 / 例程抽屉）
- 工具在项目下的 `workspace/` 目录执行（本地开发沙箱，**不是**安全隔离环境）
- 会话、记忆、设置、例程持久化在 `data/`（已 gitignore，切勿提交密钥）
- 上传文件在 `workspace/uploads/`，生成图在 `workspace/generated/`

## 获取 API Key（多提供商）

支持三个提供商，聊天对话框**右侧「模型」面板**可切换，对下一条消息生效（同时写入当前会话和默认设置）。

| 提供商 | 基址 | 预置模型 |
|--------|------|----------|
| **xAI** | `https://api.x.ai/v1`（OpenAI 兼容） | `grok-4.6`（默认）、`grok-4.5` |
| **OpenAI** | `https://api.openai.com/v1`（OpenAI SDK + tools） | `gpt-5.6`、`gpt-5`、`gpt-5-mini`、`gpt-5-chat-latest` |
| **Anthropic** | `https://api.anthropic.com`（Messages API + tools，**不**兼容 OpenAI） | `claude-opus-5`、`claude-sonnet-5`、`claude-fable-5`、`claude-haiku-4-5` |

右侧面板也可以输入自定义模型 id（仍走当前提供商）。

密钥按提供商分别保存在 gitignored 的 `data/settings.json`：`xai_api_key` / `openai_api_key` / `anthropic_api_key`。环境变量 `XAI_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 作为回退。设置抽屉可分别粘贴三个密钥；已保存则留空不修改。界面**永不回显**密钥。

1. 到对应控制台申请密钥：[xAI](https://console.x.ai) / [OpenAI](https://platform.openai.com) / [Anthropic](https://console.anthropic.com)
2. 任选其一（**无需重启**）：
   - 启动后点右上角「设置」，填入对应提供商密钥并保存
   - 或复制 `.env.example` 为 `.env` 后填入

```bash
cp .env.example .env
# XAI_API_KEY=xai-...
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
# GROK_MODEL=grok-4.6
```

当前选中的提供商没有密钥时，聊天接口返回 **503**，中文错误会点名该提供商。

图像生成仍只走 xAI（`POST /v1/images/generations`，默认 `grok-imagine-image-2.0`）。当前对话不是 xAI 时，只要已保存 xAI 密钥仍可生图。

## 运行（推荐）

需要 Python 3.11+。在项目根目录：

```bash
cd grok-assistant
./start.sh
```

`start.sh` 会：创建 venv、安装依赖、缺少 `.env` 时复制示例、若本机还没有 Chromium 则执行 `python -m playwright install chromium`，然后在 `127.0.0.1:8000` 启动。

浏览器打开：http://127.0.0.1:8000

## 网页 / 桌面 / 手机 / 语言

| 端 | 怎么用 |
|----|--------|
| **网页** | 本仓库就是 Web 应用。窄屏下侧栏与模型轨会收起。 |
| **桌面** | `desktop/` 下的 Electron 封装。见 `desktop/README.md`：`npm install` 后启动，可打 Windows NSIS 与 macOS dmg/zip。未签名。 |
| **手机** | **PWA 即 mobile client v1**（同一套 UI）。Android Chrome / iOS Safari「添加到主屏幕」。见 `mobile/README.md`。不拆第二套前端，也不填远程服务器地址。 |
| **语言** | 默认中文。顶栏「EN / 中文」与设置里的「界面语言」可切换。选择写入 localStorage（kk-lang）并保存到 data/settings.json 的 language 字段。模型 id 与提供商名称不翻译。 |

也可以：

```bash
python -m app
```

## Playwright 浏览器

工具：`browser_open` / `browser_snapshot` / `browser_click` / `browser_type` / `browser_screenshot`。

- 只允许 `http(s)`；默认拒绝 `file://`、localhost、私有 IP（与 `fetch_url` 相同）
- 设置里可打开「允许浏览器访问 localhost / 内网」（`allow_local_browser`），**仍然禁止 file://**
- Chromium 惰性启动，空闲约 3 分钟后关闭
- 截图保存在 `workspace/screenshots/`

首次使用若未装浏览器：

```bash
source venv/bin/activate
python -m playwright install chromium
```

## MCP

从 gitignored 的 `data/mcp.json` 加载服务器。仓库根目录有可解析的示例 `mcp.example.json`（默认 `enabled: false`，不启动进程，工具列表为空且无错误）。

```bash
mkdir -p data
cp mcp.example.json data/mcp.json
# 如需启用内置回声示例，把 echo.servers[0].enabled 改为 true
```

示例服务器命令：`python3 -m app.mcp_echo`（最小 stdio JSON-RPC，提供 `echo` 工具）。启用后模型侧工具名为 `mcp_echo_echo`。

设置面板会列出每个服务器的连接状态和错误。配置解析失败时不会拖垮主程序。

支持 `{ "servers": [ { "name", "command", "args", "env", "enabled" } ] }`，也兼容 `{ "mcpServers": { "name": { "command", "args" } } }`。

## 例程

右上角「例程」：名称 + cron + 提示词。时区固定 **Asia/Shanghai**。

- 持久化 `data/routines.json`
- 可暂停 / 恢复 / 删除
- 后台调度到点后走同一套 agent 循环（中风险命令在例程里默认拒绝）
- 上次运行结果写回列表

Cron 为 5 段：`分 时 日 月 周`，例如每天 9:00：`0 9 * * *`。

## 危险命令批准

`run_command` 仍硬拦截 `rm -rf /`、`sudo`、`mkfs`、`curl|sh` 等。

中风险（`rm`、`chmod`、写入沙箱外路径等）会通过 SSE 发出 `approval_required`。界面出现批准 / 拒绝按钮；约 60 秒未操作视为拒绝。

接口：`POST /api/approvals/{id}`，body `{"decision":"approve"}` 或 `"deny"`。

## 视觉

上传 `png` / `jpg` / `webp` 后，下一轮对话会把图片以 `image_url` data URL 发给 xAI / OpenAI（`detail: high`）。Anthropic 会转成 Messages 的 image 块；无法转换时返回明确错误。不必再 `read_file` 二进制图。

## 子助手与生图

- `delegate_task(goal, context)`：嵌套工具循环，最多 5 轮，**不能再委派**，并发上限 2。界面工具芯片带「子助手」。
- `generate_image(prompt)`：调用 xAI ` /images/generations`（`grok-imagine-image-2.0`，优先 `b64_json`），保存到 `workspace/generated/`，对话中展示。

## 内置工具

| 工具 | 作用 |
|------|------|
| `web_search` | 公开网页搜索 |
| `fetch_url` | GET 公开 http(s)，拒绝内网 / file:// |
| `list_dir` / `read_file` / `write_file` | workspace 文件 |
| `run_command` | workspace shell；硬拦截 + 中风险批准 |
| `memory_write` / `memory_read` | `data/memory.json` |
| `browser_*` | Playwright Chromium |
| `generate_image` | xAI 图像生成 |
| `delegate_task` | 子助手 |
| `mcp_*` | 来自 `data/mcp.json` 的动态工具 |

默认聊天走 `/api/chat/stream`，工具芯片实时出现。工具循环最多 8 轮（子助手 5 轮）。

## 示例提示词

打开页面可点卡片，或粘贴：

1. **帮我搜一下 xAI Grok API 怎么做 function calling** — `web_search`
2. **读一下 workspace 里的 sample.txt** — `read_file`
3. **在 workspace 里写一个 notes.txt，写上三行待办事项** — `write_file`
4. **记住：我叫小康，喜欢简洁的中文回答** — `memory_write`
5. **抓取 https://example.com 的页面摘要** — `fetch_url`
6. **用浏览器打开 https://example.com，做一次 snapshot，然后总结页面** — `browser_open` + `browser_snapshot`
7. **生成一张水墨风格的熊猫坐在竹林里的图片** — `generate_image`
8. **用子助手去搜索 Playwright 官方安装步骤，并写进 workspace/playwright-notes.txt** — `delegate_task`
9. **（先上传一张 png/jpg/webp）看看这张图里有什么** — 视觉
10. **例程**：在「例程」里加 `0 9 * * *` + 「用 web_search 总结今天的 AI 要闻，写进 workspace/daily.md」

## 运行测试

```bash
cd grok-assistant
source venv/bin/activate
pytest
```

覆盖：路径穿越、危险命令硬拦截、中风险分类、`fetch_url` / 浏览器 URL 封锁、上传路径、cron 解析、子助手不能递归、设置接口不泄露密钥、MCP 示例配置可干净加载、三家提供商目录与预置模型 id、缺 OpenAI/Anthropic 密钥时的中文 503、i18n 中英文字典、语言设置默认中文。

## 项目结构

```
grok-assistant/
  README.md
  start.sh
  mcp.example.json     # 可解析的 MCP 示例（默认不启用）
  requirements.txt
  app/main.py          # FastAPI：会话 / 上传 / SSE / 设置 / 例程 / 批准
  app/agent.py         # 工具循环、子助手、批准等待（xAI/OpenAI）
  app/anthropic_agent.py # Anthropic Messages 工具循环
  app/providers.py     # 提供商目录与消息转换
  app/tools.py         # 沙箱工具 + 动态 schema
  app/settings.py      # data/settings.json（每请求重读，三把密钥）
  app/approvals.py     # 中风险命令批准
  app/browser.py       # Playwright
  app/mcp_client.py    # stdio JSON-RPC MCP 客户端
  app/mcp_echo.py      # 示例 echo 服务器
  app/routines.py      # Asia/Shanghai cron
  app/images.py        # 图像生成
  app/static/index.html
  app/static/i18n.json
  app/static/manifest.webmanifest
  app/static/sw.js
  app/static/icons/
  desktop/             # Electron wrapper
  mobile/README.md     # PWA mobile client v1
  tests/
  workspace/
  data/                # gitignore
```

## 常见问题

- **未配置密钥**：服务能启动。打开「设置」填写对应提供商密钥后立即生效，不必重启。右侧面板会显示「该提供商密钥已保存 / 未保存」。
- **鉴权失败 / 模型不存在**：检查 key，或把模型改成控制台里可用的 id。
- **Playwright 失败**：执行 `python -m playwright install chromium`。
- **MCP 连不上**：设置面板会显示错误；`enabled: false` 时必须干净（无工具、无异常）。
- **图像生成失败**：确认账号开通了 Imagine / `images/generations`；默认模型 `grok-imagine-image-2.0`。
- **不要提交** `.env` 或 `data/`（含密钥、会话、例程）。
