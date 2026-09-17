#!/usr/bin/env bash
# qalab 本地执行 runner 启动脚本(Mac/Linux)。对应 Windows 的 run.cmd。
# 首次使用前:cp .env.example .env 并填好;在 gui-mcp 目录跑 npm install。
# 启动时自动向平台检查 runner 新版本(node runner.mjs --update):
#   exit 75 = 已下载覆盖新版本;exit 0 = 已最新或网络更新失败(可继续使用本地版本)。
#   其他退出码表示模块缺失等启动错误，应停止，不能带错继续启动。
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "--check" ]; then
  exec node runner.mjs --check
fi

echo "[run] checking runner update"
for i in 1 2 3; do
  code=0
  node runner.mjs --update || code=$?
  if [ "$code" -ne 0 ] && [ "$code" -ne 75 ]; then
    echo "[run] runner startup check failed (exit $code); stopped. Check missing modules or restore a complete runner version." >&2
    exit "$code"
  fi
  [ "$code" -eq 0 ] && break
  echo "[run] runner updated, re-checking"
done

echo "[run] starting qalab runner"
# runner.mjs 会自动读取同目录 .env
exec node runner.mjs "$@"
