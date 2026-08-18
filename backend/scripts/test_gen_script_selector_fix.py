"""gen-script 对「选择器待补」降级用例一键重生的端点自测(TestClient + 内存库 + 桩引擎)。
运行: cd backend && python -m scripts.test_gen_script_selector_fix

验证:manual+选择器待补 → 重生按原意图 gui 生成 → 恢复 exec_kind=gui + 清除待补标识;
      纯 manual(非选择器降级)→ 仍 400。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import AiTask, TestCase, Project
from app.services import generators

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# ① 选择器待补降级用例(manual + 标识,script 空)
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="降级用例", steps="打开首页\n看导航",
                exec_kind="manual", review_status="pending",
                kind_reason="[选择器待补] 补齐选择器 key:navTasks 后即可执行 gui"))
# ② 纯人工用例(非选择器降级)
_s.add(TestCase(id=2, ai_task_id=1, project_id=1, title="纯人工", exec_kind="manual",
                review_status="pending", kind_reason="页面美观,主观判断"))
_s.commit()


def _override_db():
    yield _s


# 桩引擎:永远可用,返回一段合法 gui script(connect + assert_visible)
_STUB = SimpleNamespace(
    is_available=lambda: True,
    generate_script=lambda kind, title, steps, expected, project_id=None, timeout=None: (
        [{"action": "connect", "target": {}, "args": {}, "desc": "连"},
         {"action": "assert_visible", "target": {"key": "navTasks"}, "args": {}, "desc": "看"}], None),
)
generators.get_provider = lambda name=None: _STUB

app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    # ① 降级用例一键重生 → 200,恢复 gui,清标识,带 script
    r = client.post("/api/ai/testcases/1/gen-script")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["exec_kind"] == "gui", f"应恢复 gui,实际 {d['exec_kind']}"
    assert d["selector_fix"] is False, "重生成功应清除待补标识"
    assert not d["kind_reason"], f"kind_reason 应清空,实际 {d['kind_reason']}"
    assert d["script"], "应写入 script"
    # DB 落地核验
    _s.expire_all()
    row = _s.get(TestCase, 1)
    assert row.exec_kind == "gui" and row.kind_reason is None and row.script

    # ② 纯人工用例 → 400(不可重生)
    r2 = client.post("/api/ai/testcases/2/gen-script")
    assert r2.json()["code"] != 0, "纯 manual 应拒绝重生"

    print("OK test_gen_script_selector_fix")


if __name__ == "__main__":
    main()
