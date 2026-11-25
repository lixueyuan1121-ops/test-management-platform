#!/bin/bash
set -eu
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v node >/dev/null || ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)'; then
  echo "本机需要 Node.js 18 或以上版本。安装后重新启动本工具。"
  read -r -p "按回车退出。" _
  exit 2
fi
exec node ./agent.mjs
