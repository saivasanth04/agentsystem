@echo off
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
        if not "%%a"=="" (
            echo  [-] Freeing port %%P [PID: %%a]
            taskkill /F /PID %%a >nul 2>&1
        )
    )
)

:: 2. Close matching terminal windows by window title
taskkill /FI "WINDOWTITLE eq Unified LLM Gateway [Port 8080]*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Agent Orchestrator Backend [Port 8000]*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Mission Control Frontend [Port 3000]*" /F >nul 2>&1

ping 127.0.0.1 -n 2 >nul

:: 3. Verify all ports are freed
echo.
echo [*] Verifying port release status:
for %%P in (3000 8000 8080) do (
    netstat -aon 2>nul | findstr :%%P | findstr LISTENING >nul
    if errorlevel 1 (
        echo  [OK] Port %%P is clean and released.
    ) else (
        echo  [!] Warning: Port %%P is still active.
    )
)

echo.
echo ======================================================================
echo           ALL SERVICES HAVE BEEN SAFELY SHUT DOWN
echo ======================================================================
echo.
pause
