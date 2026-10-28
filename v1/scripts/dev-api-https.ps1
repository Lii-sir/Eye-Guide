$ErrorActionPreference = "Stop"

# 这个脚本做三件事：
# 1. 先生成或刷新本地开发证书
# 2. 清理 8010 端口上残留的旧服务
# 3. 用 HTTPS 启动 EyeGuide API，便于手机浏览器调用摄像头

$projectRoot = Split-Path -Parent $PSScriptRoot
$certDir = Join-Path $projectRoot ".tmp\dev-cert"
$certFile = Join-Path $certDir "localhost-cert.pem"
$keyFile = Join-Path $certDir "localhost-key.pem"
$port = 8010

Write-Host "正在生成开发证书..."
uv run python scripts/generate_dev_cert.py

if (-not (Test-Path $certFile) -or -not (Test-Path $keyFile)) {
    throw "开发证书生成失败，未找到证书文件。"
}

$listenPids = @(
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)

if ($listenPids.Count -gt 0) {
    Write-Host "检测到旧服务占用端口 $port，正在停止进程: $($listenPids -join ', ')"
    Stop-Process -Id $listenPids -Force
    Start-Sleep -Milliseconds 500
}

Write-Host ""
Write-Host "HTTPS API 正在启动..."
Write-Host "本机访问: https://127.0.0.1:$port/mobile/"
Write-Host "手机访问: https://<你的电脑局域网IP>:$port/mobile/"
Write-Host "首次访问时，手机浏览器需要手动信任此开发证书。"
Write-Host ""

uv run uvicorn eyeguide.api.server:app `
    --host 0.0.0.0 `
    --port $port `
    --ssl-certfile $certFile `
    --ssl-keyfile $keyFile
