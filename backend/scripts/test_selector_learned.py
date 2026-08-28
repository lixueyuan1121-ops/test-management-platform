"""运行时自学习候选(selector learned)闭环自测。
运行: cd backend && python -m scripts.test_selector_learned

覆盖:
- runner 上报:新候选 → 建 pending 行 + 追加注册表尾部(带 src:learned 试用标)
- 幂等去重:同候选重复上报只 hit_count+1,不重复追加
- 试用位上限:每 key 至多 2 个 learned 候选在注册表挂着,超出只记行不追加
- 评审:approve 去掉试用标(转正);reject 从注册表移除候选,且同候选再上报不再入注册表
- 无效候选剔除;未注册 key 只记行不追加;鉴权(错 token 401)
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.enums import ProjectRole
from app.db.session import Base, get_db
from app.main import app
from app.models import Project, ProjectMember, SelectorKey, SelectorLearned, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
client = TestClient(app)

RUNNER_H = {"Authorization": "Bearer test-runner-token"}


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="纳米Work", code="nw"),
    ])
    _s.flush()
    _s.add(ProjectMember(user_id=1, project_id=100, role=ProjectRole.admin))
    _s.add(SelectorKey(project_id=100, sub_product="", key="sendBtn", desc="「发送」按钮",
                       candidates=json.dumps([{"by": "css", "value": ".send-old"}])))
    _s.commit()
    settings.RUNNER_TOKEN = "test-runner-token"


def _key_cands():
    sk = _s.query(SelectorKey).filter_by(project_id=100, key="sendBtn").first()
    _s.refresh(sk)
    return json.loads(sk.candidates)


def _report(items, run_id=None):
    return client.post("/api/selectors/learned", headers=RUNNER_H, json={
        "project_id": 100, "sub_product": "", "runner": "mac-01",
        "run_id": run_id, "items": items,
    }).json()


LEARNED_CAND = {"by": "testid", "value": "chat-send", "src": "learned"}


def test_report_and_dedupe():
    # 错 token → 401
    r0 = client.post("/api/selectors/learned", headers={"Authorization": "Bearer bad"},
                     json={"project_id": 100, "items": [{"key": "sendBtn"}]})
    assert r0.json()["code"] == 401, r0.json()

    d = _report([{"key": "sendBtn", "candidates": [LEARNED_CAND, {"by": "bogus", "value": "x"}],
                  "evidence": {"matched": "testid~send", "text": "发送", "score": 100}}], run_id=77)
    assert d["code"] == 0 and d["data"]["accepted"] == 1 and d["data"]["appended"] == 1, d
    cands = _key_cands()
    assert cands[-1] == LEARNED_CAND, cands   # 追加在尾部,带试用标
    row = _s.query(SelectorLearned).filter_by(key="sendBtn").first()
    assert row.status == "pending" and row.hit_count == 1 and row.run_id == 77

    # 同候选重复上报 → 去重 bump,不重复追加
    d2 = _report([{"key": "sendBtn", "candidates": [LEARNED_CAND]}])
    assert d2["data"]["deduped"] == 1 and d2["data"]["appended"] == 0, d2
    _s.refresh(row)
    assert row.hit_count == 2
    assert len(_key_cands()) == 2   # 原1 + learned1
    print("OK report+dedupe")


def test_probation_cap():
    # 再学 2 个不同候选:第 2 个可挂(上限 2),第 3 个只记行不追加
    _report([{"key": "sendBtn", "candidates": [{"by": "css", "value": "#send2", "src": "learned"}]}])
    d = _report([{"key": "sendBtn", "candidates": [{"by": "css", "value": "#send3", "src": "learned"}]}])
    assert d["data"]["accepted"] == 1 and d["data"]["appended"] == 0, d
    assert len(_key_cands()) == 3   # 原1 + learned2(上限)
    print("OK probation cap")


def test_review():
    # approve 第一条 → 试用标去掉
    row = _s.query(SelectorLearned).filter_by(cand_value="chat-send").first()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    # 密码是假的登录不了 → 直接 override get_current_user 更省事
    from app.core.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: _s.get(User, 1)
    try:
        a = client.patch(f"/api/selectors/learned/{row.id}", json={"action": "approve"}).json()
        assert a["code"] == 0 and a["data"]["status"] == "approved", a
        cands = _key_cands()
        approved = [c for c in cands if c.get("value") == "chat-send"]
        assert approved and "src" not in approved[0], cands   # 转正:标已去
        _s.refresh(row)
        assert row.status == "approved"

        # reject 第二条 → 注册表移除
        row2 = _s.query(SelectorLearned).filter_by(cand_value="#send2").first()
        b = client.patch(f"/api/selectors/learned/{row2.id}", json={"action": "reject"}).json()
        assert b["code"] == 0 and b["data"]["status"] == "rejected", b
        cands2 = _key_cands()
        assert not any(c.get("value") == "#send2" for c in cands2), cands2
        # rejected 后同候选再上报:只 bump,不再追加注册表
        d = _report([{"key": "sendBtn", "candidates": [{"by": "css", "value": "#send2", "src": "learned"}]}])
        assert d["data"]["deduped"] == 1 and d["data"]["appended"] == 0, d
        assert not any(c.get("value") == "#send2" for c in _key_cands())

        # 评审列表(pending 还剩 #send3)
        lst = client.get("/api/selectors/learned", params={"project_id": 100}).json()
        assert lst["code"] == 0
        assert [x["candidate"]["value"] for x in lst["data"]] == ["#send3"], lst["data"]
        assert lst["data"][0]["desc"] == "「发送」按钮"   # 带 key 元信息
        # 非法 action
        c = client.patch(f"/api/selectors/learned/{row.id}", json={"action": "nope"}).json()
        assert c["code"] == 400
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    print("OK review approve/reject")


def test_unregistered_key():
    d = _report([{"key": "ghostKey", "candidates": [{"by": "css", "value": "#g", "src": "learned"}]}])
    assert d["data"]["accepted"] == 1 and d["data"]["appended"] == 0, d   # 只记行,无注册表可挂
    print("OK unregistered key")


def main():
    _seed()
    test_report_and_dedupe()
    test_probation_cap()
    test_review()
    test_unregistered_key()
    print("OK test_selector_learned")


if __name__ == "__main__":
    main()
