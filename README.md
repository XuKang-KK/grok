# KK AI助手（本地 v1.4.0）

一个可在本机运行的完整 KK AI助手：浏览器里聊天，模型循环调用工具直到给出最终回答。v1.4.0 支持网页 / 桌面 / 手机（PWA），界面默认中文、可切换英文。

**对外 = 右侧 GPT / Claude / Grok，后台中转站，不展示价格。** 对话一律走 CCAPI；右侧不出现 xAI / OpenAI / Anthropic / 中转站页签，也不显示基址或价格。

- 后端：FastAPI；默认全部聊天经 CCAPI 中转（`https://api.ccapi.ai/v1`，设置可改）。高级直连仍可走 xAI / OpenAI / Anthropic，但默认 UI 不会切到这三家。
- 前端：单页中文深色聊天界面（左侧多会话，右侧 GPT / Claude / Grok 模型轨，设置 / 例程抽屉）
- 工具在项目下的 `workspace/` 目录执行（本地开发沙箱，**不是**安全隔离环境）
- 会话、记忆、设置、例程持久化在 `data/`（已 gitignore，切勿提交密钥）
- 上传文件在 `workspace/uploads/`，生成图在 `workspace/generated/`

## 对外模型轨与后台中转站

普通用户只在右侧选 **GPT / Claude / Grok** 和一个模型（顶栏徽章显示友好名，例如 `GPT-5.6 Terra`）。**不展示价格**，也不展示 xAI / OpenAI / Anthropic /「中转站」Tab 或基址。

所有默认对话走 **CCAPI 中转**（`provider=ccapi`，基址默认 `https://api.ccapi.ai/v1`，设置可覆盖）。管理员在设置里粘贴一枚中转站密钥（`CCAPI_API_KEY` / `ccapi_api_key`）。端用户只选家族 + 模型。

`GET /api/models` 在已配置密钥时请求 `{base}/models`（8 秒超时），把 id 合并进 GPT / Claude / Grok；Gemini / Kimi / 图像 / 视频不出现。拉取失败或未配置密钥时使用无价格的本地回退列表。响应形如 `{ok, source: live|fallback, families, provider: ccapi, model, family, has_relay_key}`，不含 `price` / `pricing` / `cost` / `fee`。

