#!/usr/bin/env bash
# DeepSeek 引擎（dsh）独立环境部署脚本 —— 仅在需要"deepseek 生成引擎"时运行。
#
# 为什么独立：dsh SDK 自带内嵌 runtime、依赖较新 pydantic，与后端主环境（backend/.venv）
# 隔离开，装在单独的 venv 里，由后端以子进程方式调用（generators/dsh_worker.py）。
# 这样主环境依赖不受影响，dsh 崩溃也波及不到平台；不跑本脚本时，平台 claude 引擎照常。
#
# 用法（服务器上，仓库根目录执行）：
#   bash scripts/deploy-dsh.sh
# 默认在 ~/.dsh-venv 建 venv；可用环境变量覆盖：DSH_VENV=/path bash scripts/deploy-dsh.sh
#
# 之后在 backend/.env 里配（示例）：
#   DEEPSEEK_ENABLED=true
#   DEEPSEEK_VENV_PYTHON=~/.dsh-venv/bin/python   # 与本脚本的 DSH_VENV 对应
#   DEEPSEEK_BASE_URL=http://<内网网关>/v1         # 或用官方，留空
#   DEEPSEEK_API_KEY=<key>                         # llm-deepseek 强制要非空，网关不校验也给占位
#   DEEPSEEK_MODEL=deepseek-v4-flash
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DSH_VENV="${DSH_VENV:-$HOME/.dsh-venv}"
# dsh SDK 版本：锁定到已验证的 rc 版，避免 developer-preview 迭代破坏（可按需升级）
DSH_PKG="${DSH_PKG:-deepseek-harness-sdk==0.1.0rc6}"

echo "==> dsh 独立 venv: $DSH_VENV"
echo "==> 安装包: $DSH_PKG"

# ---- 1. 探测 python (>=3.10，dsh SDK 要求) ----
pick_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}
PY="$(pick_python || true)"
if [ -z "$PY" ]; then
  echo "!! 未找到 Python 3.10+，dsh SDK 无法安装。" >&2
  exit 1
fi
echo "==> 使用 Python: $($PY --version 2>&1)"

# ---- 2. 建独立 venv + 装 dsh SDK ----
if [ ! -d "$DSH_VENV" ]; then
  echo "==> 创建独立 venv $DSH_VENV"
  "$PY" -m venv "$DSH_VENV"
fi
"$DSH_VENV/bin/pip" install --upgrade pip >/dev/null
echo "==> 安装 $DSH_PKG（含内嵌 runtime，无需系统 Node）"
"$DSH_VENV/bin/pip" install "$DSH_PKG"

# ---- 3. 冒烟：确认 SDK 可导入、runtime 二进制就位 ----
echo "==> 冒烟验证"
"$DSH_VENV/bin/python" - <<'PYEOF'
import importlib.util as u
ok = all(u.find_spec(m) for m in ("deepseek_harness", "deepseek_harness_runtime"))
print("   deepseek_harness + runtime:", "OK" if ok else "MISSING")
import deepseek_harness  # noqa
print("   import OK, version tag rc6")
PYEOF

cat <<EOF

==> dsh 独立环境就绪：$DSH_VENV
    下一步：在 backend/.env 配置 DEEPSEEK_* （见本脚本顶部注释），然后重启后端。
    验证：登录平台 → AI 测试助手 → 顶部"生成引擎"下拉应能选到 deepseek。
    未配置或本 venv 缺失时，deepseek 自动置灰，claude 引擎不受影响。
EOF
