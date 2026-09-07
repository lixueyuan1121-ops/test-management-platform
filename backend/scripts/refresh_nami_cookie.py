"""刷新 nami(n.cn)登录 cookie 并端到端验证短链能力。

背景:综合评价在线短链依赖 ~/.openclaw/workspace/config/.cookie.json 里的 n.cn 登录态,
约 7 天过期。过期后上传仍可用(出目录 URL),但取短链接口报 110005 Unauthorized,
一条龙只能回落长链接。本脚本把「换 cookie」做成一步:备份旧文件 → 写新 cookie →
真实调一次 上传+取短链 验证,打印出短链即成功。

用法(在 backend/ 下,用后端 venv):
  .venv/bin/python -m scripts.refresh_nami_cookie              # 交互:提示粘贴 cookie
  .venv/bin/python -m scripts.refresh_nami_cookie --from-file /tmp/cookie.txt
  .venv/bin/python -m scripts.refresh_nami_cookie --check-only # 不改文件,只验证当前 cookie 死活

cookie 从哪来:浏览器登录 https://www.n.cn 后,DevTools → Network → 任一发往 www.n.cn
的请求 → Request Headers → cookie,整串复制。允许带 "cookie:" 前缀/引号,脚本会清洗。
"""
import argparse
import json
import shutil
import sys
from datetime import datetime

from app.services import nami_deploy as nd

_TEST_HTML = "<!doctype html><meta charset='utf-8'><title>cookie 验证</title><p>nami cookie 刷新验证页</p>"


def _clean(raw: str) -> str:
    """清洗粘贴内容:去 BOM/引号/换行/"cookie:" 前缀。"""
    c = raw.strip().lstrip("﻿").strip()
    if c.lower().startswith("cookie:"):
        c = c.split(":", 1)[1].strip()
    if len(c) >= 2 and c[0] == c[-1] and c[0] in "\"'":
        c = c[1:-1].strip()
    return c


def _verify(cookie: str) -> bool:
    """真实走一遍 上传 → 取短链,分步报告。返回短链是否成功。"""
    print("== 验证:上传测试页 …")
    try:
        base_url = nd._upload(_TEST_HTML, cookie)
    except nd.NamiDeployError as e:
        print(f"✗ 上传失败:{e}")
        print("  cookie 整体无效(连上传都不过)。确认已在浏览器里登录 www.n.cn 再复制。")
        return False
    print(f"✓ 上传 OK:{base_url}")

    vm_id = nd._read_vm_id()
    if not vm_id:
        print("! 无 vm_id(cloud_config.json 缺失/不完整),只能出目录链接,无法验证短链。")
        return False
    print("== 验证:取短链 …")
    try:
        short = nd._get_short(vm_id, base_url, cookie)
    except nd.NamiDeployError as e:
        print(f"✗ 取短链失败:{e}")
        if "110005" in str(e):
            print("  仍是 Unauthorized:登录态没进来。常见原因:")
            print("  · 复制的是未登录/别的站点的 cookie —— 必须是发往 www.n.cn 请求头里的整串 cookie;")
            print("  · 复制不完整(cookie 很长,注意别截断,Q/T/__NS_T/__NS_Q 都要在)。")
        return False
    print(f"✓ 短链 OK:{short}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新并验证 nami cookie")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--from-file", help="从文件读 cookie 串(避免交互粘贴)")
    g.add_argument("--check-only", action="store_true", help="不改文件,只验证当前 cookie")
    args = ap.parse_args()

    path = nd._cookie_path()

    if args.check_only:
        try:
            cookie = nd._read_cookie()
        except nd.NamiDeployError as e:
            print(f"✗ 读不到现有 cookie:{e}")
            return 1
        print(f"检查当前 cookie({path})…")
        return 0 if _verify(cookie) else 1

    if args.from_file:
        try:
            with open(args.from_file, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            print(f"✗ 读 {args.from_file} 失败:{e}")
            return 1
    else:
        print("请粘贴新 cookie(浏览器 DevTools → Network → www.n.cn 请求 → Request Headers → cookie 整串),")
        print("粘贴后回车:")
        raw = sys.stdin.readline()

    cookie = _clean(raw)
    if not cookie or "=" not in cookie:
        print("✗ 内容不像 cookie(应为 k=v; k2=v2 …),未做任何修改。")
        return 1
    # 粗查关键登录键,缺了大概率是 document.cookie 复制的(拿不到 HttpOnly),提前提醒
    missing = [k for k in ("Q", "T") if f"{k}=" not in cookie]
    if missing:
        print(f"! 提醒:cookie 里没看到 {missing} 键。若验证失败,请改用 Network 请求头复制(而非 document.cookie)。")

    if not _verify(cookie):
        print("✗ 新 cookie 验证未通过,未写入文件(原 cookie 保持不动)。")
        return 1

    # 验证通过才落盘:备份旧文件 → 写新 → 收紧权限
    if path.is_file():
        bak = path.with_name(f"{path.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, bak)
        print(f"旧 cookie 已备份:{bak}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cookie": cookie}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print(f"✓ 已写入 {path}(权限 600)。后端按次读盘,无需重启,下次综合评价即出真短链。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
