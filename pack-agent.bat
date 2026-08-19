@echo off
setlocal
title Pack perf-agent

set "ROOT=%~dp0"
set "AGENT=%ROOT%tools\perf-agent"
set "PERFDOG=D:\git\test\nami-perfdog"
set "STAGE=%TEMP%\perf-agent-pack\perf-agent"
set "OUT=%ROOT%frontend\public\perf-agent.zip"

echo [1/4] Cleaning staging...
if exist "%TEMP%\perf-agent-pack" rmdir /s /q "%TEMP%\perf-agent-pack"
mkdir "%STAGE%\vendor"

echo [2/4] Copying agent files...
copy /y "%AGENT%\perf-agent.mjs"  "%STAGE%\" >nul
copy /y "%AGENT%\.env.example"    "%STAGE%\" >nul
copy /y "%AGENT%\run.cmd"         "%STAGE%\" >nul
copy /y "%AGENT%\DEPLOY.md"       "%STAGE%\" >nul
copy /y "%AGENT%\README.md"       "%STAGE%\" >nul

echo [3/4] Copying perfdog collector...
copy /y "%PERFDOG%\nami-perfdog.mjs" "%STAGE%\" >nul
copy /y "%PERFDOG%\report-logic.mjs" "%STAGE%\" >nul
copy /y "%PERFDOG%\纳米性能测试.bat"  "%STAGE%\" >nul 2>nul
copy /y "%PERFDOG%\vendor\*"         "%STAGE%\vendor\" >nul 2>nul

echo [4/4] Zipping...
if exist "%OUT%" del /q "%OUT%"
powershell -NoProfile -Command "Compress-Archive -Path '%TEMP%\perf-agent-pack\perf-agent' -DestinationPath '%OUT%' -Force"

if exist "%OUT%" (
  echo Done. Bundle: %OUT%
) else (
  echo FAILED - zip not produced.
)
pause
