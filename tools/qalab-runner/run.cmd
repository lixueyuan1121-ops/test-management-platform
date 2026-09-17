@echo off
REM qalab 本地执行 runner 启动脚本。双击或命令行运行。
REM 首次使用前:先在 gui-mcp 目录跑一次 npm install(见 README)。
REM 启动时自动检查更新:runner.mjs --update 退出码 75 = 已更新,重新检查直至最新。

setlocal
set "BASE_URL=https://qalab.claw.qihoo.net"
set "RUNNER_ID=win-01"
REM TODO: 填入平台发给本 runner 的长期 token
set "RUNNER_TOKEN=REPLACE_WITH_RUNNER_TOKEN"
set "NAMICLAW_EXE=D:\Program Files\namiclaw\Application\namiclaw.exe"
set "CDP_PORT=9222"
set "POLL_MS=5000"

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
echo [run] starting qalab runner (base=%BASE_URL% runner=%RUNNER_ID%)
node runner.mjs %*
exit /b %errorlevel%

:check_only
node runner.mjs --check
exit /b %errorlevel%

:update_failed
echo [run] runner startup check failed (exit %_update_code%); stopped. Check missing modules or restore a complete runner version. >&2
exit /b %_update_code%
