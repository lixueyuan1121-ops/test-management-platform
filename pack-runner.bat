@echo off
setlocal
title Pack qalab-runner
set "ROOT=%~dp0"
set "RUNNER=%ROOT%tools\qalab-runner"
set "PERFDOG=D:\git\test\nami-perfdog"
set "STAGE=%TEMP%\qalab-runner-pack\qalab-runner"
set "OUT=%ROOT%frontend\public\qalab-runner.zip"

echo [1/4] Cleaning staging...
if exist "%TEMP%\qalab-runner-pack" rmdir /s /q "%TEMP%\qalab-runner-pack"
mkdir "%STAGE%"

echo [2/4] Copying runner (exclude .env / node_modules)...
robocopy "%RUNNER%" "%STAGE%" /E /XD node_modules .git /XF .env >nul

echo [3/4] Copying perfdog collector...
copy /y "%PERFDOG%\nami-perfdog.mjs" "%STAGE%\" >nul
copy /y "%PERFDOG%\report-logic.mjs" "%STAGE%\" >nul
copy /y "%PERFDOG%\*.bat" "%STAGE%\" >nul 2>nul
if not exist "%STAGE%\vendor" mkdir "%STAGE%\vendor"
copy /y "%PERFDOG%\vendor\*" "%STAGE%\vendor\" >nul 2>nul

echo [4/4] Zipping...
if exist "%OUT%" del /q "%OUT%"
powershell -NoProfile -Command "Compress-Archive -Path '%TEMP%\qalab-runner-pack\qalab-runner' -DestinationPath '%OUT%' -Force"
if exist "%OUT%" ( echo Done. Bundle: %OUT% ) else ( echo FAILED - zip not produced. )
pause
