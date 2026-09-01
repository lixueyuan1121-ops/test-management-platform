"""AI 任务队列(ai_job)+ worker 池 + 归因入队 自测。
运行: cd backend && python -m scripts.test_ai_jobs

覆盖(分任务递增):
- Task1 模型:AiJob 落库/查回,默认 status=pending
- Task2 队列核心:enqueue 建 pending(input 可 json 回)、claim_next 原子抢占(单条只一胜)、
  queue_position 排队位次
- Task3 handler:归因 handler 经 run_job 跑通(mock 引擎)→ 域表 triage 落库;坏输出→failed 不覆盖
- Task4 worker 池:reap 启动收口 running→failed;start_pool 起线程消费一条到 done
- Task5 端点:GET /api/ai-jobs/{id} 状态/位次/鉴权;cancel 仅 pending
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
from app.models import AiJob, ExecRun, Project, User
from app.services import ai_jobs

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


_current_uid = 1
app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[get_current_user] = lambda: _s.get(User, _current_uid)
client = TestClient(app)


# ─── Task1: 模型 ────────────────────────────────────────────────────────────────

def test_model_defaults():
    j = AiJob(kind="triage", input=json.dumps({"run_id": 501}))
    _s.add(j); _s.commit(); _s.refresh(j)
    assert j.id and j.status == "pending", (j.id, j.status)
    got = _s.get(AiJob, j.id)
    assert json.loads(got.input)["run_id"] == 501
    assert got.created_at is not None
    print("OK model defaults")
    _s.delete(got); _s.commit()


# ─── Task2: 队列核心 ──────────────────────────────────────────────────────────────

def _clear():
    _s.query(AiJob).delete(); _s.commit()


def test_enqueue():
    _clear()
    j = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1,
                        input={"run_id": 501}, ref_kind="exec_run", ref_id=501)
    assert j.id and j.status == "pending"
    got = _s.get(AiJob, j.id)
    assert json.loads(got.input)["run_id"] == 501 and got.ref_id == 501
    print("OK enqueue")


def test_claim_atomic():
    _clear()
    j = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1,
                        input={"run_id": 1})
    c1 = ai_jobs.claim_next(_s)
    c2 = ai_jobs.claim_next(_s)
    assert c1 is not None and c1.id == j.id and c1.status == "running", c1
    assert c2 is None, "同一 pending 不应被抢两次"
    assert c1.worker and c1.claimed_at
    print("OK claim atomic")


def test_queue_position():
    _clear()
    j1 = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 1})
    j2 = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 2})
    assert ai_jobs.queue_position(_s, j1) == 0, "队首位次 0"
    assert ai_jobs.queue_position(_s, j2) == 1, "第二条位次 1"
    ai_jobs.claim_next(_s)  # 消费 j1 → running
    _s.refresh(j2)
    assert ai_jobs.queue_position(_s, j2) == 0, "队首消费后 j2 升到 0"
    print("OK queue position")


# ─── Task3: handler + run_job(归因) ──────────────────────────────────────────────

class _FakeEngine:
    def __init__(self, out): self._out = out
    def is_available(self): return True
    def stream_generate(self, *_a, **_kw):
        yield {"type": "delta", "text": self._out}


def _seed_exec():
    if not _s.get(User, 1):
        _s.add(User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True))
    if not _s.get(Project, 100):
        _s.add(Project(id=100, name="P1", code="p1"))
    if not _s.get(ExecRun, 501):
        _s.add(ExecRun(id=501, project_id=100, runner="m", status=ExecStatus.failed,
                       fail_kind="business", reason="断言失败",
                       payload=json.dumps({"title": "登录", "steps": "s", "expected": "欢迎"})))
    _s.commit()


def test_run_job_triage_success():
    _clear(); _seed_exec()
    job = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1,
                          input={"run_id": 501}, ref_kind="exec_run", ref_id=501)
    fake = _FakeEngine('{"kind":"bug","confidence":0.85,"reason":"接口500","suggestion":"提缺陷"}')
    with patch("app.services.generators.get_provider", return_value=fake), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    got = _s.get(AiJob, job.id)
    assert got.status == "done", (got.status, got.error)
    assert json.loads(got.result)["kind"] == "bug"
    assert _s.get(ExecRun, 501).triage_kind == "bug"
    print("OK run_job triage success")


def test_run_job_triage_bad_output_failed_no_overwrite():
    _clear(); _seed_exec()
    # 先成功归因一次(triage_kind=bug),再用坏输出跑 → job failed 且不覆盖已有归因
    _s.get(ExecRun, 501).triage_kind = "bug"; _s.commit()
    job = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1,
                          input={"run_id": 501})
    bad = _FakeEngine("我觉得是环境问题")  # 无 JSON
    with patch("app.services.generators.get_provider", return_value=bad), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    got = _s.get(AiJob, job.id)
    assert got.status == "failed" and got.error, got.status
    assert _s.get(ExecRun, 501).triage_kind == "bug", "失败不应覆盖已有归因"
    print("OK run_job triage failed (no overwrite)")


# ─── Task4: worker 池 / 启动收口 ──────────────────────────────────────────────────

def test_reap_stale_on_startup():
    _clear()
    for i in range(2):
        _s.add(AiJob(kind="triage", status="running", input="{}"))
    _s.commit()
    n = ai_jobs.reap_stale_ai_jobs_on_startup(_s)
    assert n == 2, n
    _s.expire_all()
    assert _s.query(AiJob).filter(AiJob.status == "failed").count() == 2
    print("OK reap stale on startup")


def test_drain_once_consumes():
    _clear(); _seed_exec()
    job = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1,
                          input={"run_id": 501})
    fake = _FakeEngine('{"kind":"selector","confidence":0.7,"reason":"元素找不到","suggestion":"补选择器"}')
    with patch("app.services.generators.get_provider", return_value=fake), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        drained = ai_jobs._drain_once(_Session)
    assert drained is True
    _s.expire_all()
    assert _s.get(AiJob, job.id).status == "done"
    assert ai_jobs._drain_once(_Session) is False, "空队列应返回 False"
    print("OK drain once consumes")


def test_pool_start_stop_smoke():
    # 空队列起停:worker 起来等事件、stop 后退出,不抛、不挂
    ai_jobs.start_pool(2, factory=_Session)
    ai_jobs.stop_pool()
    print("OK pool start/stop smoke")


# ─── Task5: 轮询端点 + cancel ─────────────────────────────────────────────────────

def test_api_get_status_and_position():
    _clear(); _seed_exec()
    global _current_uid; _current_uid = 1
    j1 = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 1})
    j2 = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 2})
    d = client.get(f"/api/ai-jobs/{j2.id}").json()
    assert d["code"] == 0, d
    assert d["data"]["status"] == "pending" and d["data"]["queue_position"] == 1, d
    # done job 带 result
    j1.status = "done"; j1.result = json.dumps({"kind": "bug"}); _s.commit()
    d1 = client.get(f"/api/ai-jobs/{j1.id}").json()
    assert d1["data"]["status"] == "done" and d1["data"]["result"]["kind"] == "bug", d1
    print("OK api get status/position")


def test_api_auth_non_member_denied():
    _clear()
    # 建一个非平台管理员、非成员用户 2
    if not _s.get(User, 2):
        _s.add(User(id=2, username="u2", name="路人", password_hash="x", is_platform_admin=False))
        _s.commit()
    job = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 1})
    global _current_uid; _current_uid = 2
    r = client.get(f"/api/ai-jobs/{job.id}")
    assert r.json()["code"] != 0, "非 owner 非成员应被拒"
    _current_uid = 1
    print("OK api auth non-member denied")


def test_api_cancel_pending_only():
    _clear()
    global _current_uid; _current_uid = 1
    job = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 1})
    d = client.post(f"/api/ai-jobs/{job.id}/cancel").json()
    assert d["code"] == 0, d
    _s.expire_all()
    assert _s.get(AiJob, job.id).status == "cancelled"
    # running 不可取消
    job2 = ai_jobs.enqueue(_s, "triage", provider="claude", project_id=100, user_id=1, input={"n": 2})
    job2.status = "running"; _s.commit()
    r = client.post(f"/api/ai-jobs/{job2.id}/cancel")
    assert r.status_code == 409, r.text
    print("OK api cancel pending only")


def main():
    test_model_defaults()
    test_enqueue()
    test_claim_atomic()
    test_queue_position()
    test_run_job_triage_success()
    test_run_job_triage_bad_output_failed_no_overwrite()
    test_reap_stale_on_startup()
    test_drain_once_consumes()
    test_pool_start_stop_smoke()
    test_api_get_status_and_position()
    test_api_auth_non_member_denied()
    test_api_cancel_pending_only()
    print("OK test_ai_jobs")


if __name__ == "__main__":
    main()
