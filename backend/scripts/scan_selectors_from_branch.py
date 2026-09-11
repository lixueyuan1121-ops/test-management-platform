"""主动探测补充选择器:输入(平台已配的)代码分支 → 本地拉代码 → 扫 data-testid →
按规定格式(docs/选择器格式说明.md)生成可导入 JSON,可选直接导入平台。

设计要点(为什么这样)：
- **分支配在平台,不写死在脚本**:分支名存在「选择器管理」页(每个作用域一个 scan_branch)。
  脚本登录后从平台 GET /api/selectors/scan-config 读分支 → 换分支不用改脚本、发给同事即可用。
- **git 凭据用本机现成的**:脚本在你本机的 openclaw360-web 工作副本里 git fetch/checkout,
  用你本机已登录的 git 凭据,不需要在服务端配任何 git/DB 凭据(服务端多半也访问不到内网 GitLab)。
- **desc 复用已整理映射 + 自动兜底**:以 docs/testid-selectors-export-full.json(已人工整理的四段式
  [导航Tab]-[页面]-[场景]-[元素])为已知映射源,命中即复用;分支新出现、映射没有的 testid,按
  testid 词段自动拼一个尽量准的四段 desc,并在末尾打印「自动生成、待人工复核」清单。

用法(在 backend 目录;先 --list-projects 查项目号):
    python -m scripts.scan_selectors_from_branch \
        --base-url http://<平台ip>:8000 --username <u> --password <p> --list-projects

    # 生成文件(分支从平台读,不传 --branch):
    python -m scripts.scan_selectors_from_branch \
        --base-url http://<平台ip>:8000 --username <u> --password <p> \
        --project 1 --repo D:/git/openclaw360-web/openclaw360-web \
        --out docs/selectors-scanned.json

    # 生成并直接导入平台当前作用域(同名跳过;加 --overwrite 覆盖):
    python -m scripts.scan_selectors_from_branch ... --project 1 --repo <path> --import

参数:
    --repo         本机 openclaw360-web 工作副本目录(内含 src/test-ids/bindings.ts)
    --branch       覆盖平台配置的扫描分支(不传则用平台 scan-config 的 scan_branch)
    --sub-product  作用域(默认空=项目级共享);要与平台上配 scan_branch 的作用域一致
    --no-git       跳过 git fetch/checkout,直接扫当前工作副本(离线/无凭据时用)
    --out          输出文件路径(默认 docs/selectors-scanned-<branch>.json)
    --import       生成后直接 POST /api/selectors/import 入库
    --overwrite    导入时同名 key 以扫描为准覆盖(默认跳过)
"""
import argparse
import json
import os
import re
import subprocess
import sys

import requests

# testid 后缀 → 元素名(第四段)。按最长后缀优先匹配(-search-input 先于 -input)。
_ELEM_BY_SUFFIX = [
    ("search-input", "搜索输入框"), ("search-clear", "清空按钮"), ("name-input", "名称输入框"),
    ("desc-input", "描述输入框"), ("contact-input", "联系输入框"), ("query-input", "查询输入框"),
    ("textarea", "多行输入框"), ("input", "输入框"),
    ("submit", "提交按钮"), ("confirm", "确认按钮"), ("cancel", "取消按钮"),
    ("close", "关闭按钮"), ("save", "保存按钮"), ("delete", "删除按钮"),
    ("add-btn", "添加按钮"), ("create-btn", "创建按钮"), ("refresh-btn", "刷新按钮"),
    ("more-btn", "更多按钮"), ("back-btn", "返回按钮"), ("done-btn", "完成按钮"),
    ("btn", "按钮"), ("button", "按钮"),
    ("modal", "弹窗容器"), ("overlay", "遮罩层"), ("popover", "浮层容器"), ("panel", "面板容器"),
    ("dropdown-item", "菜单项"), ("menu-item", "菜单项"), ("dropdown", "下拉菜单"), ("menu", "菜单容器"),
    ("tabs", "Tab栏"), ("tab", "Tab项"),
    ("card-title", "卡片标题"), ("card", "卡片"),
    ("row-title", "列表项标题"), ("row", "列表项"), ("list", "列表容器"),
    ("title", "标题"), ("subtitle", "副标题"), ("empty", "空状态"),
    ("search", "搜索输入框"), ("select", "下拉选择"), ("checkbox", "复选框"),
    ("icon", "图标"), ("avatar", "头像"), ("badge", "徽标"), ("chip", "标签"),
    ("view", "页面容器"), ("page", "页面容器"), ("root", "页面容器"), ("section", "区域容器"),
    ("item", "列表项"),
]

