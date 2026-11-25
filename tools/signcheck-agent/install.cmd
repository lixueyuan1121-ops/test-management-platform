@echo off
REM One-time setup: registers the sign-check agent as a background task that starts with Windows.
REM Fill in .env first. Needs administrator rights (asks for elevation if missing).
net session >nul 2>&1
if errorlevel 1 (
  echo [signcheck] requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
