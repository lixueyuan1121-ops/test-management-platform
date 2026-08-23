#!/usr/bin/env bash
# 对话测评执行器启动脚本(ai-eval platform 模式)。与 run.sh(功能测试点)共用同一套平台配置。
# 首次:cd eval && npm install(装 playwright 等依赖)。
set -e
export BASE_URL="${BASE_URL:-https://qalab.claw.qihoo.net}"
export RUNNER_ID="${RUNNER_ID:-mac-01}"
export RUNNER_TOKEN="${RUNNER_TOKEN:-REPLACE_WITH_RUNNER_TOKEN}"
export NAMICLAW_EXE="${NAMICLAW_EXE:-/Applications/Namiwork.app/Contents/MacOS/Namiwork}"
export CDP_PORT="${CDP_PORT:-9222}"
cd "$(dirname "$0")/eval"
echo "[run-eval] starting 对话测评 executor (base=$BASE_URL runner=$RUNNER_ID)"
node bin/ai-eval.js platform "$@"