# testid 前缀 → (导航Tab, 页面) 兜底归类。命中最长前缀优先。
_TAB_BY_PREFIX = [
    ("nav-", ("全局", "左侧导航栏")), ("aside-", ("全局", "左侧栏")),
    ("modal-", ("全局", "弹窗")),
    ("task-search", ("任务", "任务搜索弹窗")), ("task-", ("任务", "任务列表")),
    ("history-", ("任务", "任务页")),
    ("home-", ("首页", "技能引导首页")),
    ("automation-", ("自动化", "自动化页")), ("acm-", ("自动化", "自动化创建弹窗")),
    ("expert-", ("专家", "专家广场")), ("creator-", ("专家", "专家创建页")),
    ("swarm-", ("专家", "专家团面板")), ("skills-", ("专家", "技能广场")),
    ("agent-", ("专家", "专家页")),
    ("project-", ("项目", "项目页")),
    ("cloud-", ("文件", "文件页")), ("kb-", ("知识库", "知识库页")),
    ("links-", ("连接器", "连接器页")), ("channel-", ("连接器", "连接器页")),
    ("usage-", ("设置", "用量页")), ("user-menu", ("设置", "头像菜单")),
    ("sessions-", ("会话", "会话列表页")), ("session-", ("会话", "会话列表页")),
    ("chat-", ("会话", "会话页")), ("compose-", ("会话", "聊天输入框")),
    ("slash-", ("会话", "斜杠技能菜单")), ("attachment", ("会话", "聊天输入框")),
    ("image-attachment", ("会话", "聊天输入框")), ("message-", ("会话", "聊天输入框")),
    ("send-", ("会话", "聊天输入框")), ("abort-", ("会话", "聊天输入框")),
]

# 语义纠正:某些词段本身即元素/场景。仅用于第三段「场景」的粗略填充。
_SCENE_HINT = [
    ("empty", "空状态"), ("search", "搜索"), ("create", "新建"), ("add", "添加"),
    ("delete", "删除"), ("edit", "编辑"), ("submit", "提交"), ("close", "关闭"),
    ("open", "打开"), ("confirm", "确认"), ("cancel", "取消"), ("save", "保存"),
]


def parse_testids(text: str) -> list[str]:
    """从 bindings.ts 文本解析全部 data-testid 字符串(去重保序)。

    绑定形如 `testId: "message-input"`(双引号);类型定义 `testId: string;` 无引号,天然不匹配。
    resolveComposeSendButton 里 send-button/abort-button 亦以 `testId: "..."` 绑定,一并覆盖。
    """
    seen, out = set(), []
    for m in re.finditer(r'testId:\s*"([^"]+)"', text):
        tid = m.group(1)
        if tid not in seen:
            seen.add(tid)
            out.append(tid)
    return out


def to_camel(testid: str) -> str:
    """kebab testid → camelCase key,与 gen_export.py 口径一致。"""
    parts = testid.split("-")
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def _match_longest(testid: str, table, key_is_prefix: bool):
    """在 (片段, 值) 表里按最长匹配命中(prefix 或 suffix),返回值或 None。"""
    best = None
    for frag, val in table:
        hit = testid.startswith(frag) if key_is_prefix else testid.endswith(frag)
        if hit and (best is None or len(frag) > best[0]):
            best = (len(frag), val)
    return best[1] if best else None


