#!/usr/bin/env bash
# qalab 本地执行 runner 启动脚本(Mac/Linux)。对应 Windows 的 run.cmd。
# 首次使用前:cp .env.example .env 并填好;在 gui-mcp 目录跑 npm install。
# 启动时自动向平台检查 runner 新版本(node runner.mjs --update):
#   exit 75 = 已下载覆盖新版本(再检查一次直至最新,防连续两次发版);其余退出码 = 已最新/检查失败,直接启动。
set -euo pipefail
cd "$(dirname "$0")"

echo "[run] checking runner update"
for i in 1 2 3; do
  code=0
  node runner.mjs --update || code=$?
  [ "$code" -ne 75 ] && break
  echo "[run] runner updated, re-checking"
done

echo "[run] starting qalab runner"
# runner.mjs 会自动读取同目录 .env
exec node runner.mjs "$@"
