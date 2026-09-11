"""模块入口 API：upsert(建/改同一 page 不重复)、列表带 key_count、删除。
运行: cd backend && python -m scripts.test_module_entry_api  (需 httpx)
"""
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, SelectorKey

eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(eng)
S = sessionmaker(bind=eng)
s = S()
s.add(Project(id=2, name="PC", code="test", status="active"))
s.add(SelectorKey(project_id=2, sub_product="", key="automationCreateBtn", page="自动化", candidates="[]"))
s.commit()

app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = lambda: (yield s)
client = TestClient(app)

def main():
    r = client.post("/api/modules", json={"project_id": 2, "page": "自动化",
                                          "nav_keys": ["navAutomation"], "ready_key": "automationPageTitle"})
    assert r.json()["code"] == 0, r.text
    r2 = client.post("/api/modules", json={"project_id": 2, "page": "自动化",
                                           "nav_keys": ["navAuto2"], "ready_key": "t2"})
    assert r2.json()["code"] == 0
    lst = client.get("/api/modules", params={"project_id": 2}).json()["data"]
    assert len(lst) == 1, f"upsert 应不新增,实际 {len(lst)}"
    assert lst[0]["nav_keys"] == ["navAuto2"], lst[0]
    assert lst[0]["key_count"] == 1, "自动化模块下有 1 个 selector_key"
    mid = lst[0]["id"]
    assert client.delete(f"/api/modules/{mid}").json()["code"] == 0
    assert client.get("/api/modules", params={"project_id": 2}).json()["data"] == []
    print("OK test_module_entry_api")

if __name__ == "__main__":
    main()