def auto_desc(testid: str) -> str:
    """为映射表未覆盖的 testid 自动拼四段式 [导航Tab]-[页面]-[场景]-[元素]。

    尽量准但不保证——故调用方会把自动生成的标出来给人工复核。desc 本身保持干净(不加标记)。
    """
    tab_page = _match_longest(testid, _TAB_BY_PREFIX, key_is_prefix=True) or ("未分类", "未分类")
    tab, page = tab_page
    elem = _match_longest(testid, _ELEM_BY_SUFFIX, key_is_prefix=False) or "控件"
    scene = None
    for frag, val in _SCENE_HINT:
        if frag in testid:
            scene = val
            break
    if scene is None:
        scene = f"操作{elem}"
    return f"[{tab}]-[{page}]-[{scene}]-[{elem}]"


def load_known_map(path: str) -> dict:
    """从已整理的四段式导出(testid-selectors-export-full.json)建 testid → {page,desc,frame}。"""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    known = {}
    for v in (data.get("registry") or {}).values():
        cands = v.get("candidates") or []
        tid = cands[0].get("value") if cands else None
        if tid:
            known[tid] = {"page": v.get("page", ""), "desc": v.get("desc", ""),
                          "frame": v.get("frame", "vm")}
    return known


def build_registry(testids: list[str], known_map: dict) -> tuple[dict, list[str]]:
    """testid 列表 → 注册表 {key:{frame,page,desc,candidates}}。

    命中 known_map 复用其 page/desc/frame;未命中则自动兜底 desc + page 从四段 desc 反取。
    返回 (registry, auto_keys):auto_keys 是自动生成 desc 的 key 名(待人工复核)。
    """
    registry, auto_keys = {}, []
    for tid in testids:
        key = to_camel(tid)
        known = known_map.get(tid)
        if known:
            frame, page, desc = known["frame"] or "vm", known.get("page", ""), known["desc"]
        else:
            desc = auto_desc(tid)
            # page 取四段 desc 的第二段(页面),与已整理条目口径一致。
            page = desc.split("]-[")[1] if "]-[" in desc else ""
            frame = "vm"
            auto_keys.append(key)
        registry[key] = {"frame": frame, "page": page, "desc": desc,
                         "candidates": [{"by": "testid", "value": tid}]}
    return registry, auto_keys


# ---------------- 以下为脚本执行部分(HTTP + git),纯逻辑在上面便于单测) ----------------


def _unwrap(resp: requests.Response):
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


def _git(repo: str, *args: str) -> str:
    """在 repo 里跑 git,返回 stdout;失败抛 RuntimeError(带 stderr)。"""
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _checkout_branch(repo: str, branch: str):
    """fetch + checkout + 快进拉取到指定分支(用本机 git 凭据)。"""
    print(f"[git] fetch origin {branch} …")
    _git(repo, "fetch", "origin", branch)
    _git(repo, "checkout", branch)
    try:
        _git(repo, "pull", "--ff-only", "origin", branch)
    except RuntimeError as e:
        print(f"[git] pull --ff-only 跳过({e});用当前 checkout 内容继续")
    head = _git(repo, "rev-parse", "--short", "HEAD")
    print(f"[git] 现在在 {branch} @ {head}")


