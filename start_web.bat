@echo off
chcp 65001 >nul
title Douyin Downloader Web Server

echo ========================================
echo   Douyin Downloader Web Server
echo ========================================
echo.

REM Check Python installation
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

REM Check config file
if not exist "config.yml" (
    echo [WARNING] config.yml not found
    if exist "config.example.yml" (
        echo [INFO] Copying config.example.yml to config.yml...
        copy config.example.yml config.yml >nul
        echo [INFO] Created config.yml. Please edit as needed.
    ) else (
        echo [ERROR] Config template not found. Please create config.yml manually.
        pause
        exit /b 1
    )
)

REM Check dependencies
echo [CHECK] Checking dependencies...
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies
        pause
        exit /b 1
    )
)

echo.
echo [START] Starting Web Server...
echo [INFO] Web UI: http://localhost:8000
echo [INFO] API Docs: http://localhost:8000/docs
echo [INFO] Press Ctrl+C to stop
echo.

REM Start Web server with verbose logging
python -m web.app --config config.yml --port 8000 -v

pause
