"""gen-script 重生失败原因落库/成功清空 的端点自测(TestClient + 内存库 + 可切换桩引擎)。
运行: cd backend && python -m scripts.test_gen_script_last_error

验证:
  ① gui 用例重生失败 → last_gen_error 落库 + 接口返回;成功 → 清空。
  ② 选择器待补 manual 用例(批量路径):失败 → 落库 last_gen_error 且仍 manual、标识保留;
     成功 → 恢复 gui、清 kind_reason、清 last_gen_error。
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
# gen_script 已改为「关闭注入 session、AI 完成后另开 SessionLocal 写回」(连接释放修复)。
# 测试里把 SessionLocal 也指向内存库,使写回/落库失败原因命中同一 DB(StaticPool 共享连接)。
from app.api import ai as _ai_mod
_ai_mod.SessionLocal = _Session
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# ① 普通 gui 用例
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="gui 用例", steps="打开首页\n看导航",
                exec_kind="gui", review_status="pending"))
# ② 选择器待补降级用例(manual + 标识,批量重生路径)
_s.add(TestCase(id=2, ai_task_id=1, project_id=1, title="降级用例", steps="打开首页\n看导航",
                exec_kind="manual", review_status="pending",
                kind_reason="[选择器待补] 补齐选择器 key:navTasks 后即可执行 gui"))
_s.commit()


def _override_db():
    yield _s


# 可切换桩引擎:_MODE['ok'] 决定这次重生成功还是失败。
_MODE = {"ok": False}
_ERR = "step「assert_visible」用了未注册的 key「navTasks」(不在 selectors.json 注册表内)"
_OK_SCRIPT = [
    {"action": "connect", "target": {}, "args": {}, "desc": "连"},
    {"action": "assert_visible", "target": {"key": "navTasks"}, "args": {}, "desc": "看"},
]


def _gen(kind, title, steps, expected, project_id=None, timeout=None):
    return (_OK_SCRIPT, None) if _MODE["ok"] else ([], _ERR)


generators.get_provider = lambda name=None: SimpleNamespace(is_available=lambda: True, generate_script=_gen)

app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    # ---- ① gui 用例:先失败,落库 last_gen_error ----
    _MODE["ok"] = False
    r = client.post("/api/ai/testcases/1/gen-script")
    assert r.json()["code"] != 0, "引擎报错应返回失败"
    _s.expire_all()
    row = _s.get(TestCase, 1)
    assert row.last_gen_error and "未注册" in row.last_gen_error, f"失败原因应落库,实际 {row.last_gen_error!r}"
    assert row.script is None, "失败不应写坏 script"
    # 接口(GET 详情)返回 last_gen_error
    d = client.get("/api/ai/testcases/1").json()["data"]
    assert "last_gen_error" in d and d["last_gen_error"], f"接口应返回 last_gen_error,实际 {d.get('last_gen_error')!r}"

    # ---- ① gui 用例:补齐后成功,清空 last_gen_error ----
    _MODE["ok"] = True
    r = client.post("/api/ai/testcases/1/gen-script")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["last_gen_error"] is None, "重生成功应清空 last_gen_error"
    _s.expire_all()
    row = _s.get(TestCase, 1)
    assert row.last_gen_error is None and row.script, "DB 应清空错误并写入 script"

    # ---- ② 选择器待补 manual:失败 → 落库且仍 manual、标识保留 ----
    _MODE["ok"] = False
    r = client.post("/api/ai/testcases/2/gen-script")
    assert r.json()["code"] != 0, "仍缺 key 应失败"
    _s.expire_all()
    row = _s.get(TestCase, 2)
    assert row.exec_kind == "manual", "重生失败不应恢复类型"
    assert row.kind_reason and row.kind_reason.startswith("[选择器待补]"), "失败应保留待补标识"
    assert row.last_gen_error and "未注册" in row.last_gen_error, "失败原因应落库"

    # ---- ② 补齐后成功 → 恢复 gui + 清标识 + 清 last_gen_error ----
    _MODE["ok"] = True
    r = client.post("/api/ai/testcases/2/gen-script")
    d = r.json()["data"]
    assert d["exec_kind"] == "gui", f"应恢复 gui,实际 {d['exec_kind']}"
    assert d["selector_fix"] is False and not d["kind_reason"], "成功应清除待补标识"
    assert d["last_gen_error"] is None, "成功应清空 last_gen_error"
    _s.expire_all()
    row = _s.get(TestCase, 2)
    assert row.exec_kind == "gui" and row.kind_reason is None and row.last_gen_error is None and row.script

    print("OK test_gen_script_last_error")


if __name__ == "__main__":
    main()
