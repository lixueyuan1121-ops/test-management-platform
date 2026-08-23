@echo off
REM 对话测评执行器启动脚本(ai-eval platform 模式)。与 run.cmd(功能测试点)共用同一套平台配置。
REM 首次使用前:cd eval ^&^& npm install(装 playwright 等依赖,见 eval\README.md)。

setlocal
REM 平台连接(与 run.cmd 保持一致的值)
set "BASE_URL=https://qalab.claw.qihoo.net"
set "RUNNER_ID=lili-win"
REM TODO: 填入平台发给本 runner 的长期 token(与 run.cmd 同一个)
set "RUNNER_TOKEN=REPLACE_WITH_RUNNER_TOKEN"
set "NAMICLAW_EXE=D:\Program Files\namiwork\Namiwork.exe"
set "CDP_PORT=9222"
REM 对话测评专属(飞书导出等,按需填;不用可留空)
set "FEISHU_APP_ID="
set "FEISHU_APP_SECRET="

cd /d "%~dp0eval"
echo [run-eval] starting 对话测评 executor (base=%BASE_URL% runner=%RUNNER_ID%)
node bin/ai-eval.js platform %*
