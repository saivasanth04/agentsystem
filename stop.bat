@echo off
chcp 65001 >nul
title Stop Agent System Services
cls

echo ======================================================================
echo           STOPPING AGENT SYSTEM MISSION CONTROL SERVICES
echo ======================================================================
echo.

echo [*] Stopping services listening on ports 3000, 8000, 8080...

for %%P in (3000 8000 8080) do (
    for /f "tokens=5" %%a in ('netstat -aon ^| findstr :%%P ^| findstr LISTENING') do (
        echo  [-] Terminating process on port %%P (PID: %%a)...
        taskkill /F /PID %%a >nul 2>nul
    )
)

echo.
echo [PASS] All services on ports 3000, 8000, and 8080 have been stopped.
echo.
pause
