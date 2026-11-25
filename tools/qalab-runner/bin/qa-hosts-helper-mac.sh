#!/bin/bash
# qa-hosts-helper —— Mac 上的提权助手。只做三件事：快照 / 应用 / 还原系统 hosts。
# 安装：sudo install -m 755 -o root -g wheel qa-hosts-helper /usr/local/bin/
# 提权：sudoers 里只对这一条命令放行（见 README）。
#
# 安全边界：这个脚本被 NOPASSWD 放行，所以它必须足够笨 ——
# 不执行外部输入、不读 stdin、参数只当文件路径用且做白名单校验。

set -euo pipefail

HOSTS=/etc/hosts
ALLOWED_DIR_PREFIX="${QA_HELPER_ALLOWED_DIR:-}"

die() { echo "qa-hosts-helper: $*" >&2; exit 1; }

# 参数必须是绝对路径、且是普通文件或目标位置（防止被当成别的东西注入）
check_path() {
  local p="$1" must_exist="${2:-yes}"
  [[ "$p" == /* ]] || die "路径必须是绝对路径: $p"
  [[ "$p" != *$'\n'* ]] || die "路径含换行"
  if [[ "$must_exist" == "yes" ]]; then
    [[ -f "$p" ]] || die "不是普通文件: $p"
  fi
  # 可选：限定工作目录前缀，避免被用来读写任意位置
  if [[ -n "$ALLOWED_DIR_PREFIX" ]]; then
    [[ "$p" == "$ALLOWED_DIR_PREFIX"* ]] || die "路径不在允许的前缀下: $p"
  fi
}

case "${1:-}" in
  snapshot)
    check_path "${2:-}" no
    cp "$HOSTS" "$2"
    chmod 644 "$2"
    echo "snapshot -> $2"
    ;;

  apply)
    check_path "${2:-}" yes
    # 内容校验：只看「生效行」（去掉注释和空行）必须是 ASCII。
    # 注释里写中文完全正常 —— SwitchHosts 导出的分组基本都带中文注释，不能拦。
    # 目的是防止把奇怪的东西塞进解析行，不是防止注释。
    if grep -v '^[[:space:]]*#' "$2" | grep -v '^[[:space:]]*$' | grep -qP '[^\x00-\x7F]'; then
      die "分组文件的有效行含非 ASCII 字符（注释不受限），拒绝写入"
    fi
    # 保留一份时间戳备份，便于事后追溯
    cp "$HOSTS" "/etc/hosts.qa-bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
    # 原子写：先写到临时文件再 mv，避免中途崩溃留下半个 hosts
    install -m 644 -o root -g wheel "$2" "$HOSTS.qa-tmp"
    mv -f "$HOSTS.qa-tmp" "$HOSTS"
    echo "applied <- $2"
    ;;

  restore)
    check_path "${2:-}" yes
    install -m 644 -o root -g wheel "$2" "$HOSTS.qa-tmp"
    mv -f "$HOSTS.qa-tmp" "$HOSTS"
    echo "restored <- $2"
    ;;

  *)
    die "用法: $0 {snapshot|apply|restore} <path>"
    ;;
esac
