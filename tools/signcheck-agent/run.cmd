@echo off
REM Windows sign-check agent: polls the test platform and runs verifier\360Base_test.exe.
REM Config lives in .env next to this file (copy .env.example to .env first).
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo [signcheck] Node.js 18+ is required. Install it from https://nodejs.org and reopen this window.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [signcheck] .env not found. Copy .env.example to .env and fill BASE_URL / SIGNCHECK_AGENT_TOKEN.
  pause
  exit /b 1
)

:loop
node agent.mjs %*
if "%~1"=="--selftest" exit /b %errorlevel%
if %errorlevel%==2 (
  pause
  exit /b 2
)
echo [signcheck] agent exited with code %errorlevel%, restarting in 5s
timeout /t 5 /nobreak >nul
goto loop
