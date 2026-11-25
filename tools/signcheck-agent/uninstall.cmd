@echo off
REM Stops and removes the background task created by install.cmd.
net session >nul 2>&1
if errorlevel 1 (
  echo [signcheck] requesting administrator rights...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
echo.
pause
