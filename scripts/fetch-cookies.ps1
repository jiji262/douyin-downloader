# 采集抖音 Cookie 并写回 config.yml
# 用法：在仓库根目录执行  .\scripts\fetch-cookies.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = Join-Path $PWD ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "未找到 .venv，请先在仓库根目录执行: uv venv .venv; uv pip install -r requirements.txt playwright fastapi uvicorn; .\.venv\Scripts\python.exe -m playwright install chromium"
}
Write-Host "将打开浏览器，请登录 douyin.com；登录完成后回到本终端按 Enter。" -ForegroundColor Cyan
& $py -m tools.cookie_fetcher --config config.yml
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Cookie 已写入 config.yml / config\cookies.json" -ForegroundColor Green
