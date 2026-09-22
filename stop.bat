@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Agent System Mission Control - Shutdown
cls

echo ======================================================================
echo           STOPPING AGENT SYSTEM MISSION CONTROL SERVICES
echo ======================================================================
echo.

echo [*] Scanning for active services on ports 3000, 8000, 8080...

:: 1. Terminate processes listening on ports 3000, 8000, 8080
for %%P in (3000 8000 8080) do (
    for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr :%%P ^| findstr LISTENING') do (
        if not "%%a"=="" if not "%%a"=="0" (
            echo  [-] Terminating process on port %%P (PID: %%a)...
            taskkill /F /PID %%a >nul 2>nul
        )
    )
)

:: 2. Close matching terminal windows by window title
taskkill /FI "WINDOWTITLE eq Unified LLM Gateway [Port 8080]*" /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Agent Orchestrator Backend [Port 8000]*" /F >nul 2>nul
taskkill /FI "WINDOWTITLE eq Mission Control Frontend [Port 3000]*" /F >nul 2>nul

timeout /t 1 /nobreak >nul

:: 3. Verify all ports are freed
echo.
echo [*] Verifying port release status:
for %%P in (3000 8000 8080) do (
    netstat -aon 2>nul | findstr :%%P | findstr LISTENING >nul
    if !ERRORLEVEL! equ 0 (
        echo  [!] Warning: Port %%P is still active.
    ) else (
        echo  [OK] Port %%P is clean and released.
    )
)

echo.
echo ======================================================================
echo           ALL SERVICES HAVE BEEN SAFELY SHUT DOWN
echo ======================================================================
echo.
timeout /t 3
