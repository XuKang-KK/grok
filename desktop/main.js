const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

const BIND_HOST = process.env.HOST || "127.0.0.1";
const PORT = parseInt(process.env.PORT || "8000", 10) || 8000;
const CONNECT_HOST = (BIND_HOST === "0.0.0.0" || BIND_HOST === "::" || BIND_HOST === "[::]")
  ? "127.0.0.1"
  : BIND_HOST;
const ORIGIN = "http://" + CONNECT_HOST + ":" + PORT;

let mainWindow = null;
let backendProc = null;
let startedBackend = false;

function projectRoot() {
  return path.resolve(__dirname, "..");
}

function healthCheck(timeoutMs) {
  timeoutMs = timeoutMs || 800;
  return new Promise(function (resolve) {
    const req = http.get(ORIGIN + "/api/health", function (res) {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.on("error", function () { resolve(false); });
    req.setTimeout(timeoutMs, function () { req.destroy(); resolve(false); });
  });
}

function findPython(root) {
  if (process.platform === "win32") {
    const venv = path.join(root, "venv", "Scripts", "python.exe");
    if (fs.existsSync(venv)) return venv;
    return "python";
  }
  const venv = path.join(root, "venv", "bin", "python");
  if (fs.existsSync(venv)) return venv;
  return "python3";
}

function spawnBackend(root) {
  const startSh = path.join(root, "start.sh");
  if (process.platform !== "win32" && fs.existsSync(startSh)) {
    return spawn("bash", [startSh], { cwd: root, stdio: "inherit", env: process.env });
  }
  const py = findPython(root);
  return spawn(py, ["-m", "uvicorn", "app.main:app", "--host", BIND_HOST, "--port", String(PORT)], {
    cwd: root,
    stdio: "inherit",
    env: process.env,
    windowsHide: true
  });
}

async function ensureBackend() {
  if (await healthCheck()) {
    startedBackend = false;
    return;
  }
  const root = projectRoot();
  backendProc = spawnBackend(root);
  startedBackend = true;
  backendProc.on("exit", function () { backendProc = null; });
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    await new Promise(function (r) { setTimeout(r, 500); });
    if (await healthCheck()) return;
  }
  throw new Error("后端未在 " + CONNECT_HOST + ":" + PORT + " 就绪。请确认已创建 venv 并安装 requirements.txt。");
}

function errorPageHtml(err) {
  const raw = err && err.message ? String(err.message) : "";
  const msg = raw.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><title>KK AI助手</title>" +
    "<style>html,body{height:100%;margin:0}body{background:#09090b;color:#f4f1ea;" +
    "font-family:\"PingFang SC\",\"Noto Sans SC\",system-ui,sans-serif;display:flex;" +
    "align-items:center;justify-content:center}.box{max-width:460px;padding:32px}" +
    "h1{color:#f4c56a;font-size:20px;margin:0 0 12px}p{color:#9a958b;line-height:1.65;margin:0 0 10px}" +
    "code{color:#f4c56a}</style></head><body><div class=\"box\">" +
    "<h1>无法连接后端</h1>" +
    "<p>KK AI助手没能在 <code>" + CONNECT_HOST + ":" + PORT + "</code> 启动 Python 服务，所以窗口显示这条说明，而不是空白页。</p>" +
    "<p>请先在项目根目录准备好虚拟环境并安装依赖，然后重试：</p>" +
    "<p><code>./start.sh</code>（macOS / Linux）或 <code>.\\start.ps1</code>（Windows）</p>" +
    "<p>也可以先打开 <code>" + ORIGIN + "</code> 确认网页能用，再启动桌面端。</p>" +
    (msg ? "<p>" + msg + "</p>" : "") +
    "</div></body></html>";
}

function showBackendError(err) {
  if (!mainWindow) return;
  mainWindow.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(errorPageHtml(err)));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    title: "KK AI助手",
    width: 1280,
    height: 840,
    minWidth: 390,
    minHeight: 640,
    backgroundColor: "#09090b",
    autoHideMenuBar: true,
    icon: path.join(__dirname, "icon.png"),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  });
  mainWindow.setTitle("KK AI助手");
  mainWindow.on("closed", function () { mainWindow = null; });
}

async function loadAppOrError() {
  try {
    await ensureBackend();
    if (mainWindow) mainWindow.loadURL(ORIGIN);
  } catch (err) {
    console.error(err);
    showBackendError(err);
  }
}

function stopBackendIfStarted() {
  if (!startedBackend || !backendProc) return;
  const child = backendProc;
  startedBackend = false;
  backendProc = null;
  try { child.kill(); } catch (e) {}
}

app.whenReady().then(async function () {
  createWindow();
  await loadAppOrError();
  app.on("activate", function () {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
      loadAppOrError();
    }
  });
});

app.on("window-all-closed", function () {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopBackendIfStarted);
app.on("quit", stopBackendIfStarted);
