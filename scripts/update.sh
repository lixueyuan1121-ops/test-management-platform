#!/usr/bin/env bash
# 测试管理平台 · 更新并后台重启脚本
#
# 用法（在服务器上，仓库根目录执行）：
#   bash scripts/update.sh
#
# 做了什么：
#   1. git pull 拉取最新代码（含开发机构建好的 frontend/dist）
#   2. 若 requirements.txt 有变化，自动重装后端依赖
#   3. 停掉旧进程（按 app.pid），用 nohup 后台重启，日志写 app.log
#
# 端口：默认 4173，可用环境变量覆盖：PORT=8000 bash scripts/update.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PORT="${PORT:-4173}"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.venv"
PIDFILE="$ROOT/app.pid"
LOGFILE="$ROOT/app.log"

if [ ! -x "$VENV/bin/uvicorn" ]; then
  echo "!! 未找到虚拟环境 $VENV。请先跑一次首次部署：bash scripts/deploy.sh" >&2
  exit 1
fi

# ---- 1. 拉取最新代码，顺带看 requirements 是否变化 ----
echo "==> git pull"
REQ_HASH_BEFORE="$(shasum "$BACKEND/requirements.txt" 2>/dev/null | awk '{print $1}' || true)"
git pull
REQ_HASH_AFTER="$(shasum "$BACKEND/requirements.txt" 2>/dev/null | awk '{print $1}' || true)"

# ---- 2. 依赖变化才重装 ----
if [ "$REQ_HASH_BEFORE" != "$REQ_HASH_AFTER" ]; then
  echo "==> requirements.txt 有变化，重装依赖"
  "$VENV/bin/pip" install -r "$BACKEND/requirements.txt"
else
  echo "==> requirements.txt 无变化，跳过装依赖"
fi

# ---- 3. 停旧进程 ----
if [ -f "$PIDFILE" ]; then
  OLD_PID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "==> 停止旧进程 PID=$OLD_PID"
    kill "$OLD_PID" 2>/dev/null || true
    # 等待优雅退出，最多 5 秒
    for _ in 1 2 3 4 5; do
      kill -0 "$OLD_PID" 2>/dev/null || break
      sleep 1
    done
    kill -9 "$OLD_PID" 2>/dev/null || true
  fi
  rm -f "$PIDFILE"
fi

# ---- 3.5 端口兜底：杀掉仍占用 $PORT 的任何进程（孤儿进程 / 手动起的 / pid 对不上）----
free_port() {
  local pids=""
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -ti:"$PORT" 2>/dev/null || true)"
  elif command -v fuser >/dev/null 2>&1; then
    pids="$(fuser "$PORT"/tcp 2>/dev/null || true)"
  else
    echo "   （无 lsof/fuser，跳过端口占用检查）"
    return 0
  fi
  if [ -n "$pids" ]; then
    echo "==> 端口 $PORT 被占用 (PID: $pids)，正在清理"
    kill $pids 2>/dev/null || true
    sleep 2
    # 复查仍在的强杀
    local still=""
    if command -v lsof >/dev/null 2>&1; then
      still="$(lsof -ti:"$PORT" 2>/dev/null || true)"
    fi
    [ -n "$still" ] && { echo "   仍占用，强杀 (PID: $still)"; kill -9 $still 2>/dev/null || true; }
  fi
}
free_port

# ---- 4. 后台重启 ----
echo "==> 后台启动 uvicorn (端口 $PORT)"
cd "$BACKEND"
nohup "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$PORT" > "$LOGFILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"

# 等几秒确认起得来
sleep 4
if kill -0 "$NEW_PID" 2>/dev/null; then
  echo "==> 已启动 PID=$NEW_PID"
  echo "    日志: tail -f $LOGFILE"
  echo "    验证: curl http://127.0.0.1:$PORT/api/health"
  echo "    访问: http://<服务器IP>:$PORT"
else
  echo "!! 进程未能持续运行，请查看日志：" >&2
  tail -n 30 "$LOGFILE" >&2
  exit 1
fi
