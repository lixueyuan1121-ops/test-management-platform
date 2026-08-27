"""清空指定项目的语义选择器(selector_key),为「重新上传一份全新注册表」腾空。

走线上 HTTP API(不直连库,本机能访问线上 backend 即可运行),风格对齐
import_selectors_from_file.py。安全设计:
  - dry-run 默认:只导出备份 + 打印将删清单,绝不删;加 --apply 才真删(且再交互确认)。
  - 删前强制先导出 JSON 备份,格式 = import_selectors_from_file.py 的输入格式,
    等于内置一键回滚(见运行末尾提示)。备份默认名带时间戳,永不互相覆盖。
  - 只清 selector_key,不动 selector_scope(vmIframe 保持不变,重传新的时自行覆盖)。
  - 按 (project, sub_product) 分域清,默认项目级共享(sub_product='');
    每次都先打印全域概览,避免漏清子产品域或误清错域。

用法(在 backend 目录):
    # 1) 先查项目号
    .venv/bin/python scripts/clear_project_selectors.py \
        --base-url http://<线上ip>:8000 --username X --list-projects

    # 2) dry-run(默认):导出备份 + 打印将删清单,不删
    .venv/bin/python scripts/clear_project_selectors.py \
        --base-url http://<线上ip>:8000 --username X --project 1

    # 3) 核对 dry-run 清单无误后,真正清空(会再次交互确认)
    .venv/bin/python scripts/clear_project_selectors.py \
        --base-url http://<线上ip>:8000 --username X --project 1 --apply

回滚:用导出的备份文件回灌(--overwrite 以备份为准)
    .venv/bin/python scripts/import_selectors_from_file.py \
        --base-url ... --username X --project 1 --file <backup.json> --overwrite

若本机访问不到线上 backend,把本文件拷到服务器,--base-url 用 http://127.0.0.1:8000 跑。
"""
import argparse
import getpass
import json
import os
import sys
from datetime import datetime

import requests


def _unwrap(resp: requests.Response):
    """解 {code,msg,data} 信封;code!=0 抛错。"""
    try:
        body = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"非 JSON 响应: {resp.status_code} {resp.text[:200]}")
    if isinstance(body, dict) and "code" in body:
        if body["code"] != 0:
            raise RuntimeError(f"接口报错 code={body['code']}: {body.get('msg')}")
        return body.get("data")
    return body


def _login(sess, base, username, password) -> str:
    data = _unwrap(sess.post(f"{base}/api/auth/login",
                             json={"username": username, "password": password}, timeout=15))
    return data["access_token"]


def _list_projects(sess, base):
    data = _unwrap(sess.get(f"{base}/api/projects", timeout=15))
    rows = data if isinstance(data, list) else data.get("items", data)
    print(f"可选项目({len(rows)}):")
    for p in rows:
        print(f"  id={p.get('id')}  name={p.get('name')}  code={p.get('code')}")


def _fetch(sess, base, project_id):
    """GET /manage → (shared:list, by_sub:dict)。每行含 id/key/frame/page/desc/candidates。"""
    data = _unwrap(sess.get(f"{base}/api/selectors/manage",
                            params={"project_id": project_id}, timeout=30))
    return data.get("shared", []), data.get("by_sub", {})


def _to_registry(rows) -> dict:
    """把 manage 行列表转成可回灌的 registry:{key:{frame,page,desc,candidates}}。"""
    return {
        r["key"]: {
            "frame": r.get("frame", "auto"), "page": r.get("page", ""),
            "desc": r.get("desc", ""), "candidates": r.get("candidates", []),
        }
        for r in rows
    }


def _rollback_hint(base, username, project, backup) -> str:
    return (f"回滚:python scripts/import_selectors_from_file.py --base-url {base} "
            f"--username {username} --password *** --project {project} "
            f"--file {backup} --overwrite")


