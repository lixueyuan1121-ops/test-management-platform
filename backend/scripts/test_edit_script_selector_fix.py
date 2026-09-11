"""编辑用例改 script 时,未注册 key → 降级「选择器待补」保存(而非 400 拒绝);硬错误仍拒绝。
运行: cd backend && python -m scripts.test_edit_script_selector_fix  (需 httpx)

背景:用户改用例引入新 key(未注册)时,应允许保存 + 标待补(补齐即可执行),不该阻挡。
与生成侧口径一致(生成侧遇未注册 key 也是降级待补,非拒绝)。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, SelectorKey
from app.services.claude_runner import _SELECTOR_FIX_MARK

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
import app.db.session as dbs
dbs.SessionLocal = _Session
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
_s.add(SelectorKey(project_id=1, sub_product="", key="navTasks", frame="auto",
                   candidates='[{"by": "css", "value": ".nav-tasks"}]'))
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="T", exec_kind="gui", review_status="pending"))
_s.commit()

app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = lambda: (yield _s)
client = TestClient(app)


def main():
    import json
    # ① 改 script 引入未注册 key「brandNewKey」→ 应保存成功 + 降级 manual + 标待补(不 400)
    script = [
        {"action": "connect", "target": {}, "args": {}, "desc": "连"},
        {"action": "click", "target": {"key": "brandNewKey"}, "args": {}, "desc": "点新元素"},
        {"action": "assert_visible", "target": {"key": "brandNewKey"}, "args": {}, "desc": "看"},
    ]
    r = client.patch("/api/ai/testcases/1", json={"script": script})
    assert r.json()["code"] == 0, f"未注册 key 应允许保存(降级待补),不该 400:{r.text}"
    d = r.json()["data"]
    assert d["exec_kind"] == "manual", f"应降级 manual,实际 {d['exec_kind']}"
    assert _SELECTOR_FIX_MARK in (d.get("kind_reason") or ""), f"应标选择器待补,实际 {d.get('kind_reason')}"
    assert "brandNewKey" in (d.get("kind_reason") or ""), "应列出缺的 key"

    # ② 硬错误(无断言步)→ 仍应 400 拒绝
    bad = [{"action": "connect", "target": {}, "args": {}, "desc": "连"},
           {"action": "click", "target": {"key": "navTasks"}, "args": {}, "desc": "点"}]  # 无断言
    r2 = client.patch("/api/ai/testcases/1", json={"script": bad})
    assert r2.json()["code"] != 0, "无断言的硬错误应仍拒绝"

    print("OK test_edit_script_selector_fix")


if __name__ == "__main__":
    main()
