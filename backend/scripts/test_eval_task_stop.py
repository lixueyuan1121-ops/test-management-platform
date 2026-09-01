"""测评任务停止(建议项:一键停止已下发的测评任务)自测。
运行: cd backend && python -m scripts.test_eval_task_stop

覆盖:
- POST /eval-tasks/{id}/stop: 当前批次 pending+running → cancelled;任务 → stopped;
  开了定时则一并关(schedule_enabled=False + sync_eval_task_job(...,False) + next_run_at=None)
- 已终态(done)的 run 不被 stop 波及;停止幂等(再停一次 cancelled_count=0)
- report 回写保护:对已 cancelled 的 run PATCH 回写 → 409,status 仍 cancelled(防跑完的回写冲回 done)
- summarize 排除 cancelled:全 cancelled 批次无可评素材 → 400(取消的用例不进 AI 综合评价)
"""
import json
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import RunnerCtx, get_current_user, require_runner_ctx
from app.core.enums import EvalRunStatus
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, EvalRun, Project, User
from app.models.ai_eval import EvalTask

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[get_current_user] = lambda: _s.get(User, 1)
app.dependency_overrides[require_runner_ctx] = lambda: RunnerCtx(device=None)
client = TestClient(app)


def _st(r: EvalRun) -> str:
    return getattr(r.status, "value", r.status)


def _seed():
    _s.add_all([
        User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True),
        Project(id=100, name="P1", code="p1"),
    ])
    _s.flush()
    for qid in (1, 2, 3):
        _s.add(EvalQuery(id=qid, project_id=100, title=f"问题{qid}",
                         prompt=f"问题{qid}正文", expected="预期"))
    _s.add(EvalTask(id=10, project_id=100, name="基线集",
                    query_ids=json.dumps([1, 2, 3])))
    _s.commit()


def test_stop_task():
    # 下发 3 条 → 全 pending
    d = client.post("/api/eval-tasks/10/run", json={
        "runner": "win-01", "target_engine": "namiwork"}).json()
    assert d["code"] == 0 and len(d["data"]["run_ids"]) == 3, d
    r1, r2, r3 = d["data"]["run_ids"]
    # 模拟:r1 执行机已认领(running)、r3 已跑完(done)、r2 仍 pending
    _s.get(EvalRun, r1).status = EvalRunStatus.running
    done = _s.get(EvalRun, r3)
    done.status = EvalRunStatus.done
    done.answer = "已完成的回答"
    # 模拟该任务开了定时基线跑
    t = _s.get(EvalTask, 10)
    t.schedule_enabled = True
    t.schedule_cron = "0 3 * * *"
    _s.commit()

    with patch("app.services.scheduler.sync_eval_task_job") as m:
        m.return_value = None
        d = client.post("/api/eval-tasks/10/stop").json()
    assert d["code"] == 0, d
    # 只收口 pending+running(r1,r2),不动已完成的 r3
    assert d["data"]["cancelled_count"] == 2, d
    _s.expire_all()
    assert _st(_s.get(EvalRun, r1)) == "cancelled", "running 应被停止"
    assert _st(_s.get(EvalRun, r2)) == "cancelled", "pending 应被停止"
    assert _st(_s.get(EvalRun, r3)) == "done", "已完成的不应被波及"
    # 任务状态 → stopped
    t = _s.get(EvalTask, 10)
    assert getattr(t.status, "value", t.status) == "stopped", "任务应标记 stopped"
    # 定时一并关:enabled 关、sync 以 enabled=False 调用(移除 job)
    # 注:线上 EvalTask 无 next_run_at 列(定时执行为远程实现),故不校验该字段
    assert t.schedule_enabled is False, "定时应被关闭"
    m.assert_called_once_with(10, "0 3 * * *", False)

    # 幂等:再停一次已无 pending/running → cancelled_count=0
    d2 = client.post("/api/eval-tasks/10/stop").json()
    assert d2["code"] == 0 and d2["data"]["cancelled_count"] == 0, d2
    print("OK stop task")


def test_report_rejected_after_cancel():
    # 取一条已 cancelled 的 run(r2),模拟执行机跑完回写 → 应被 409 拒,不覆盖 cancelled
    rid = _s.query(EvalRun).filter(EvalRun.status == EvalRunStatus.cancelled).first().id
    d = client.patch(f"/api/eval-queue/{rid}", params={"runner": "win-01"},
                     json={"status": "done", "answer": "迟到的回写"}).json()
    assert d["code"] == 409, d
    _s.expire_all()
    assert _st(_s.get(EvalRun, rid)) == "cancelled", "回写不得覆盖已停止状态"
    print("OK report rejected after cancel")


def test_batch_judge_skips_cancelled():
    # 手工/漂移把 cancelled 的 run 显式送批量判定 → 应进 skipped(不建 job、不判定),结果作废
    rid = _s.query(EvalRun).filter(EvalRun.status == EvalRunStatus.cancelled).first().id
    d = client.post("/api/eval-judge/batch",
                    json={"project_id": 100, "run_ids": [rid]}).json()
    assert d["code"] == 0, d
    data = d["data"]
    # 方案2:批量判定改入队,cancelled 不可判 → 不产 job、进 skipped
    assert data["count"] == 0 and not data["job_ids"], data
    assert len(data["skipped"]) == 1 and "cancelled" in data["skipped"][0]["reason"], data
    _s.expire_all()
    assert _s.get(EvalRun, rid).verdict is None, "取消的 run 不应被判出 verdict"
    print("OK batch judge skips cancelled")


def test_summarize_excludes_cancelled():
    class _FakeEngine:
        def is_available(self):
            return True

    # 新批次 B2:两条都 cancelled → 排除后无可评素材
    for _ in range(2):
        _s.add(EvalRun(eval_query_id=1, project_id=100, batch_id="B2", eval_task_id=10,
                       runner="win-01", status=EvalRunStatus.cancelled,
                       payload=json.dumps({"title": "t", "prompt": "p"})))
    t = _s.get(EvalTask, 10)
    t.last_batch_id = "B2"
    _s.commit()

    with patch("app.api.eval_task.generators.get_provider", return_value=_FakeEngine()):
        d = client.post("/api/eval-tasks/10/summarize", json={}).json()
    assert d["code"] == 400, f"全 cancelled 批次应无可评素材: {d}"
    print("OK summarize excludes cancelled")


def main():
    _seed()
    test_stop_task()
    test_report_rejected_after_cancel()
    test_batch_judge_skips_cancelled()
    test_summarize_excludes_cancelled()
    print("OK test_eval_task_stop")


if __name__ == "__main__":
    main()
