@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo.
echo ========================================
echo  抖音 Cookie 采集
echo ========================================
echo  1) 将打开浏览器，请登录 douyin.com
echo  2) 登录成功后回到【本黑窗口】按 Enter
echo ========================================
echo.
".venv\Scripts\python.exe" -m tools.cookie_fetcher --config config.yml
echo.
if errorlevel 1 (
  echo [失败] Cookie 采集未完成
) else (
  echo [完成] Cookie 已写入 config.yml 与 config\cookies.json
)
echo.
pause
