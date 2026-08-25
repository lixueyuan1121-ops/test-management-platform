"""选择器删除联动自测(TestClient + 内存库)。
运行: cd backend && python -m scripts.test_selector_usage_delete

验证「选择器有更新 → 用例联动更新」的删除段:
1. GET /api/selectors/{kid}/usage:返回引用该 key 的 gui/e2e 用例(id/title/exec_kind),
   供前端删除前展示影响范围;不含 manual(已降级)与其它项目的用例。
2. DELETE /api/selectors/{kid}:删 key 的同时把引用它的可执行 gui/e2e 用例降级 manual +
   写标准 [选择器待补] kind_reason(格式与 parse_testcases 一致,故待补筛选/badge/一键重生/
   批量回填全部自动适用);script 保留(供重新加回 key 后确定性回填)。响应带 downgraded。
   已是 manual/待补的用例不重复降级。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import AiTask, TestCase, Project, SelectorKey
from app.services.claude_runner import selector_fix_info

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

_SCRIPT_USES = (
    '[{"action":"connect","target":{},"args":{},"desc":"连"},'
    '{"action":"click","target":{"key":"submitOrderBtn"},"args":{},"desc":"点下单"},'
    '{"action":"assert_visible","target":{"key":"orderOkToast"},"args":{},"desc":"看"}]'
)
_SCRIPT_OTHER = (
    '[{"action":"connect","target":{},"args":{},"desc":"连"},'
    '{"action":"click","target":{"key":"navHome"},"args":{},"desc":"点"},'
    '{"action":"assert_visible","target":{"key":"navHome"},"args":{},"desc":"看"}]'
)
# 用例4 的 script:引用被删的 submitOrderBtn(验证已 manual 的用例删 key 时不被重复降级/覆盖标),
# 同时引用 oldMissing(与其 kind_reason 自洽,回填时始终缺 key → 不会被救回)。
_SCRIPT_OLDMISS = (
    '[{"action":"connect","target":{},"args":{},"desc":"连"},'
    '{"action":"click","target":{"key":"submitOrderBtn"},"args":{},"desc":"点"},'
    '{"action":"click","target":{"key":"oldMissing"},"args":{},"desc":"点"},'
    '{"action":"assert_visible","target":{"key":"oldMissing"},"args":{},"desc":"看"}]'
)

_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
_s.add(SelectorKey(id=11, project_id=1, sub_product="", key="submitOrderBtn", frame="auto",
                   candidates='[{"by": "text", "value": "下单"}]'))
# 用例1:gui,引用 submitOrderBtn → 应被联动降级。
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="下单成功", steps="点下单",
                exec_kind="gui", review_status="adopted", script=_SCRIPT_USES))
# 用例2:e2e,引用 submitOrderBtn → 也应降级(intended=e2e)。
_s.add(TestCase(id=2, ai_task_id=1, project_id=1, title="下单链路", steps="全链路",
                exec_kind="e2e", review_status="adopted", script=_SCRIPT_USES))
# 用例3:gui,不引用该 key → 不动。
_s.add(TestCase(id=3, ai_task_id=1, project_id=1, title="首页", steps="点",
                exec_kind="gui", review_status="adopted", script=_SCRIPT_OTHER))
# 用例4:已是 manual 的待补用例(引用该 key) → 不重复降级、kind_reason 不被覆盖。
_s.add(TestCase(id=4, ai_task_id=1, project_id=1, title="已待补", steps="点",
                exec_kind="manual", review_status="pending", script=_SCRIPT_OLDMISS,
                kind_reason="[选择器待补] 补齐选择器 key:oldMissing 后即可执行 gui"))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    # ---- usage:引用该 key 的可执行用例(1/2),不含未引用(3)与已 manual(4) ----
    r = client.get("/api/selectors/11/usage")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    ids = sorted(c["id"] for c in d["cases"])
    assert d["count"] == 2 and ids == [1, 2], f"usage 应命中用例1/2,实际 {d}"
    titles = {c["id"]: c["title"] for c in d["cases"]}
    assert titles[1] == "下单成功", d

    # ---- delete:级联降级 ----
    r = client.delete("/api/selectors/11")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["deleted"] == 11 and d["downgraded"] == 2, f"应联动降级 2 条,实际 {d}"

    _s.expire_all()
    assert _s.get(SelectorKey, 11) is None, "key 应已删除"

    c1 = _s.get(TestCase, 1)
    assert c1.exec_kind == "manual", f"用例1应降级 manual,实际 {c1.exec_kind}"
    sel_fix, keys, intended = selector_fix_info(c1.kind_reason)
    assert sel_fix and keys == ["submitOrderBtn"] and intended == "gui", \
        f"用例1待补标应可被 selector_fix_info 解析出 key+原意图,实际 {c1.kind_reason}"
    assert "submitOrderBtn" in (c1.script or ""), "script 应保留(供加回 key 后回填)"

    c2 = _s.get(TestCase, 2)
    sel_fix2, _k2, intended2 = selector_fix_info(c2.kind_reason)
    assert c2.exec_kind == "manual" and sel_fix2 and intended2 == "e2e", \
        f"用例2应降级且原意图 e2e,实际 {c2.exec_kind}/{c2.kind_reason}"

    c3 = _s.get(TestCase, 3)
    assert c3.exec_kind == "gui" and c3.kind_reason is None, "未引用该 key 的用例不应被动"

    c4 = _s.get(TestCase, 4)
    assert "oldMissing" in (c4.kind_reason or ""), "已待补用例的 kind_reason 不应被覆盖"

    # ---- 闭环:重新加回 key 后,批量回填应能救回这 2 条 ----
    _s.add(SelectorKey(id=12, project_id=1, sub_product="", key="submitOrderBtn", frame="auto",
                       candidates='[{"by": "text", "value": "下单"}]'))
    _s.add(SelectorKey(id=13, project_id=1, sub_product="", key="orderOkToast", frame="auto",
                       candidates='[{"by": "text", "value": "成功"}]'))
    _s.commit()
    from app.services import claude_runner as cr
    cr._registered_keys = lambda pid=None: {"submitOrderBtn", "orderOkToast"} if pid == 1 else set()
    r = client.post("/api/ai/testcases/backfill", params={"project_id": 1})
    d = r.json()["data"]
    assert d["restored"] == 2, f"加回 key 后批量回填应救回 2 条,实际 {d}"
    _s.expire_all()
    assert _s.get(TestCase, 1).exec_kind == "gui" and _s.get(TestCase, 2).exec_kind == "e2e", \
        "救回后应恢复各自原意图"
    print("OK test_selector_usage_delete")


if __name__ == "__main__":
    main()
