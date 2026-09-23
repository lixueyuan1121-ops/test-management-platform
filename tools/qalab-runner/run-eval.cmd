@echo off
setlocal
cd /d "%~dp0eval"
if not exist "node_modules" (
  echo [run-eval] Install dependencies first: cd eval then npm install
  exit /b 1
)
:run_loop
node bin\ai-eval.js platform %*
set "_code=%errorlevel%"
if "%_code%"=="75" goto run_loop
exit /b %_code%