def main():
    # 脚本会打印中文;Windows 默认 GBK 控制台遇到少数字符会崩,统一改 UTF-8(容错替换)。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--project", type=int)
    ap.add_argument("--sub-product", default="")
    ap.add_argument("--repo", help="本机 openclaw360-web 工作副本目录")
    ap.add_argument("--branch", help="覆盖平台配置的扫描分支")
    ap.add_argument("--no-git", action="store_true", help="不 fetch/checkout,直接扫当前工作副本")
    ap.add_argument("--known-map", default=None,
                    help="已整理四段式映射源(默认 docs/testid-selectors-export-full.json)")
    ap.add_argument("--out", help="输出文件路径")
    ap.add_argument("--import", dest="do_import", action="store_true", help="生成后直接导入平台")
    ap.add_argument("--overwrite", action="store_true", help="导入同名 key 覆盖(默认跳过)")
    ap.add_argument("--list-projects", action="store_true")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    sess = requests.Session()
    token = _login(sess, base, args.username, args.password)
    sess.headers["Authorization"] = f"Bearer {token}"
    print(f"登录成功 @ {base}")

    if args.list_projects:
        data = _unwrap(sess.get(f"{base}/api/projects", timeout=15))
        rows = data if isinstance(data, list) else data.get("items", data)
        print(f"可选项目({len(rows)}):")
        for p in rows:
            print(f"  id={p.get('id')}  name={p.get('name')}  code={p.get('code')}")
        return

    if not args.project or not args.repo:
        sys.exit("需 --project 和 --repo(先用 --list-projects 查项目号)")

    # 分支:命令行 > 平台 scan-config。
    branch = args.branch
    if not branch:
        cfg = _unwrap(sess.get(f"{base}/api/selectors/scan-config",
                               params={"project_id": args.project, "sub_product": args.sub_product},
                               timeout=15))
        branch = (cfg or {}).get("scan_branch", "").strip()
        if not branch:
            sys.exit("平台未配置扫描分支(去『选择器管理』填「扫描分支」并保存,或用 --branch 指定)")
        print(f"从平台读取扫描分支: {branch}")

    if not args.no_git:
        _checkout_branch(args.repo, branch)

    bindings = os.path.join(args.repo, "src", "test-ids", "bindings.ts")
    if not os.path.exists(bindings):
        sys.exit(f"未找到 {bindings}(--repo 是否指向 openclaw360-web 工作副本?)")
    with open(bindings, encoding="utf-8") as f:
        testids = parse_testids(f.read())
    print(f"扫到 {len(testids)} 个 data-testid")

    known_default = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "docs", "testid-selectors-export-full.json"))
    known_map = load_known_map(args.known_map or known_default)
    registry, auto_keys = build_registry(testids, known_map)
    print(f"复用已整理 desc {len(registry) - len(auto_keys)} 个,自动生成 {len(auto_keys)} 个")

    vm_iframe = 'iframe[src*=".work.n.cn"]'
    out_doc = {
        "_comment": f"openclaw360-web {branch} 分支 data-testid 扫描导出({len(registry)}个)。"
                    "格式见 docs/选择器格式说明.md。",
        "vmIframe": vm_iframe,
        "coreKeys": [],
        "registry": registry,
    }
    out_path = args.out or os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "docs", f"selectors-scanned-{branch.replace('/', '_')}.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_doc, f, ensure_ascii=False, indent=2)
    print(f"已写出 {out_path}")

    if auto_keys:
        print(f"\n[!] 以下 {len(auto_keys)} 个 desc 为自动生成,请人工复核后再入库:")
        for k in auto_keys:
            print(f"  {k}: {registry[k]['desc']}")

    if args.do_import:
        res = _unwrap(sess.post(f"{base}/api/selectors/import", json={
            "project_id": args.project, "sub_product": args.sub_product,
            "registry": registry, "vm_iframe": vm_iframe, "overwrite": args.overwrite,
        }, timeout=60))
        print(f"\n导入完成: 新增 {res['imported']} / 覆盖 {res['updated']} / "
              f"跳过 {res['skipped']} / 非法 {len(res['invalid'])}")
        # 补 key 后联动回填「选择器待补」用例:引用的 key 现已注册 → 自动恢复 gui/e2e 可执行。
        try:
            bf = _unwrap(sess.post(f"{base}/api/ai/testcases/backfill",
                                   params={"project_id": args.project}, timeout=60))
            if bf and bf.get("restored"):
                print(f"联动回填: 恢复 {bf['restored']} 条「选择器待补」用例"
                      f"{('，仍有 %d 条待补' % bf['remaining']) if bf.get('remaining') else ''}")
        except (requests.RequestException, RuntimeError) as e:
            print(f"回填跳过（不影响导入）: {e}")


if __name__ == "__main__":
    main()
