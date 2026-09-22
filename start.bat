@echo off
title Agent System Mission Control
cd /d "%~dp0"

echo ======================================================================
echo           AGENT SYSTEM - AUTONOMOUS AI CODING IDE
echo ======================================================================
echo.
echo [*] Working Directory: %CD%
echo.

:: 1. Start Unified Multi-Provider LLM Gateway on port 8080
echo [1/3] Starting Unified LLM Gateway (Port 8080)...
start "LLM Gateway - Port 8080" cmd /k "python -m uvicorn unified_gateway.gateway.server:app --host 127.0.0.1 --port 8080"

:: 2. Start Agent Orchestrator Backend on port 8000
echo [2/3] Starting Agent Orchestrator Backend (Port 8000)...
start "Agent Orchestrator - Port 8000" cmd /k "set GATEWAY_BASE_URL=http://127.0.0.1:8080/v1&& python -m uvicorn agent_orchestrator.server:app --host 127.0.0.1 --port 8000 --reload"

:: 3. Start Mission Control Frontend on port 3000
echo [3/3] Starting Mission Control Frontend (Port 3000)...
start "Mission Control UI - Port 3000" cmd /k "cd frontend && npm run dev"

echo.
echo ======================================================================
echo                 ALL SERVICES LAUNCHED!
echo ======================================================================
echo.
echo  - Frontend UI:       http://localhost:3000
echo  - Backend API:       http://127.0.0.1:8000/docs
echo  - LLM Gateway:       http://127.0.0.1:8080/stats
echo.
echo [*] Opening browser to http://localhost:3000 ...
ping 127.0.0.1 -n 4 >nul
start http://localhost:3000

echo.
echo [*] Services are running in separate console windows.
echo [*] You can keep this window or press any key to close it.
echo.
pause
