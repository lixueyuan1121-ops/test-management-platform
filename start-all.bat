@echo off
title Test Platform - Serve
echo ============================================
echo   Test Management Platform - serve for team
echo ============================================
echo.
echo Cleaning old backend on 8000 ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo Starting BACKEND (serves UI + API on port 8000) ...
start "TP-Backend"   cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"

echo Starting FRONTEND auto-build (watch: rebuilds 8000 when you edit frontend code) ...
start "TP-Build"     cmd /k "cd /d %~dp0frontend && npm run build:watch"

echo Starting PERF-AGENT (runner win-01) ...
start "TP-PerfAgent" cmd /k "cd /d %~dp0tools\perf-agent && node perf-agent.mjs"

echo.
echo ---------------------------------------------
echo  Three windows launched. Close a window to stop that service.
echo  Team accesses:  http://[this-machine-IP]:8000   (run ipconfig to get IPv4)
echo  TP-Build auto-rebuilds frontend into 8000 on code change (wait ~15s, then refresh browser).
echo ---------------------------------------------
pause
