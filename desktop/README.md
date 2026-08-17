# KK AI助手 · 桌面端（Windows / Mac）

这是现有 Python 网页应用的 Electron 薄封装，不会改写后端。窗口加载本机后端（默认 127.0.0.1:8000）。

If the backend is already running, the window reuses it.
Backend failure shows a Chinese error page instead of a blank window.
## 一键启动

One-command launch after the Python environment exists: use the start script in the desktop folder.

## 要求

- Node.js 18+
- Python 3.11+
- 根目录已有可用的 venv

## 打包

产物在 desktop/dist/。Windows NSIS、macOS dmg 与 zip。未签名。

## 注意（未签名构建）

不含 Apple 公证 / Developer ID，也不含 Windows Authenticode。本仓库不能替你购买证书或上架商店。不要削弱 Python 沙箱。

## 与网页 / 手机的关系

同一套 UI。手机请用 PWA，见仓库 mobile/README.md。
