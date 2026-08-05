#!/usr/bin/env bash
# 测试管理平台 · 服务器首次部署脚本（无 Docker / 无 Node / 无 sudo）
#
# 用法（在服务器上，仓库根目录执行）：
#   bash scripts/deploy.sh
#
# 做了什么：
#   1. 探测可用的 python3（要求 3.10+，因代码用了 `X | None` 语法）
#   2. 建后端虚拟环境 backend/.venv 并安装依赖
#   3. 若无 backend/.env，则从 .env.example 复制并生成随机 JWT_SECRET
#   4. 前台启动一次做冒烟（可 Ctrl+C 退出），随后提示用 update.sh 常驻
#
# 端口：默认 4173，可用环境变量覆盖：PORT=8000 bash scripts/deploy.sh
set -euo pipefail

# 切到仓库根（脚本在 scripts/ 下）
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PORT="${PORT:-4173}"
BACKEND="$ROOT/backend"
VENV="$BACKEND/.venv"

echo "==> 仓库根: $ROOT"
echo "==> 目标端口: $PORT"

# ---- 1. 探测 python (>=3.10) ----
pick_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      # 校验版本 >= 3.10
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}
PY="$(pick_python || true)"
if [ -z "$PY" ]; then
  echo "!! 未找到 Python 3.10+。本项目源码使用了 3.10 语法（X | None），无法用更低版本运行。" >&2
  echo "   请安装/激活 Python 3.10+ 后重试（服务器上 pip3 指向的那个 3.10 通常可用，试试 which python3.10）。" >&2
  exit 1
fi
echo "==> 使用 Python: $($PY --version 2>&1) ($(command -v "$PY"))"

# ---- 2. 建 venv + 装依赖 ----
if [ ! -d "$VENV" ]; then
  echo "==> 创建虚拟环境 $VENV"
  "$PY" -m venv "$VENV"
fi
echo "==> 安装/更新依赖"
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$BACKEND/requirements.txt"

# ---- 3. 准备 .env ----
ENV_FILE="$BACKEND/.env"
if [ ! -f "$ENV_FILE" ]; then
  echo "==> 未发现 backend/.env，从 .env.example 生成"
  cp "$BACKEND/.env.example" "$ENV_FILE"
  # 生成随机 JWT_SECRET（优先 openssl，退化用 python）
  SECRET="$(openssl rand -hex 32 2>/dev/null || "$VENV/bin/python" -c 'import secrets;print(secrets.token_hex(32))')"
  # 就地替换 JWT_SECRET 行（BSD/GNU sed 都兼容的写法：用 python 改，避免 sed -i 差异）
  "$VENV/bin/python" - "$ENV_FILE" "$SECRET" <<'PYEOF'
import sys, re, pathlib
p, secret = pathlib.Path(sys.argv[1]), sys.argv[2]
txt = p.read_text(encoding="utf-8")
txt = re.sub(r'^JWT_SECRET=.*$', f'JWT_SECRET={secret}', txt, flags=re.M)
p.write_text(txt, encoding="utf-8")
PYEOF
  echo "   已写入随机 JWT_SECRET。"
  echo "   !! 请手动编辑 backend/.env，把 SEED_ADMIN_PASSWORD 改成强密码（首次启动即生效）。"
else
  echo "==> 已存在 backend/.env，保持不变（不覆盖你的生产配置）"
fi

# ---- 4. 前台冒烟 ----
cat <<EOF

==> 依赖就绪。现在前台启动做一次冒烟验证（Ctrl+C 可停止）：
    浏览器访问 http://<服务器IP>:$PORT
    本机验证：curl http://127.0.0.1:$PORT/api/health

    冒烟没问题后，请用后台常驻方式启动：
        bash scripts/update.sh

EOF
cd "$BACKEND"
exec "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$PORT"
