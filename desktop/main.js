const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");

const HOST = "127.0.0.1";
const PORT = 8000;
const ORIGIN = "http://" + HOST + ":" + PORT;

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
  return spawn(py, ["-m", "uvicorn", "app.main:app", "--host", HOST, "--port", String(PORT)], {
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
  throw new Error("Backend did not become ready on 127.0.0.1:8000");
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
  mainWindow.loadURL(ORIGIN);
  mainWindow.on("closed", function () { mainWindow = null; });
}

function stopBackendIfStarted() {
  if (!startedBackend || !backendProc) return;
  const child = backendProc;
  startedBackend = false;
  backendProc = null;
  try { child.kill(); } catch (e) {}
}

app.whenReady().then(async function () {
  try { await ensureBackend(); } catch (err) { console.error(err); }
  createWindow();
  app.on("activate", function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", function () {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", stopBackendIfStarted);
app.on("quit", stopBackendIfStarted);