回退目录（聊天家族，来自 [CCAPI 价格页](https://ccapi.us/pricing/)）：

| 家族 | 模型 id |
|------|---------|
| **GPT** | `gpt-5-mini`、`gpt-5.1`、`gpt-5.2`、`gpt-5.3-codex`、`gpt-5.4`、`gpt-5.4-mini`、`gpt-5.4-nano`、`gpt-5.5`、`gpt-5.6-luna`、`gpt-5.6-sol`、`gpt-5.6-terra`（默认） |
| **Claude** | `claude-haiku-4-5-20251001`、`claude-opus-4-6` 及 high/low/max/medium/thinking、`claude-opus-4-7` 及变体、`claude-opus-4-8` 及变体、`claude-opus-5`、`claude-sonnet-4-6`、`claude-sonnet-5`、`cursor-opus-4-8` |
| **Grok** | `grok-4.5` |

`PUT /api/model` 传 `{family, model}` 时写入 `provider=ccapi` + 该模型 id。新安装默认：ccapi + `gpt-5.6-terra`（价格页没有裸的 `gpt-5.6`）。

设置抽屉默认只露出中转站密钥和可选基址。xAI / OpenAI / Anthropic 直连密钥收在「高级直连」里；若 `data/settings.json` 里已经保存了这三家之一，旧路径仍可用，但默认 UI 不会再切过去。

密钥写在 gitignored 的 `data/settings.json`。环境变量 `CCAPI_API_KEY` 作为回退。界面**永不回显**密钥。

1. **管理员必须做的**：到 CCAPI 控制台复制 token，在设置里填「中转站密钥」，或写入 `.env` 的 `CCAPI_API_KEY`。地址默认 `https://api.ccapi.ai/v1`。
2. 高级直连（可选）：[xAI](https://console.x.ai) / [OpenAI](https://platform.openai.com) / [Anthropic](https://console.anthropic.com) 密钥在设置的「高级直连」里。
3. **无需重启**：保存设置后立即生效。

```bash
cp .env.example .env
# CCAPI_API_KEY=...          # 管理员必填，否则右侧模型能看、聊天会 503
# CCAPI_BASE_URL=https://api.ccapi.ai/v1
# XAI_API_KEY=xai-...        # 仅高级直连
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...
```

未配置中转站密钥时，聊天接口返回 **503**。界面只显示一行：管理员尚未配置中转站密钥（设置里填写）。

图像生成：默认走中转站同一基址的 `/images/generations`。失败或未配置时，若已保存 xAI 密钥则回退 xAI（默认 `grok-imagine-image-2.0`）。

## 运行（推荐）

需要 Python 3.11+。在项目根目录：

```bash
cd grok-assistant
./start.sh
```

`start.sh`（Windows 用 `start.ps1`）会：创建 venv、安装依赖、缺少 `.env` 时复制示例、若本机还没有 Chromium 则执行 `python -m playwright install chromium`，然后按 `HOST`/`PORT` 启动（默认 `127.0.0.1:8000`）。

浏览器打开：http://127.0.0.1:8000

手机要连电脑时，在 `.env` 设 `HOST=0.0.0.0` 并设置 `KK_ACCESS_TOKEN`，不要把 `0.0.0.0` 当成默认值。

## 网页 / 桌面 / 手机 / 语言

| 端 | 怎么用 |
|----|--------|
| **网页** | 本仓库就是 Web 应用。窄屏下侧栏与模型轨会收起。 |
| **桌面** | `desktop/` 下的 Electron 封装。见 `desktop/README.md`：`npm install` 后启动，可打 Windows NSIS 与 macOS dmg/zip。未签名。 |
| **手机** | **PWA 即 mobile client v1**（同一套 UI）。Android Chrome / iOS Safari「添加到主屏幕」。见 `mobile/README.md`。不拆第二套前端，也不填远程服务器地址。 |
| **语言** | 默认中文。顶栏「EN / 中文」与设置里的「界面语言」可切换。选择写入 localStorage（kk-lang）并保存到 data/settings.json 的 language 字段。GPT / Claude / Grok 品牌名不翻译。 |

也可以：

```bash
python -m app
```

`python -m app` 与启动脚本一样读取 `HOST` / `PORT`。

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
- `generate_image(prompt)`：中转站优先走其 `/images/generations`；否则调用 xAI（`grok-imagine-image-2.0`，优先 `b64_json`），保存到 `workspace/generated/`，对话中展示。

## 内置工具

| 工具 | 作用 |
|------|------|
| `web_search` | 公开网页搜索 |
| `fetch_url` | GET 公开 http(s)，拒绝内网 / file:// |
| `list_dir` / `read_file` / `write_file` | workspace 文件 |
| `run_command` | workspace shell；硬拦截 + 中风险批准 |
| `memory_write` / `memory_read` | `data/memory.json` |
| `browser_*` | Playwright Chromium |
| `generate_image` | 中转站或 xAI 图像生成 |
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

覆盖：路径穿越、危险命令硬拦截、中风险分类、`fetch_url` / 浏览器 URL 封锁、上传路径、cron 解析、子助手不能递归、设置接口不泄露密钥、MCP 示例配置可干净加载、`family_for_model` 分组、`/api/models` 的 GPT/Claude/Grok 家族且无价格字段、无网络时的回退列表、缺中转站/OpenAI/Anthropic 密钥时的中文 503、i18n 中英文字典、语言设置默认中文、无口令时 API 仍开放、有口令时 chat/settings 先 401 再带 header 通过。单元测试不访问真实 CCAPI 网络。

## 项目结构

```
grok-assistant/
  README.md
  start.sh
  start.ps1            # Windows，同样读取 HOST/PORT
  mcp.example.json     # 可解析的 MCP 示例（默认不启用）
  requirements.txt
  app/main.py          # FastAPI：会话 / 上传 / SSE / 设置 / 例程 / 批准 / 就绪
  app/auth.py          # 可选访问口令与 CORS
  app/agent.py         # 工具循环、子助手、批准等待（xAI/OpenAI）
  app/anthropic_agent.py # Anthropic Messages 工具循环
  app/providers.py     # GPT/Claude/Grok 家族目录与消息转换
  app/tools.py         # 沙箱工具 + 动态 schema
  app/settings.py      # data/settings.json（每请求重读，含中转站密钥与基址）
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


## 上线前你必须自己做的

本仓库**不能**替你完成这些事（也没有假装已经完成）：

1. **中转站密钥**：管理员必须自己申请 CCAPI token，写入设置或 `.env` 的 `CCAPI_API_KEY`。我们没有、也不会去填你的密钥。不要提交 `data/settings.json` 或 `.env`。
2. **局域网 / 手机访问**：默认只监听 `127.0.0.1`。手机要连这台电脑，请自己设 `HOST=0.0.0.0`，并设置 `KK_ACCESS_TOKEN`（或网页设置里的访问口令）。
3. **PWA 在局域网用 HTTPS**：浏览器要求安全上下文才能完整「添加到主屏幕」。请自己用 Caddy / nginx 反代并配证书（内网可用 mkcert）。本仓库不代签证书。
4. **Apple / Windows 签名与上架**：需要付费的 Apple Developer、Windows Authenticode / 商店账号。这里只有未签名的 Electron 打包脚本，不会代你签名或上架。

## 本版本已处理的

- `HOST` / `PORT` 从环境变量和 `.env` 读取（默认 `127.0.0.1:8000`），`start.sh`、`start.ps1`、`python -m app` 一致
- 可选访问口令（`KK_ACCESS_TOKEN` / `ACCESS_TOKEN` / 设置页），保护 `/api/*`（`GET /api/health` 除外）；`POST /api/login` 写 httpOnly cookie `kk_token`
- CORS：同源始终允许；`KK_CORS_ORIGINS` 可追加来源，且带 credentials
- `/api/health` 增加 `version` / `host` / `bind` / `auth_required` / `has_any_provider_key` / `chromium`；`GET /api/ready` 列出未就绪项
- 桌面端后端启动失败时显示中文错误页，而不是空白窗口
- 设置里可写 / 清除口令且永不回显
- `HOST=0.0.0.0` 且未设口令时，启动脚本和后端会打出中文警告

## 常见问题

- **未配置中转站密钥**：服务能启动，右侧仍显示 GPT / Claude / Grok 回退列表。打开「设置」填写中转站密钥后立即生效，不必重启。界面不会把价格或三家官方 Key 表单当作主路径。
- **鉴权失败 / 模型不存在**：检查 key，或把模型改成控制台里可用的 id。
- **Playwright 失败**：执行 `python -m playwright install chromium`。
- **MCP 连不上**：设置面板会显示错误；`enabled: false` 时必须干净（无工具、无异常）。
- **图像生成失败**：确认账号开通了 Imagine / `images/generations`；默认模型 `grok-imagine-image-2.0`。
- **不要提交** `.env` 或 `data/`（含密钥、会话、例程）。
