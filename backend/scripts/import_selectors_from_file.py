"""把本地整理好的选择器注册表(registry-json)导入线上平台的「选择器管理」。

走线上 HTTP API(不直连库,故本机即可运行,只要能访问到线上 backend)。文件格式
与内置 selectors.json 一致:{vmIframe?, coreKeys?, registry:{key:{frame,page,desc,candidates}}}。
导入为指定项目的【项目级共享】选择器(sub_product 默认空)。幂等:同名 key 默认跳过,
--overwrite 则以文件为准 PATCH 覆盖(frame/page/desc/candidates)。

用法(在 backend 目录;先 --list-projects 查项目号,再正式导):
    .venv/bin/python scripts/import_selectors_from_file.py \
        --base-url http://<线上ip>:8000 --username lixueyuan --password xxx --list-projects

    .venv/bin/python scripts/import_selectors_from_file.py \
        --base-url http://<线上ip>:8000 --username lixueyuan --password xxx \
        --project 1 --file /path/to/selector.json            # 同名跳过
    加 --overwrite 则同名以文件为准覆盖。

若本机访问不到线上 backend,可把本文件+selector.json 拷到服务器,--base-url 用
http://127.0.0.1:8000 在服务器上跑。
"""
import argparse
import json
import sys

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


def _existing_keys(sess, base, project_id, sub) -> dict:
    """返回该 (project, sub_product) 下 {key: id}。"""
    data = _unwrap(sess.get(f"{base}/api/selectors/manage",
                            params={"project_id": project_id}, timeout=15))
    rows = data.get("shared", []) if sub == "" else data.get("by_sub", {}).get(sub, [])
    return {r["key"]: r["id"] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="线上 backend 地址,如 http://10.x.x.x:8000")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--file", help="registry-json 文件路径")
    ap.add_argument("--project", type=int, help="导入目标 project_id")
    ap.add_argument("--sub-product", default="", help="子产品(默认空=项目级共享)")
    ap.add_argument("--overwrite", action="store_true", help="同名 key 以文件为准覆盖(默认跳过)")
    ap.add_argument("--list-projects", action="store_true", help="只列出项目后退出")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    sess = requests.Session()
    token = _login(sess, base, args.username, args.password)
    sess.headers["Authorization"] = f"Bearer {token}"
    print(f"登录成功 @ {base}")

    if args.list_projects:
        _list_projects(sess, base)
        return

    if args.project is None or not args.file:
        sys.exit("正式导入需 --project 和 --file(先用 --list-projects 查项目号)")

    with open(args.file, encoding="utf-8-sig") as f:  # utf-8-sig 去 BOM
        data = json.load(f)
    reg = data.get("registry", {})
    sub = args.sub_product
    print(f"文件含 {len(reg)} 个 key → 项目 {args.project} / "
          f"{'项目级共享' if sub == '' else '子产品:' + sub} / "
          f"{'覆盖' if args.overwrite else '跳过'}同名")

    existing = _existing_keys(sess, base, args.project, sub)
    created = updated = skipped = failed = 0
    for key, v in reg.items():
        payload = {
            "project_id": args.project, "sub_product": sub, "key": key,
            "frame": v.get("frame", "auto"), "page": v.get("page", ""),
            "desc": v.get("desc", ""), "candidates": v.get("candidates", []),
        }
        try:
            if key in existing:
                if not args.overwrite:
                    skipped += 1
                    continue
                _unwrap(sess.patch(f"{base}/api/selectors/{existing[key]}", json={
                    "frame": payload["frame"], "page": payload["page"],
                    "desc": payload["desc"], "candidates": payload["candidates"],
                }, timeout=15))
                updated += 1
            else:
                _unwrap(sess.post(f"{base}/api/selectors", json=payload, timeout=15))
                created += 1
        except (requests.RequestException, RuntimeError) as e:
            failed += 1
            print(f"  ✗ key「{key}」: {e}")

    # vmIframe → scope(仅非空时写)
    vm = data.get("vmIframe", "")
    if vm:
        try:
            _unwrap(sess.put(f"{base}/api/selectors/scope", json={
                "project_id": args.project, "sub_product": sub, "vm_iframe": vm}, timeout=15))
            print(f"vmIframe 已写入: {vm}")
        except (requests.RequestException, RuntimeError) as e:
            print(f"vmIframe 写入失败: {e}")

    print(f"\n完成: 新建 {created} / 覆盖 {updated} / 跳过 {skipped} / 失败 {failed}")


if __name__ == "__main__":
    main()
