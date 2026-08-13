@echo off
REM qalab 本地执行 runner 启动脚本。双击或命令行运行。
REM 首次使用前:先在 gui-mcp 目录跑一次 npm install(见 README)。

setlocal
set "BASE_URL=https://qalab.claw.qihoo.net"
set "RUNNER_ID=win-01"
REM TODO: 填入平台发给本 runner 的长期 token
set "RUNNER_TOKEN=REPLACE_WITH_RUNNER_TOKEN"
set "NAMICLAW_EXE=D:\Program Files\namiclaw\Application\namiclaw.exe"
set "CDP_PORT=9222"
set "POLL_MS=5000"

cd /d "%~dp0"
echo [run] starting qalab runner (base=%BASE_URL% runner=%RUNNER_ID%)
node runner.mjs %*
