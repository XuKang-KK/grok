# KK AI助手 · 手机端（Android / iOS）v1

**v1 可安装手机客户端就是 PWA**，与网页同一套 UI，默认中文，可切换英文。

不拆第二套前端，也不在设置里填写服务器地址：手机打开的就是同源站点。

## 现在怎么用（推荐）

1. 电脑上启动后端：仓库根目录 ./start.sh（127.0.0.1:8000）。
2. 若要让手机访问，需让服务监听局域网（例如 HOST=0.0.0.0），并用 https 或受信任内网地址。
3. 手机浏览器打开该站点：
   - Android Chrome：菜单 → 添加到主屏幕 / 安装应用
   - iOS Safari：分享 → 添加到主屏幕
4. 主屏幕图标为金星深色底；独立窗口打开。语言切换与网页相同。

PWA 资源：manifest.webmanifest（name: KK AI助手，lang: zh-CN）、/sw.js 最小 Service Worker、app/static/icons/ 下 PNG。

这就是 mobile client v1。

## 以后可选：Capacitor 薄封装

若以后要上 Play / App Store，可以在 mobile/ 下加 Capacitor，只包现有网页，不要再做一套界面。

- Android 可在 Windows / macOS / Linux 打 debug 包。
- iOS 真机 / 上架必须：一台 Mac、Xcode，以及付费的 Apple Developer 账号。未签名的 iOS 包无法按商店方式分发。

## 不要做的事

- 不要为了手机再写一套聊天前端
- 不要在 v1 里加远程服务器地址去连不明主机（同源 PWA 最简单）
- 不要削弱 workspace 沙箱

商店上架需要签名、隐私问卷与审核，不在 v1.2 范围。未签名桌面 / 手机包仅供本机或内测。
