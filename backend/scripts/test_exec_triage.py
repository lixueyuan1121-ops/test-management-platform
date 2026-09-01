"""AI 失败归因(建议项⑨)自测。
运行: cd backend && python -m scripts.test_exec_triage

覆盖:
- parse_triage: 正常/围栏包裹/夹杂文字/坏 JSON/非法 kind/置信度越界钳制
- build_triage_prompt: 含用例快照/失败原因/逐步报告断言摘要
- 端点 POST /exec-queue/{id}/triage: mock 引擎 → 落库 triage_kind/triage;
  passed 的 run 400;引擎解析失败 502 且不覆盖已有归因
"""
import json
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.core.enums import ExecStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import ExecRun, Project, User
from app.services.exec_triage import build_triage_prompt, parse_triage

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[get_current_user] = lambda: _s.get(User, 1)
client = TestClient(app)


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="P1", code="p1"),
    ])
    _s.flush()
    _s.add(ExecRun(id=501, project_id=100, runner="m", status=ExecStatus.failed,
                   fail_kind="business", reason="断言失败:未见成功提示",
                   payload=json.dumps({"title": "登录成功", "steps": "1.输入 2.提交",
                                       "expected": "出现欢迎语"}),
                   report=json.dumps([{"no": 1, "action": "fill", "ok": True},
                                      {"no": 2, "action": "assertText", "ok": False,
                                       "check": {"expected": "欢迎", "actual": "错误码500"}}])))
    _s.add(ExecRun(id=502, project_id=100, runner="m", status=ExecStatus.passed,
                   payload="{}"))
    _s.commit()


def test_parse():
    ok1 = parse_triage('{"kind":"bug","confidence":0.9,"reason":"服务器500","suggestion":"提缺陷"}')
    assert ok1["kind"] == "bug" and ok1["confidence"] == 0.9, ok1
    ok2 = parse_triage('前置说明\n```json\n{"kind":"selector","confidence":2,"reason":"x"}\n```尾巴')
    assert ok2["kind"] == "selector" and ok2["confidence"] == 1.0, ok2   # 越界钳到 1
    ok3 = parse_triage('{"kind":"ENVIRONMENT","confidence":"abc"}')
    assert ok3["kind"] == "environment" and ok3["confidence"] == 0.5, ok3  # 大小写宽容+坏置信回默认
    assert parse_triage("") .get("error")
    assert parse_triage("没有json").get("error")
    assert parse_triage('{"kind":"other"}').get("error")
    assert parse_triage('{kind:bad}').get("error")
    print("OK parse")


def test_prompt():
    p = build_triage_prompt({"title": "登录", "steps": "s", "expected": "e"},
                            "断言失败", "business",
                            [{"no": 2, "action": "assertText", "ok": False,
                              "check": {"expected": "欢迎", "actual": "500"}}])
    assert "登录" in p and "断言失败" in p and "business" in p
    assert "期望" in p and "欢迎" in p and "500" in p
    assert "selector" in p and "bug" in p
    print("OK prompt")


class _FakeEngine:
    def __init__(self, out): self._out = out
    def is_available(self): return True
    def stream_generate(self, *_a, **_kw):
        yield {"type": "delta", "text": self._out}


def _triage_via_queue(run_id, engine):
    """归因端点改入队后:POST 拿 job_id → 同步 run_job 跑一次 → 返回 job_id。"""
    with patch("app.services.generators.get_provider", return_value=engine), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        r = client.post(f"/api/exec-queue/{run_id}/triage")
        if r.json().get("code") != 0:
            return r, None
        job_id = r.json()["data"]["job_id"]
        from app.services import ai_jobs
        ai_jobs.run_job(_Session, job_id)
    return r, job_id


def _job_status(job_id):
    return client.get(f"/api/ai-jobs/{job_id}").json()["data"]


def test_endpoint():
    fake = _FakeEngine('{"kind":"bug","confidence":0.85,"reason":"接口500导致断言失败","suggestion":"提缺陷"}')
    r, job_id = _triage_via_queue(501, fake)
    assert r.json()["code"] == 0 and job_id, r.text
    st = _job_status(job_id)
    assert st["status"] == "done" and st["result"]["kind"] == "bug", st
    _s.expire_all()
    row = _s.get(ExecRun, 501)
    assert row.triage_kind == "bug"
    saved = json.loads(row.triage)
    assert saved["suggestion"] == "提缺陷" and saved["at"]

    # passed 不可归因 → 入队前普通 400(不建 job)
    d2 = client.post("/api/exec-queue/502/triage").json()
    assert d2["code"] == 400, d2

    # 引擎输出坏 JSON → job failed,且不覆盖已有归因
    bad = _FakeEngine("我觉得是环境问题")
    r3, job_id3 = _triage_via_queue(501, bad)
    assert r3.json()["code"] == 0, r3.text
    st3 = _job_status(job_id3)
    assert st3["status"] == "failed" and st3["error"], st3
    _s.expire_all()
    assert _s.get(ExecRun, 501).triage_kind == "bug", "解析失败不应覆盖已有归因"

    # 404 run → 入队前普通 404
    assert client.post("/api/exec-queue/9999/triage").json()["code"] == 404
    print("OK endpoint (enqueue+poll)")


def main():
    _seed()
    test_parse()
    test_prompt()
    test_endpoint()
    print("OK test_exec_triage")


if __name__ == "__main__":
    main()
