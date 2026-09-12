"""录制会话端点 + 保存为用例(含选择器回填)自测(TestClient + 内存库)。
运行: cd backend && python -m scripts.test_record_flow
"""
import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.models import Project, SelectorKey, TestCase, RecordSession, AiTask, Task, ChecklistItem, RunnerDevice

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
# 已注册一个 key(navAgents),录制命中它则复用;另一个元素(office-online-preview)未注册→新建回填
_s.add(SelectorKey(id=1, project_id=1, sub_product="", key="navAgents",
                   candidates='[{"by":"testid","value":"nav-agents"}]'))
from datetime import date
_s.add(Task(id=1, project_id=1, assigned_by=1, assigned_to=1, title="录制目标任务", assigned_date=date(2026, 9, 10), status="pending"))
_s.add(RunnerDevice(id=1, owner_id=1, runner_id="win-01", name="Test", token="test-device-token"))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[require_runner_ctx] = lambda: SimpleNamespace(device=_s.get(RunnerDevice, 1))
client = TestClient(app)


def main():
    # ① 发起录制
    r = client.post("/api/record", json={"project_id": 1, "sub_product": "", "runner": "win-01"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    sid = r.json()["data"]["id"]

    # ② runner 拉取 → pending 认领为 recording
    r = client.get("/api/record/pending?runner=win-01&consumer_id=test-process")
    assert any(x["id"] == sid for x in r.json()["data"]), r.text
    _s.expire_all()
    assert _s.get(RecordSession, sid).status == "recording"

    # ③ runner 增量上报事件(命中 navAgents + 未注册 office-online-preview + 断言)
    evs = [
        {"action": "click", "tag": "li", "text": "专家", "candidates": [{"by": "testid", "value": "nav-agents"}]},
        {"action": "click", "tag": "button", "text": "在线预览",
         "candidates": [{"by": "testid", "value": "office-online-preview"}, {"by": "css", "value": ".prev-btn"}]},
        {"action": "assert", "tag": "div", "text": "预览区", "candidates": [{"by": "testid", "value": "preview-panel"}],
         "assert": {"kind": "visible"}},
    ]
    evs = [{**e, "event_id": str(i), "ts": i} for i, e in enumerate(evs)]
    r = client.post(f"/api/record/{sid}/events?runner=win-01", json={"events": evs, "consumer_id": "test-process"})
    assert r.json()["data"]["total"] == 3, r.text

    # ④ 用户轮询看实时步骤
    r = client.get(f"/api/record/{sid}")
    assert len(r.json()["data"]["events"]) == 3

    # ⑤ 停止
    r = client.post(f"/api/record/{sid}/stop")
    assert r.json()["data"]["status"] == "stopping"
    ack = client.post(f"/api/record/{sid}/events", json={"events": [], "consumer_id": "test-process", "final": True})
    assert ack.json()["data"]["status"] == "stopped"

    # ⑥ 保存为用例 → 组装 e2e script + 回填 office-online-preview（task_id 必填）
    r = client.post(f"/api/record/{sid}/save-as-case", json={"title": "录制:专家页在线预览", "task_id": 1, "precondition": "进入首页"})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["exec_kind"] == "e2e", d
    assert d["precondition"] == "进入首页", d
    assert d["task_id"] == 1, f"应关联任务 1,实际 {d.get('task_id')}"
    # 预期结果由断言步生成(修"录制 e2e 没有预期结果")
    tc_row = _s.get(TestCase, d["id"])
    assert tc_row.expected and tc_row.expected.strip(), f"expected 不应为空,实际 {tc_row.expected!r}"
    # 关联任务 → 生成一条验收清单项
    assert _s.query(ChecklistItem).filter_by(task_id=1, test_case_id=d["id"]).first() is not None, "应补一条清单项"
    steps = json.loads(tc_row.script)
    keys = [ (s.get("target") or {}).get("key") for s in steps if (s.get("target") or {}).get("key") ]
    assert "navAgents" in keys, f"应复用已注册 navAgents,实际 {keys}"
    # 未注册元素新建 key 并回填到库
    _s.expire_all()
    newk = _s.query(SelectorKey).filter_by(project_id=1, key="officeOnlinePreview").first()
    assert newk is not None, "office-online-preview 应新建 key officeOnlinePreview 并回填"
    cands = json.loads(newk.candidates)
    assert cands[0]["by"] == "testid", cands
    assert d["id"] and steps[0]["action"] == "connect", "script 首步应为 connect"
    assert _s.get(RecordSession, sid).status == "done"

    # ⑦ 删除会话
    r2 = client.post("/api/record", json={"project_id": 1, "runner": "win-01"})
    sid2 = r2.json()["data"]["id"]
    # save-as-case 缺 task_id → 422(必填)
    client.post(f"/api/record/{sid2}/events?runner=win-01", json={"events": evs})
    assert client.post(f"/api/record/{sid2}/save-as-case", json={"title": "x"}).status_code == 422, "task_id 必填"
    assert client.delete(f"/api/record/{sid2}").json()["code"] == 0
    print("ALL OK test_record_flow")


if __name__ == "__main__":
    main()
