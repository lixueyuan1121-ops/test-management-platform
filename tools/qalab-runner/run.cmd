@echo off
setlocal
REM Configure this runner in .env next to runner.mjs.
cd /d "%~dp0"
if /I "%~1"=="--check" goto check_only

echo [run] checking runner update
set /a _tries=0
:update_loop
node runner.mjs --update
set "_update_code=%errorlevel%"
if "%_update_code%"=="75" goto updated
if not "%_update_code%"=="0" goto update_failed
goto start_runner

:updated
set /a _tries+=1
if %_tries% lss 3 (
  echo [run] runner updated, re-checking
  goto update_loop
)

:start_runner
echo [run] starting qalab runner
node runner.mjs %*
exit /b %errorlevel%

:check_only
node runner.mjs --check
exit /b %errorlevel%

:update_failed
echo [run] runner startup check failed (exit %_update_code%); stopped. Check missing modules or restore a complete runner version. >&2
exit /b %_update_code%
