"""list_cases 的 exec_kind 支持多值(逗号分隔)过滤 自测(内存库 + TestClient)。
运行: cd backend && python -m scripts.test_list_cases_multi_kind

覆盖:
  A. exec_kind=单值 → 仍只返回该类型(向后兼容);
  B. exec_kind=逗号多值(gui,e2e) → 返回这些类型的并集,不含其它(api/manual);
  C. 不传 exec_kind → 全部类型都在。
"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_admin = User(id=1, username="admin", name="A", password_hash="x", is_platform_admin=True, status="active")
_s.add(_admin)
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# 每种类型一条用例
for cid, kind in [(201, "gui"), (202, "e2e"), (203, "api"), (204, "manual")]:
    _s.add(TestCase(id=cid, ai_task_id=1, project_id=1, title=f"{kind}用例", exec_kind=kind, review_status="pending"))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[get_current_user] = lambda: _admin
client = TestClient(app)


def _list(exec_kind=None):
    params = {"project_id": 1, "limit": 50}
    if exec_kind is not None:
        params["exec_kind"] = exec_kind
    r = client.get("/api/ai/cases", params=params)
    assert r.json()["code"] == 0, r.text
    return {it["exec_kind"] for it in r.json()["data"]["items"]}


def test_single_kind_backward_compatible():
    assert _list("gui") == {"gui"}, "单值 exec_kind 应只返回该类型"


def test_multi_kind_union():
    kinds = _list("gui,e2e")
    assert kinds == {"gui", "e2e"}, f"多值应返回并集,实际 {kinds}"
    assert "api" not in kinds and "manual" not in kinds


def test_no_kind_returns_all():
    assert _list() == {"gui", "e2e", "api", "manual"}, "不传 exec_kind 应返回全部类型"


def test_multi_kind_with_spaces():
    # 容错:值里有空格也应正确解析
    assert _list("gui, e2e") == {"gui", "e2e"}, "带空格的多值应被 trim 后正确过滤"


def main():
    test_single_kind_backward_compatible()
    test_multi_kind_union()
    test_no_kind_returns_all()
    test_multi_kind_with_spaces()
    print("OK test_list_cases_multi_kind")


if __name__ == "__main__":
    main()
