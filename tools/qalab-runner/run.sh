#!/usr/bin/env bash
# qalab 本地执行 runner 启动脚本(Mac/Linux)。对应 Windows 的 run.cmd。
# 首次使用前:cp .env.example .env 并填好;在 gui-mcp 目录跑 npm install。
set -euo pipefail
cd "$(dirname "$0")"
echo "[run] starting qalab runner"
# runner.mjs 会自动读取同目录 .env
exec node runner.mjs "$@"
