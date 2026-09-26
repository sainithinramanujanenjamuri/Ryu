@echo off
title RYU AI Command Center
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch_ryu.ps1"
if errorlevel 1 (
    echo.
    echo [ERROR] Preflight failed. Press any key to view error.
    pause
    exit /b 1
)

echo.
echo Launching RYU Desktop Command Center...
if exist "%~dp0apps\ryu-desktop\src-tauri\target\release\ryu-desktop.exe" (
    start "" "%~dp0apps\ryu-desktop\src-tauri\target\release\ryu-desktop.exe"
) else (
    start "" "%~dp0apps\ryu-desktop\src-tauri\target\debug\ryu-desktop.exe"
)
ping -n 2 127.0.0.1 >nul
