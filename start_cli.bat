@echo off
chcp 65001 >nul
title Douyin Downloader CLI

echo ========================================
echo   Douyin Downloader CLI
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
        echo [DONE] Created config.yml. Please edit and run again.
        notepad config.yml
        pause
        exit /b 0
    ) else (
        echo [ERROR] Config template not found
        pause
        exit /b 1
    )
)

echo.
echo [START] Starting CLI...
echo.

REM Start CLI
python -m cli.main

pause
