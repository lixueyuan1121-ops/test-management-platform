"""批量回填端点自测(TestClient + 内存库 + 会爆炸的桩引擎)。
运行: cd backend && python -m scripts.test_backfill_bulk

验证「选择器有更新 → 用例联动更新」的批量段:POST /api/ai/testcases/backfill
扫项目内全部「选择器待补」用例,逐条用 revalidate_for_backfill 确定性校验:
- 旧 script 引用的 key 现已全部可用 → 恢复原意图 exec_kind、清待补标/上次失败原因、重打页面标;
- 仍缺 key / 无旧 script → 跳过(留给逐条 AI 重生),计入 remaining;
- 非待补用例完全不动。
桩引擎故意抛异常:一旦被调用即测试失败,证明批量回填全程不碰 AI。
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
from app.services import claude_runner as cr

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

# 可回填用例的旧 script:只引用 submitOrderBtn(测试里注册表恰好有它)。
_SCRIPT_OK = (
    '[{"action":"connect","target":{},"args":{},"desc":"连"},'
    '{"action":"click","target":{"key":"submitOrderBtn"},"args":{},"desc":"点下单"},'
    '{"action":"assert_visible","target":{"key":"submitOrderBtn"},"args":{},"desc":"看"}]'
)
# 仍缺 key 的旧 script:引用 stillMissingKey(注册表没有) → 回填校验必失败。
_SCRIPT_MISS = (
    '[{"action":"connect","target":{},"args":{},"desc":"连"},'
    '{"action":"click","target":{"key":"stillMissingKey"},"args":{},"desc":"点"},'
    '{"action":"assert_visible","target":{"key":"stillMissingKey"},"args":{},"desc":"看"}]'
)

_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# 用例1:待补,补齐后可回填 → 应恢复 gui。
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="下单按钮", steps="进入→点下单",
                exec_kind="manual", review_status="pending", script=_SCRIPT_OK,
                last_gen_error="上次失败留痕",
                kind_reason="[选择器待补] 补齐选择器 key:submitOrderBtn 后即可执行 gui"))
# 用例2:待补,但 key 仍缺 → 应保持 manual + 待补标不动。
_s.add(TestCase(id=2, ai_task_id=1, project_id=1, title="仍缺", steps="点",
                exec_kind="manual", review_status="pending", script=_SCRIPT_MISS,
                kind_reason="[选择器待补] 补齐选择器 key:stillMissingKey 后即可执行 e2e"))
# 用例3:正常 gui 用例(非待补) → 完全不动。
_s.add(TestCase(id=3, ai_task_id=1, project_id=1, title="正常", steps="点",
                exec_kind="gui", review_status="adopted", script=_SCRIPT_OK))
# 用例4:另一个项目的待补用例 → 不在本项目扫描范围。
_s.add(Project(id=2, name="Q", code="Q1", status="active"))
_s.add(AiTask(id=2, project_id=2, user_id=1, input_type="text", status="done"))
_s.add(TestCase(id=4, ai_task_id=2, project_id=2, title="别的项目", steps="点",
                exec_kind="manual", review_status="pending", script=_SCRIPT_OK,
                kind_reason="[选择器待补] 补齐选择器 key:submitOrderBtn 后即可执行 gui"))
_s.commit()


def _override_db():
    yield _s


def _boom(*a, **k):
    raise AssertionError("批量回填不应调用 AI 引擎")


# 桩引擎:可用,但 generate_script 一被调用就炸(证明批量回填是纯确定性校验)。
generators.get_provider = lambda name=None: SimpleNamespace(is_available=lambda: True, generate_script=_boom)
# 注册表口径打桩(免真实 DB):项目1 可用 key 只有 submitOrderBtn;页面映射供重打页面标。
cr._registered_keys = lambda pid=None: {"submitOrderBtn"} if pid == 1 else set()
cr._key_page_map = lambda pid=None: {"submitOrderBtn": "下单页"} if pid == 1 else {}

app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    r = client.post("/api/ai/testcases/backfill", params={"project_id": 1})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["restored"] == 1, f"应回填 1 条,实际 {d}"
    assert d["remaining"] == 1, f"应剩 1 条待补,实际 {d}"

    _s.expire_all()
    c1 = _s.get(TestCase, 1)
    assert c1.exec_kind == "gui", f"用例1应恢复 gui,实际 {c1.exec_kind}"
    assert c1.kind_reason is None, f"用例1待补标应清空,实际 {c1.kind_reason}"
    assert c1.last_gen_error is None, "用例1上次失败原因应清空"
    assert "submitOrderBtn" in (c1.script or ""), "用例1应沿用旧 script"
    assert c1.page == "下单页", f"用例1应按 key 重打页面标,实际 {c1.page}"

    c2 = _s.get(TestCase, 2)
    assert c2.exec_kind == "manual" and (c2.kind_reason or "").startswith("[选择器待补]"), \
        f"用例2应保持待补,实际 {c2.exec_kind}/{c2.kind_reason}"

    c3 = _s.get(TestCase, 3)
    assert c3.exec_kind == "gui" and c3.kind_reason is None, "用例3(非待补)不应被动"

    c4 = _s.get(TestCase, 4)
    assert c4.exec_kind == "manual", "别的项目的用例不应被动"

    # 幂等:再跑一次,无可回填。
    r2 = client.post("/api/ai/testcases/backfill", params={"project_id": 1})
    d2 = r2.json()["data"]
    assert d2["restored"] == 0 and d2["remaining"] == 1, f"幂等复跑应 0/1,实际 {d2}"
    print("OK test_backfill_bulk")


if __name__ == "__main__":
    main()
