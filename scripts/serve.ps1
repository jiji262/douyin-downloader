# 启动本地 REST API（默认 127.0.0.1:8000）
# 用法：.\scripts\serve.ps1
param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8010
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = Join-Path $PWD ".venv\Scripts\python.exe"
Write-Host "REST API: http://${HostName}:${Port}/api/v1/health" -ForegroundColor Cyan
& $py run.py -c config.yml --serve --serve-host $HostName --serve-port $Port
exit $LASTEXITCODE
