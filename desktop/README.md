# KK AI助手 · 桌面端（Windows / Mac）

这是现有 Python 网页应用的 **Electron 薄封装**，不会改写后端。窗口标题为 **KK AI助手**，加载 `http://127.0.0.1:8000`。

若本机该地址已经在跑（例如你先执行了仓库根目录的 `./start.sh`），桌面端会直接打开，**不会再起一份 uvicorn**。


## 要求

- Node.js 18+
- Python 3.11+
- 先在仓库根目录跑一次 ./start.sh 装好依赖

## 开发启动

进入 desktop 目录后安装依赖并启动 Electron。

    npm install
    npm start

已有服务则复用，否则拉起现有 Python 后端。

## 打包

    npm run build:win
    npm run build:mac
    npm run build

产物在 desktop/dist/。
- Windows: electron-builder win + nsis (x64)
- macOS: dmg 与 zip
- 图标：本目录 icon.png

## 注意（未签名构建）

- 不含 Apple 公证 / Developer ID，也不含 Windows Authenticode。
- macOS Gatekeeper 可能拦截未签名 app。
- Windows 可能弹出 SmartScreen 未知发布者。
- 上架商店需要开发者账号与审核，不在 v1.2 范围。
- 不要削弱 Python 沙箱。

## 与网页 / 手机的关系

同一套 UI（含中 / 英切换）。手机请用 PWA，见仓库 mobile/README.md。
