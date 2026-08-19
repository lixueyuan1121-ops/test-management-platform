"""用例 ↔ 选择器页面挂钩 自测(内存库 + 桩引擎)。
运行: cd backend && python -m scripts.test_page_hook

覆盖:
  A. parse_testcases 按 script 用到的 key 自动推断 page(单页/跨页并集/无 key→None)。
  B. build_testcase_prompt 按 pages 收窄注入的 key(选中页 + 未分类,排除他页)。
  C. 端点:list 按 page 精确筛选;gen_script 重生后按新 script 的 key 重新推断 page;_to_case_out 返回 page。
"""
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.db.session as dbs
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, SelectorKey

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
# 生成器内部自开的 SessionLocal(读 key/page)与 gen_script 写回都改指向内存库(StaticPool 共享连接)。
dbs.SessionLocal = _Session
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# 选择器:navTasks/submitBtn→任务页, loginBtn→登录页, connectAnchor→未分类('')
for k, pg in [("navTasks", "任务页"), ("submitBtn", "任务页"), ("loginBtn", "登录页"), ("connectAnchor", "")]:
    _s.add(SelectorKey(project_id=1, sub_product="", key=k, frame="auto", page=pg, desc=k, candidates="[]"))
_s.commit()

from app.services import claude_runner as cr  # noqa: E402  (须在 patch SessionLocal 后 import 使用)


def _case(kind, title, steps_script, priority="P1"):
    return {"category": "功能", "title": title, "steps": "步骤", "expected": "预期",
            "priority": priority, "kind": kind, "kind_reason": "界面可断言", "script": steps_script}


def test_parse_infers_page():
    # ① gui 用 navTasks(任务页) → page=任务页
    raw = json.dumps([_case("gui", "看任务导航", [
        {"action": "connect", "target": {}, "args": {}, "desc": "连"},
        {"action": "assert_visible", "target": {"key": "navTasks"}, "args": {}, "desc": "看"},
    ])], ensure_ascii=False)
    c = cr.parse_testcases(raw, project_id=1)
    assert len(c) == 1 and c[0]["page"] == "任务页", c

    # ② 跨页 e2e 用 loginBtn(登录页)+navTasks(任务页) → 并集按出现序
    raw2 = json.dumps([_case("e2e", "登录到任务", [
        {"action": "connect", "target": {}, "args": {}, "desc": "连"},
        {"action": "click", "target": {"key": "loginBtn"}, "args": {}, "desc": "登录"},
        {"action": "click", "target": {"key": "navTasks"}, "args": {}, "desc": "进任务"},
        {"action": "assert_visible", "target": {"key": "submitBtn"}, "args": {}, "desc": "看提交"},
        {"action": "assert_visible", "target": {"key": "navTasks"}, "args": {}, "desc": "再看"},
    ], priority="P0")], ensure_ascii=False)
    c2 = cr.parse_testcases(raw2, project_id=1)
    assert c2[0]["page"] == "登录页,任务页", c2

    # ③ manual(无 key)→ page None
    raw3 = json.dumps([_case("manual", "页面美观", [])], ensure_ascii=False)
    assert cr.parse_testcases(raw3, project_id=1)[0]["page"] is None


def test_prompt_narrowing():
    full = cr.build_testcase_prompt("需求", project_id=1)
    assert "navTasks" in full and "loginBtn" in full and "connectAnchor" in full, "不收窄应含全部 key"
    only_task = cr.build_testcase_prompt("需求", project_id=1, pages=["任务页"])
    assert "navTasks" in only_task and "submitBtn" in only_task, "应含任务页 key"
    assert "connectAnchor" in only_task, "未分类 key 应始终带上"
    assert "loginBtn" not in only_task, "登录页 key 应被收窄掉"


# ---- 端点测试 ----
from app.main import app  # noqa: E402
from app.core.deps import get_current_user  # noqa: E402
from app.api import ai as ai_mod  # noqa: E402
from app.services import generators  # noqa: E402

ai_mod.SessionLocal = _Session  # gen_script 写回内存库


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def test_endpoint_list_filter_and_regen():
    _s.add(TestCase(id=101, ai_task_id=1, project_id=1, title="任务页用例", exec_kind="gui",
                    review_status="pending", page="任务页"))
    _s.add(TestCase(id=102, ai_task_id=1, project_id=1, title="登录页用例", exec_kind="gui",
                    review_status="pending", page="登录页"))
    _s.commit()

    # list 按 page 精确筛选:只出 101
    d = client.get("/api/ai/cases", params={"project_id": 1, "page": "任务页"}).json()["data"]
    ids = [c["id"] for c in d["items"]]
    assert 101 in ids and 102 not in ids, d
    assert next(c for c in d["items"] if c["id"] == 101)["page"] == "任务页"

    # gen_script 重生:stub 返回用 loginBtn 的 script → page 重新推断为 登录页
    _stub = SimpleNamespace(
        is_available=lambda: True,
        generate_script=lambda kind, title, steps, expected, project_id=None, timeout=None: (
            [{"action": "connect", "target": {}, "args": {}, "desc": "连"},
             {"action": "assert_visible", "target": {"key": "loginBtn"}, "args": {}, "desc": "看"}], None),
    )
    generators.get_provider = lambda name=None: _stub
    r = client.post("/api/ai/testcases/101/gen-script")
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["page"] == "登录页", f"重生应把 page 重新推断为登录页,实际 {r.json()['data']['page']}"


def main():
    test_parse_infers_page()
    test_prompt_narrowing()
    test_endpoint_list_filter_and_regen()
    print("OK test_page_hook")


if __name__ == "__main__":
    main()
