@echo off
chcp 65001 >nul
title Agent System Mission Control Launcher
cls

echo ======================================================================
echo           AGENT SYSTEM - MULTI-AGENT MISSION CONTROL
echo ======================================================================
echo.
echo [*] Checking runtime prerequisites...

where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not found in PATH. Please install Python 3.10+.
    pause
    exit /b 1
)

where npm >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Node.js / npm is not found in PATH. Please install Node.js 18+.
    pause
    exit /b 1
)

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo [*] Root Directory: %ROOT_DIR%
echo.

:: 1. Start Unified Multi-Provider LLM Gateway (Port 8080)
echo [1/3] Starting Unified Multi-Provider LLM Gateway on http://127.0.0.1:8080 ...
start "Unified LLM Gateway [Port 8080]" cmd /k "cd /d "%ROOT_DIR%" && python -m uvicorn unified_gateway.gateway.server:app --host 127.0.0.1 --port 8080"

:: 2. Start Agent Orchestrator Backend Server (Port 8000)
echo [2/3] Starting Agent Orchestrator Backend on http://127.0.0.1:8000 ...
start "Agent Orchestrator Backend [Port 8000]" cmd /k "cd /d "%ROOT_DIR%" && set "GATEWAY_BASE_URL=http://127.0.0.1:8080/v1" && python -m uvicorn agent_orchestrator.server:app --host 127.0.0.1 --port 8000 --reload"

:: 3. Start Frontend Dev Server (Port 3000)
echo [3/3] Starting Mission Control Frontend on http://localhost:3000 ...
start "Mission Control Frontend [Port 3000]" cmd /k "cd /d "%ROOT_DIR%frontend" && npm run dev"

echo.
echo ======================================================================
echo                 ALL SERVICES LAUNCHED SUCCESSFULLY!
echo ======================================================================
echo.
echo  - Frontend Web UI:       http://localhost:3000
echo  - Backend API & Docs:    http://127.0.0.1:8000/docs
echo  - Unified LLM Gateway:   http://127.0.0.1:8080/stats
echo.
echo [*] Opening browser to http://localhost:3000 in 3 seconds...
timeout /t 3 /nobreak >nul

start http://localhost:3000

echo.
echo [*] Mission Control is active. To stop services, close the terminal windows or run stop.bat.
echo.
pause