def main():
    ap = argparse.ArgumentParser(description="清空指定项目的选择器(dry-run 默认,删前先导备份)")
    ap.add_argument("--base-url", required=True, help="线上 backend 地址,如 http://10.x.x.x:8000")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", help="不传则交互输入(不进 shell history)")
    ap.add_argument("--project", type=int, help="目标 project_id")
    ap.add_argument("--sub-product", default="", help="清哪个域(默认空=项目级共享)")
    ap.add_argument("--list-projects", action="store_true", help="只列项目后退出")
    ap.add_argument("--backup", help="备份文件路径(默认 selectors_backup_p<pid>_<域>_<时间戳>.json)")
    ap.add_argument("--apply", action="store_true", help="真正删除(默认 dry-run 不删)")
    ap.add_argument("--force", action="store_true", help="--backup 指定的文件已存在时允许覆盖")
    ap.add_argument("--yes", action="store_true", help="--apply 时跳过交互确认")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    password = args.password or getpass.getpass(f"{args.username} 的密码: ")

    sess = requests.Session()
    token = _login(sess, base, args.username, password)
    sess.headers["Authorization"] = f"Bearer {token}"
    print(f"登录成功 @ {base}")

    if args.list_projects:
        _list_projects(sess, base)
        return

    if args.project is None:
        sys.exit("需 --project(先用 --list-projects 查项目号)")

    shared, by_sub = _fetch(sess, base, args.project)
    # 全域概览:清楚打印每个域有多少 key,避免误清错域或漏清子产品域
    print(f"\n项目 {args.project} 选择器概览:")
    print(f"  项目级共享(sub_product=''): {len(shared)} 个 key")
    for sub_name, rows in by_sub.items():
        print(f"  子产品「{sub_name}」: {len(rows)} 个 key")

    sub = args.sub_product
    target = shared if sub == "" else by_sub.get(sub, [])
    dom = "项目级共享" if sub == "" else f"子产品「{sub}」"
    if not target:
        print(f"\n目标域[{dom}]没有 key,无需清理。")
        if sub == "" and by_sub:
            print("提示:该项目的 key 在子产品域,如需清空请加 --sub-product <子产品>(逐域清)。")
        return

    # 删前强制先导备份(可回灌格式);默认名带时间戳,永不互相覆盖
    backup = args.backup or (
        f"selectors_backup_p{args.project}_{sub or 'shared'}_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    if args.backup and os.path.exists(backup) and not args.force:
        sys.exit(f"备份文件已存在: {backup}(换 --backup 路径或加 --force 覆盖)")
    payload = {
        "_meta": {"project_id": args.project, "sub_product": sub, "count": len(target),
                  "source": base, "exported_at": datetime.now().isoformat(timespec="seconds"),
                  "note": "clear_project_selectors 删除前备份,可用 "
                          "import_selectors_from_file.py --overwrite 回灌"},
        "registry": _to_registry(target),
    }
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已导出备份({len(target)} 个 key)→ {backup}")

    print(f"\n将从[{dom}]删除 {len(target)} 个 key:")
    for r in target:
        print(f"  - {r['key']}  (page={r.get('page') or '—'}, frame={r.get('frame')})")

    if not args.apply:
        print(f"\n[dry-run] 未删除任何数据。核对上面清单无误后,加 --apply 执行。")
        print(_rollback_hint(base, args.username, args.project, backup))
        return

    if not args.yes:
        ans = input(f"\n⚠️ 即将从项目 {args.project} 的[{dom}]永久删除 {len(target)} 个 key,"
                    f"备份已存于 {backup}。输入 yes 确认: ")
        if ans.strip().lower() != "yes":
            sys.exit("已取消(未删除任何数据)。")

    deleted = failed = 0
    for r in target:
        try:
            _unwrap(sess.delete(f"{base}/api/selectors/{r['id']}", timeout=15))
            deleted += 1
        except (requests.RequestException, RuntimeError) as e:
            failed += 1
            print(f"  ✗ 删除 key「{r['key']}」(id={r['id']}) 失败: {e}")

    print(f"\n完成: 删除 {deleted} / 失败 {failed}。备份: {backup}")
    if failed:
        print("有失败项:重跑本命令会重新拉取当前状态、只删剩余项(会生成新备份)。")
    print(_rollback_hint(base, args.username, args.project, backup))


if __name__ == "__main__":
    main()
