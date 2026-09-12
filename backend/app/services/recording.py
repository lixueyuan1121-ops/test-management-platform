"""录制 → e2e 脚本组装 + 选择器回填。

核心:把 runner 回传的录制操作事件(每步含 action + 目标元素候选 + 可选断言)组装成结构化 script:
- 每步元素候选先与项目注册表比对 → 命中复用已有 key;未命中新建 key 名并**回填**(多候选)。
- action 映射:click→click / fill→fill / assert→assert_visible|assert_text。
纯函数(assemble_recording_steps)与 DB 落地(save_recording_as_case)分离,前者可脱库单测。
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.models import SelectorKey, TestCase, AiTask
from app.services.selector_ranking import valid_candidates, order_candidates, candidate_identity, is_active_candidate
from app.services.heal_selectors import apply_heal_items


def _cand_key(c: dict, frame: str = "auto") -> str:
    return json.dumps(["vm" if frame == "content" else frame, candidate_identity(c)], ensure_ascii=False)


def _is_unique_cand(c: dict) -> bool:
    # 仅用于身份复用；现场唯一性仍须由运行前的探测验证。
    by, val = c.get("by"), str(c.get("value") or "")
    return (by == "testid" or (by == "role" and bool(c.get("name")) and c.get("exact") is True)
            or (by == "css" and (val.startswith("#") or val.startswith("[data-test"))))


class KeyIndex(dict):
    def __init__(self):
        super().__init__()
        self.taken: set[str] = set()

    def add(self, candidate, frame, key):
        identity = _cand_key(candidate, frame)
        if identity not in self:
            self[identity] = key
        elif self[identity] != key:
            self[identity] = None  # 多个 key 指向同一候选，不能按查询顺序随便复用。


def to_camel(s: str) -> str:
    """kebab/空白 → camelCase(与扫描脚本 to_camel 口径一致)。非法字符去除。"""
    parts = re.split(r"[-_\s]+", str(s or "").strip())
    parts = [re.sub(r"[^0-9A-Za-z一-鿿]", "", p) for p in parts if p]
    if not parts:
        return ""
    return parts[0] + "".join(p[:1].upper() + p[1:] for p in parts[1:])


def build_key_index(db: Session, project_id: int, sub_product: str = "") -> KeyIndex:
    idx = KeyIndex()
    rows = (db.query(SelectorKey).filter(SelectorKey.project_id == project_id,
            SelectorKey.sub_product.in_(["", sub_product] if sub_product else [""])).all())
    merged = {}
    for row in sorted(rows, key=lambda r: r.sub_product != ""):
        merged[row.key] = row
        idx.taken.add(row.key)
    for key, row in merged.items():
        try:
            cands = json.loads(row.candidates or "[]")
        except (ValueError, TypeError):
            cands = []
        for c in valid_candidates(cands):
            if is_active_candidate(c) and _is_unique_cand(c):
                idx.add(c, row.frame or "auto", key)
    return idx


def _new_key_name(ev: dict, taken: set[str]) -> str:
    """为未命中的录制元素起唯一 key 名:优先 testid 值转 camel,退元素文本,再退 tag;重名加序号。"""
    testid = next((c.get("value") for c in (ev.get("candidates") or [])
                   if isinstance(c, dict) and c.get("by") == "testid" and c.get("value")), "")
    base = to_camel(testid) or to_camel(ev.get("text") or "") or (str(ev.get("tag") or "el").lower() + "El")
    base = base[:56] or "recEl"
    name = base
    i = 2
    while name in taken:
        name = f"{base}{i}"
        i += 1
    return name


def assemble_recording_steps(events: list, key_index: dict[str, str]) -> tuple[list, list, str]:
    """把录制事件组装成 (script步骤列表, 待回填新key列表, 预期结果文本)。纯函数。

    每步:action 映射 + 目标 key(命中已有 / 新建)。新建 key 收进 new_keys(供 apply_heal_items 回填)。
    预期结果 expected 由**断言步**汇总生成(assert_text→"出现文案X"、assert_visible→"X 可见"),
    无断言时返回空预期，由保存入口阻止成为可执行用例。
    script 首步补 connect。
    """
    steps: list = [{"action": "connect", "target": {}, "args": {}, "desc": "连接被测客户端"}]
    new_keys: list = []
    expects: list = []
    taken = set(getattr(key_index, "taken", ())) | {k for k in key_index.values() if k}
    key_index = dict(key_index)
    for ev in events or []:
        if not isinstance(ev, dict):
            raise ValueError("录制包含非法步骤，请重新检查")
        cands = valid_candidates(ev.get("candidates") or [])
        if not cands:
            raise ValueError("录制步骤缺少有效候选，不能丢弃步骤后保存为可执行用例")
        # 命中已注册 key?按候选顺序找第一个命中的。
        frame = ev.get("frame") or "auto"
        hits = {key_index.get(_cand_key(c, frame)) for c in cands if _is_unique_cand(c)} - {None}
        key = next(iter(hits)) if len(hits) == 1 else None
        if key is None:
            key = _new_key_name(ev, taken)
            taken.add(key)
            # 同一次录制后续点击/断言复用刚建立的身份；禁止跨 frame 复用。
            for c in cands:
                if _is_unique_cand(c):
                    key_index.setdefault(_cand_key(c, frame), key)
            page = str(ev.get("page") or "")
            ctrl = _control_type(ev)
            desc = f"[{ev.get('nav_tab','') or '?'}]-[{page or '?'}]-[{(ev.get('text') or '操作')[:12]}]-[{ctrl}]"
            new_keys.append({"key": key, "candidates": order_candidates(cands),
                             "page": page, "desc": desc, "frame": frame, "mode": "create"})
        else:
            # 命中已有 key:仍把本次捕获的候选并入(apply_heal_items 会合并按完整身份去重和语义质量排序)——
            # 修"key 早先由脆弱 css 建、之后重录也不补 xpath → 一直定位不到"。不传 desc/page,不动已有元信息。
            new_keys.append({"key": key, "candidates": order_candidates(cands), "frame": frame, "mode": "update"})
        action = ev.get("action") or "click"
        desc = (ev.get("text") or "").strip()[:40]
        if action == "fill":
            steps.append({"action": "fill", "target": {"key": key},
                          "args": {"text": ev.get("value") or ""}, "desc": desc or f"输入到 {key}"})
        elif action == "set_checked":
            steps.append({"action": action, "target": {"key": key}, "args": {"checked": ev.get("checked")}, "desc": desc or f"设置 {key} 勾选状态"})
        elif action == "select_option":
            steps.append({"action": action, "target": {"key": key}, "args": {"values": ev.get("values")}, "desc": desc or f"选择 {key} 选项"})
        elif action == "press":
            steps.append({"action": action, "target": {"key": key}, "args": {"key_name": ev.get("key_name")}, "desc": desc or f"按键 {ev.get('key_name')}"})
        elif action == "assert":
            a = ev.get("assert") or {}
            if a.get("kind") not in ("text", "visible"):
                raise ValueError("请选择有效断言类型")
            if a.get("kind") == "text" and not str(a.get("expected") or "").strip():
                raise ValueError("文本断言的预期内容不能为空")
            if a.get("kind") == "text":
                steps.append({"action": "assert_text", "target": {"key": key},
                              "args": {"expected": a["expected"], "contains": True}, "desc": desc or f"断言文本「{a['expected']}」"})
                expects.append(f"出现文案「{a['expected']}」")
            else:
                steps.append({"action": "assert_visible", "target": {"key": key}, "args": {}, "desc": desc or f"断言 {key} 可见"})
                expects.append(f"{desc or key} 可见")
        elif action == "click":
            steps.append({"action": "click", "target": {"key": key}, "args": {}, "desc": desc or f"点击 {key}"})
        else:
            raise ValueError(f"不支持的录制动作：{action}")
    expected = "；".join(expects)
    return steps, new_keys, expected


_CTRL_BY_TAG = {"input": "输入框", "textarea": "多行输入框", "select": "下拉列表", "button": "按钮", "a": "链接"}


def _control_type(ev: dict) -> str:
    """控件类型(四段式第4段);录制侧只有 tag/type,按 tag 粗判,判不出给「控件」。"""
    tag = str(ev.get("tag") or "").lower()
    typ = str(ev.get("type") or "").lower()
    if tag == "input" and typ in ("checkbox", "radio"):
        return "复选框" if typ == "checkbox" else "单选框"
    return _CTRL_BY_TAG.get(tag, "控件")


def _record_ai_task(db: Session, project_id: int, user_id: int | None) -> AiTask:
    """取/建该项目的「录制」合成 AiTask(kind='record')——test_case.ai_task_id 非空,录制用例挂它。
    共享一条,不污染 AI 战绩墙(战绩按 kind='testcase_gen' 统计)。"""
    at = (db.query(AiTask)
          .filter(AiTask.project_id == project_id, AiTask.kind == "record").first())
    if at:
        return at
    at = AiTask(project_id=project_id, user_id=user_id or 0, kind="record",
                input_type="text", status="done", input_ref="[录制生成]")
    db.add(at)
    db.flush()   # 拿到 id 供 test_case 外键
    return at


def save_recording_as_case(db: Session, project_id: int, sub_product: str, events: list,
                           title: str, task_id: int, created_by: int | None = None,
                           precondition: str | None = None) -> TestCase:
    """录制事件 → 组装 script(含预期结果) → 回填新 key(多候选) → 建 e2e 用例(挂 task_id)。

    task_id 必填:录制用例须归属某任务(与前端必填一致);已采纳默认,故建后补一条清单项(幂等)。
    返回 TestCase(未 commit,调用方提交)。
    """
    key_index = build_key_index(db, project_id, sub_product)
    steps, new_keys, expected = assemble_recording_steps(events, key_index)
    if not expected:
        raise ValueError("请至少添加一个有效断言后保存为自动化用例")
    # 回填新建 key(与执行期自愈同一函数:多候选、去重、testid>xpath>css 排序、四段式 desc)。
    if sub_product:
        local_names = {r.key for r in db.query(SelectorKey).filter_by(project_id=project_id, sub_product=sub_product).all()}
        # 共享 key 已可复用，不因录制子产品用例而生成仅含本次候选的同名覆盖。
        new_keys = [item for item in new_keys if item["mode"] != "update" or item["key"] in local_names]
    if new_keys:
        apply_heal_items(db, project_id, new_keys, updated_by=created_by, sub_product=sub_product)
    from app.services.claude_runner import _validate_script
    from app.services.selectors import usable_key_set
    normalized, error = _validate_script(steps, usable_key_set(db, project_id, sub_product))
    if error:
        raise ValueError(f"录制脚本不可执行：{error}")
    steps = normalized
    at = _record_ai_task(db, project_id, created_by)
    tc = TestCase(
        ai_task_id=at.id, project_id=project_id, sub_product=sub_product, task_id=task_id, title=(title or "录制用例")[:512],
        exec_kind="e2e", review_status="adopted", adopted=True,
        steps="\n".join(s["desc"] for s in steps if s.get("desc")),
        expected=expected,
        script=json.dumps(steps, ensure_ascii=False),
        precondition=(precondition or "").strip() or None,
    )
    db.add(tc)
    db.flush()   # 拿 tc.id 供清单项外键
    # 录制用例默认已采纳 + 关联任务 → 补一条验收清单项(与采纳回流口径一致,幂等)。
    from app.models import ChecklistItem
    exists = (db.query(ChecklistItem)
              .filter(ChecklistItem.task_id == task_id, ChecklistItem.test_case_id == tc.id).first())
    if exists is None:
        db.add(ChecklistItem(task_id=task_id, test_case_id=tc.id, project_id=project_id))
    return tc
