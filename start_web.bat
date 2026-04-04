@echo off
chcp 65001 >nul
echo =========================================
echo       Douyin Downloader Web Console
echo =========================================
echo Starting FastAPI Backend Services...
start "DouyinDL API Backend" cmd /k "cd /d %~dp0 && .venv\Scripts\python.exe -m uvicorn api.server:app --reload --host 127.0.0.1 --port 8000"

echo Starting React Frontend Interface...
start "DouyinDL Web Frontend" cmd /k "cd /d %~dp0web && npm run dev"

echo.
echo All services launched!
echo Please open your browser to the local access URL: http://127.0.0.1:8899/
echo.
pause
