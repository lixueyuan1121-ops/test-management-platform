#!/usr/bin/env bash
# 对话测评执行器启动脚本(ai-eval platform 模式)。与 run.sh(功能测试点)共用同一套平台配置。
# 配置来源:上级 tools/qalab-runner/.env(eval 的 loadDotEnv 会读它)。本脚本【不】硬编码 token,
# 避免覆盖 .env(BASE_URL/RUNNER_TOKEN/RUNNER_ID/NAMICLAW_EXE/CDP_PORT 都在 .env 里)。
# 首次:cd eval && npm install(装 playwright 等依赖)。
set -e
cd "$(dirname "$0")/eval"
echo "[run-eval] starting 对话测评 executor (config from ../.env)"
node bin/ai-eval.js platform "$@"
