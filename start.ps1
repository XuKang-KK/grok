# KK AI助手 — Windows 启动脚本（与 start.sh / python -m app 相同的 HOST/PORT）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (-not (Test-Path -Path "venv")) {
    Write-Host "创建虚拟环境…"
    python -m venv venv
}

$Activate = Join-Path $Root "venv\Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
}

Write-Host "安装依赖…"
pip install -r requirements.txt

if (-not (Test-Path -Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已复制 .env.example 为 .env。请编辑 .env 或在网页设置中填入各提供商密钥。"
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $name = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (
            ($val.StartsWith('"') -and $val.EndsWith('"')) -or
            ($val.StartsWith("'") -and $val.EndsWith("'"))
        ) {
            if ($val.Length -ge 2) { $val = $val.Substring(1, $val.Length - 2) }
        }
        if (-not $name) { return }
        if (-not [Environment]::GetEnvironmentVariable($name)) {
            [Environment]::SetEnvironmentVariable($name, $val, "Process")
        }
    }
}

Import-DotEnv (Join-Path $Root ".env")

# $Host is reserved in PowerShell — do not use that name.
$BindHost = if ($env:HOST) { $env:HOST } else { "127.0.0.1" }
$BindPort = if ($env:PORT) { $env:PORT } else { "8000" }
$Token = if ($env:KK_ACCESS_TOKEN) { $env:KK_ACCESS_TOKEN } elseif ($env:ACCESS_TOKEN) { $env:ACCESS_TOKEN } else { "" }

if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::" -or $BindHost -eq "[::]") {
    if (-not $Token) {
        Write-Host "警告：HOST=$BindHost 将把服务暴露到局域网，但未设置 KK_ACCESS_TOKEN。任何能访问该端口的人都可以调用 API、读写 workspace。请在 .env 中设置 KK_ACCESS_TOKEN，或在网页设置里填写访问口令。"
    }
}

$chromiumOk = $false
try {
    python -c @"
from playwright.sync_api import sync_playwright
import os
p = sync_playwright().start()
try:
    path = p.chromium.executable_path
    ok = bool(path and os.path.exists(path))
finally:
    p.stop()
raise SystemExit(0 if ok else 1)
"@
    if ($LASTEXITCODE -eq 0) { $chromiumOk = $true }
} catch {
    $chromiumOk = $false
}
if (-not $chromiumOk) {
    Write-Host "安装 Playwright Chromium…"
    python -m playwright install chromium
}

Write-Host "启动 KK AI助手：http://${BindHost}:${BindPort}"
if ($BindHost -eq "0.0.0.0" -or $BindHost -eq "::" -or $BindHost -eq "[::]") {
    Write-Host "手机 / 局域网请访问 http://<本机局域网IP>:${BindPort}，并设置 KK_ACCESS_TOKEN。"
}

uvicorn app.main:app --host $BindHost --port $BindPort
