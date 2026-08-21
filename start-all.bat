@echo off
title Test Platform - Serve
echo =================================================
echo   Test Management Platform - serve for team
echo =================================================
echo.
echo Cleaning old backend on 8000 ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo Starting BACKEND (serves UI + API on port 8000) ...
start "TP-Backend"   cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000"

echo Starting FRONTEND auto-build (watch: rebuilds 8000) ...
start "TP-Build"     cmd /k "cd /d %~dp0frontend && npm run build:watch"
echo.
echo ---------------------------------------
echo  Two windows launched. Close a window to stop that service.
echo  Team accesses:  http://[this-machine-IP]:8000   (run ipconfig)
echo  Perf now runs inside qalab-runner on each device (perf-agent retired).
echo ---------------------------------------
pause
