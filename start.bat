@echo off
title Agent System Mission Control - Launcher
cls

echo ======================================================================
echo           AGENT SYSTEM - AUTONOMOUS AI CODING IDE AND ORCHESTRATOR
echo ======================================================================
echo.

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

echo [*] Working Directory: %ROOT_DIR%
echo.

:: 1. Validate Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH. Please install Python 3.10+ and add it to PATH.
    echo.
    pause
    exit /b 1
)

:: 2. Validate Node.js & npm
npm --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found in PATH. Please install Node.js 18+ and add it to PATH.
    echo.
    pause
    exit /b 1
)

echo [OK] Python and npm detected.
echo.

:: 3. Check frontend dependencies
if not exist "%ROOT_DIR%frontend\node_modules\" (
    echo [*] Frontend dependencies not found. Installing node packages...
    cd /d "%ROOT_DIR%frontend"
    call npm install
    cd /d "%ROOT_DIR%"
    echo [OK] Frontend packages installed.
    echo.
)

:: 4. Clean up any existing instances on ports 3000, 8000, 8080
echo [*] Checking for existing services on ports 3000, 8000, 8080...
for %%P in (3000 8000 8080) do (
    for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr :%%P ^| findstr LISTENING') do (
        if not "%%a"=="" (
            echo  [-] Freeing port %%P [PID: %%a]
            taskkill /F /PID %%a >nul 2>&1
        )
    )
)
echo [OK] Ports 3000, 8000, 8080 checked.
echo.

:: 5. Launch Unified Multi-Provider LLM Gateway (Port 8080)
echo [1/3] Starting Unified Multi-Provider LLM Gateway on http://127.0.0.1:8080 ...
start "Unified LLM Gateway [Port 8080]" cmd /k "cd /d %ROOT_DIR% && set PYTHONUTF8=1 && set PYTHONUNBUFFERED=1 && python -m uvicorn unified_gateway.gateway.server:app --host 127.0.0.1 --port 8080"

:: 6. Launch Agent Orchestrator Backend Server (Port 8000)
echo [2/3] Starting Agent Orchestrator Backend on http://127.0.0.1:8000 ...
start "Agent Orchestrator Backend [Port 8000]" cmd /k "cd /d %ROOT_DIR% && set PYTHONUTF8=1 && set PYTHONUNBUFFERED=1 && set GATEWAY_BASE_URL=http://127.0.0.1:8080/v1 && python -m uvicorn agent_orchestrator.server:app --host 127.0.0.1 --port 8000 --reload"

:: 7. Launch Frontend IDE Dev Server (Port 3000)
echo [3/3] Starting Mission Control Frontend IDE on http://localhost:3000 ...
start "Mission Control Frontend [Port 3000]" cmd /k "cd /d %ROOT_DIR%frontend && npm run dev"

echo.
echo ======================================================================
echo                 ALL SERVICES LAUNCHED SUCCESSFULLY!
echo ======================================================================
echo.
echo  - Frontend Web IDE:        http://localhost:3000
echo  - Backend API and Swagger: http://127.0.0.1:8000/docs
echo  - Unified LLM Gateway:     http://127.0.0.1:8080/stats
echo.
echo [*] Waiting 4 seconds for services to initialize...
ping 127.0.0.1 -n 5 >nul

echo [*] Opening Agentic IDE in default browser...
start http://localhost:3000

echo.
echo [*] All 3 background service consoles are running.
echo [*] To stop all services, run: stop.bat
echo.
echo Services are running. You can close this launcher window at any time.
pause
