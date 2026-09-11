"""选择器批量删除 + 手动导入 + 扫描分支配置 自测(TestClient + 内存库)。
运行: cd backend && python -m scripts.test_selector_batch_import
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import AiTask, TestCase, Project, SelectorKey, SelectorScope

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

_SCRIPT = (
    '[{"action":"click","target":{"key":"btnA"},"args":{},"desc":"点"},'
    '{"action":"assert_visible","target":{"key":"btnA"},"args":{},"desc":"看"}]'
)

_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
_s.add(SelectorKey(id=21, project_id=1, sub_product="", key="btnA", frame="auto",
                   candidates='[{"by": "text", "value": "A"}]'))
_s.add(SelectorKey(id=22, project_id=1, sub_product="", key="btnB", frame="auto",
                   candidates='[{"by": "text", "value": "B"}]'))
# gui 用例引用 btnA → 批量删 btnA 时应联动降级。
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="用A", steps="点",
                exec_kind="gui", review_status="adopted", script=_SCRIPT))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def test_batch_delete():
    r = client.post("/api/selectors/batch-delete", json={"ids": [21, 22, 999]})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["deleted"] == 2 and d["downgraded"] == 1 and d["missing"] == [999], d
    _s.expire_all()
    assert _s.get(SelectorKey, 21) is None and _s.get(SelectorKey, 22) is None
    assert _s.get(TestCase, 1).exec_kind == "manual", "引用被删 key 的用例应降级"
    # 空 ids → 422(min_length=1)
    assert client.post("/api/selectors/batch-delete", json={"ids": []}).status_code == 422
    print("OK batch_delete")


def test_import_and_scope():
    # 先配扫描分支 + vm_iframe
    r = client.put("/api/selectors/scope", json={
        "project_id": 1, "sub_product": "", "vm_iframe": "iframe.x", "scan_branch": "feature/x"})
    assert r.json()["data"]["scan_branch"] == "feature/x", r.text
    # scan-config 能读回
    r = client.get("/api/selectors/scan-config", params={"project_id": 1})
    assert r.json()["data"]["scan_branch"] == "feature/x"

    # 仅传 vm_iframe(scan_branch=None)不应清掉已存分支
    client.put("/api/selectors/scope", json={"project_id": 1, "sub_product": "", "vm_iframe": "iframe.y"})
    r = client.get("/api/selectors/scan-config", params={"project_id": 1})
    assert r.json()["data"]["scan_branch"] == "feature/x", "只存 vm_iframe 不应清分支"

    # 导入:新增 2 + 1 个非法(候选缺 value)
    reg = {
        "navHome": {"frame": "vm", "page": "首页", "desc": "[全局]-[导航]-[进入]-[按钮]",
                    "candidates": [{"by": "testid", "value": "nav-home"}]},
        "navHist": {"frame": "vm", "page": "任务", "desc": "d2",
                    "candidates": [{"by": "testid", "value": "nav-history"}]},
        "bad": {"frame": "vm", "candidates": [{"by": "css"}]},  # 缺 value → 非法
    }
    r = client.post("/api/selectors/import", json={
        "project_id": 1, "sub_product": "", "registry": reg, "vm_iframe": "iframe[work]"})
    d = r.json()["data"]
    assert d["imported"] == 2 and d["invalid"] == ["bad"], d

    # 再次导入同名:默认跳过
    r = client.post("/api/selectors/import", json={"project_id": 1, "sub_product": "", "registry": {
        "navHome": {"desc": "改了", "candidates": [{"by": "testid", "value": "nav-home2"}]}}})
    assert r.json()["data"]["skipped"] == 1 and r.json()["data"]["imported"] == 0

    # overwrite=True 覆盖
    r = client.post("/api/selectors/import", json={"project_id": 1, "sub_product": "", "overwrite": True,
        "registry": {"navHome": {"desc": "已改", "candidates": [{"by": "testid", "value": "nav-home2"}]}}})
    assert r.json()["data"]["updated"] == 1, r.text
    _s.expire_all()
    row = _s.query(SelectorKey).filter_by(project_id=1, sub_product="", key="navHome").first()
    assert row.desc == "已改", row.desc
    print("OK import_and_scope")


def test_manage_returns_scope():
    r = client.get("/api/selectors/manage", params={"project_id": 1, "sub_product": ""})
    d = r.json()["data"]
    assert "scope" in d and d["scope"]["scan_branch"] == "feature/x", d.get("scope")
    print("OK manage_returns_scope")


def main():
    test_batch_delete()
    test_import_and_scope()
    test_manage_returns_scope()
    print("ALL OK test_selector_batch_import")


if __name__ == "__main__":
    main()
