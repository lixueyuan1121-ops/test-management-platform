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
from app.models import AiJob, EvalQuery, EvalRun, ExecRun, Project, User
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


# ─── P2a: 判定 handler(eval_judge)经队列跑通 ────────────────────────────────────

class _FakeJudgeEngine:
    """判定引擎:stream_generate 返回一段三维 JSON,driver judge_run 落 verdict。"""
    def is_available(self): return True
    def stream_generate(self, *_a, **_kw):
        yield {"type": "result", "text": json.dumps({
            "thinking": {"pass": True, "note": "ok"},
            "answer": {"pass": True, "note": "ok"},
            "overall": {"pass": True, "note": "好"},
            "score": 4, "summary": "回答正确",
        }, ensure_ascii=False)}


def test_run_job_eval_judge():
    _clear()
    if not _s.get(Project, 100):
        _s.add(Project(id=100, name="P1", code="p1"))
    q = EvalQuery(project_id=100, title="t", prompt="p", expected="应正确", dimension="thinking")
    _s.add(q); _s.flush()
    run = EvalRun(project_id=100, eval_query_id=q.id, runner="m", status="done",
                  answer="这是回答", trace=None)
    _s.add(run); _s.commit()
    job = ai_jobs.enqueue(_s, "eval_judge", provider="claude", project_id=100, user_id=1,
                          input={"run_id": run.id, "votes": 1}, ref_kind="eval_run", ref_id=run.id)
    with patch("app.services.generators.get_provider", return_value=_FakeJudgeEngine()), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    got = _s.get(AiJob, job.id)
    assert got.status == "done", (got.status, got.error)
    assert json.loads(got.result)["verdict"] in ("pass", "fail", "error"), got.result
    assert _s.get(EvalRun, run.id).verdict is not None
    print("OK run_job eval_judge")


# ─── P2b: 脚本生成 handler(script_gen)经队列跑通 ──────────────────────────────────

class _FakeScriptEngine:
    def is_available(self): return True
    def generate_script(self, kind, title, steps, expected, project_id=None):
        return [{"action": "goto", "url": "/"}], None   # (script_list, err)


def test_run_job_script_gen():
    from app.models import AiTask, TestCase
    from app.core.enums import AiInputType
    _clear()
    if not _s.get(User, 1):
        _s.add(User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True))
    if not _s.get(Project, 100):
        _s.add(Project(id=100, name="P1", code="p1"))
    _s.flush()
    at = AiTask(project_id=100, user_id=1, kind="testcase_gen", input_type=AiInputType.text, status="done")
    _s.add(at); _s.flush()
    tc = TestCase(ai_task_id=at.id, project_id=100, title="登录用例", steps="1.打开", expected="成功",
                  exec_kind="gui")
    _s.add(tc); _s.commit()
    job = ai_jobs.enqueue(_s, "script_gen", provider="claude", project_id=100, user_id=1,
                          input={"cid": tc.id, "kind": "gui", "sel_fix": False, "project_id": 100,
                                 "title": "登录用例", "steps": "1.打开", "expected": "成功"},
                          ref_kind="test_case", ref_id=tc.id)
    with patch("app.services.generators.get_provider", return_value=_FakeScriptEngine()), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    got = _s.get(AiJob, job.id)
    assert got.status == "done", (got.status, got.error)
    tc2 = _s.get(TestCase, tc.id)
    assert tc2.script and "goto" in tc2.script, tc2.script
    print("OK run_job script_gen")


# ─── P3b: 测试点生成 handler(testcase_gen)经队列跑通 ────────────────────────────

class _FakeGenEngine:
    """测试点生成引擎:stream_generate 吐一段用例 JSON;parse_testcases 复用真实解析。"""
    _CASES = '[{"title":"登录成功","category":"功能","steps":"1.输入账号密码 2.提交","expected":"进入首页","priority":"P1","kind":"manual"}]'
    def is_available(self): return True
    def stream_generate(self, *_a, **_kw):
        yield {"type": "result", "text": self._CASES,
               "output_tokens": 10, "cost_usd": 0.01, "duration_ms": 123}
    def parse_testcases(self, raw, project_id=None):
        from app.services import claude_runner
        return claude_runner.parse_testcases(raw, project_id=project_id)


def test_run_job_testcase_gen():
    from app.models import AiTask, Task, TestCase
    from app.core.enums import AiInputType
    from datetime import date
    _clear()
    if not _s.get(User, 1):
        _s.add(User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True))
    if not _s.get(Project, 100):
        _s.add(Project(id=100, name="P1", code="p1"))
    _s.flush()
    if not _s.get(Task, 5):
        _s.add(Task(id=5, project_id=100, title="登录任务", assigned_by=1, assigned_to=1,
                    assigned_date=date(2026, 9, 1)))
    at = AiTask(project_id=100, task_id=5, user_id=1, kind="testcase_gen",
                input_type=AiInputType.text, status="running")
    _s.add(at); _s.commit()
    job = ai_jobs.enqueue(_s, "testcase_gen", provider="claude", project_id=100, user_id=1,
                          input={"ai_task_id": at.id, "project_id": 100, "task_id": 5,
                                 "requirement": "登录需求", "pages": None,
                                 "requirement_id": None, "provider": "claude"},
                          ref_kind="ai_task", ref_id=at.id)
    with patch("app.services.generators.get_provider", return_value=_FakeGenEngine()), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    got = _s.get(AiJob, job.id)
    assert got.status == "done", (got.status, got.error)
    assert json.loads(got.result)["case_count"] == 1, got.result
    assert _s.get(AiTask, at.id).status.value == "done"
    tcs = _s.query(TestCase).filter(TestCase.ai_task_id == at.id).all()
    assert len(tcs) == 1 and tcs[0].title == "登录成功", [t.title for t in tcs]
    print("OK run_job testcase_gen")


# ─── 分片并行:handler 走分片路径 + 部分分片失败仍落库 ──────────────────────────────

class _ShardAwareEngine:
    """按分片产不同用例的假引擎:会真的调 prompt_builder,故能验证 handler 是否走了分片路径。"""

    def __init__(self, fail_ids=()):
        self.fail_ids = set(fail_ids)
        self.seen = []

    def is_available(self): return True

    def build_testcase_prompt(self, requirement, project_id=None, pages=None, shard=None):
        return f"P::{shard['id'] if shard else 'full'}"

    def stream_generate(self, requirement, project_id=None, timeout=None, pages=None,
                        prompt_builder=None, system_prompt=None):
        sid = prompt_builder().split("::")[1] if prompt_builder else "full"
        self.seen.append(sid)
        if sid in self.fail_ids:
            yield {"type": "error", "msg": f"{sid} 片超时"}
            return
        yield {"type": "result",
               "text": json.dumps([{"title": f"{sid} 用例", "category": "功能", "steps": "1.做",
                                    "expected": "成", "priority": "P1", "kind": "manual"}], ensure_ascii=False),
               "output_tokens": 10, "cost_usd": 0.01, "duration_ms": 123}

    def parse_testcases(self, raw, project_id=None):
        from app.services import claude_runner
        return claude_runner.parse_testcases(raw, project_id=project_id)


def _run_gen_job(engine, project_id=100):
    """建 AiTask+job 并用给定引擎跑 testcase_gen handler。返回 (job, ai_task)。"""
    from app.models import AiTask, Task
    from app.core.enums import AiInputType
    from datetime import date
    if not _s.get(User, 1):
        _s.add(User(id=1, username="admin", name="管理员", password_hash="x", is_platform_admin=True))
    if not _s.get(Project, project_id):
        _s.add(Project(id=project_id, name="P1", code="p1"))
    _s.flush()
    if not _s.get(Task, 5):
        _s.add(Task(id=5, project_id=project_id, title="任务", assigned_by=1, assigned_to=1,
                    assigned_date=date(2026, 9, 1)))
    at = AiTask(project_id=project_id, task_id=5, user_id=1, kind="testcase_gen",
                input_type=AiInputType.text, status="running")
    _s.add(at); _s.commit()
    job = ai_jobs.enqueue(_s, "testcase_gen", provider="claude", project_id=project_id, user_id=1,
                          input={"ai_task_id": at.id, "project_id": project_id, "task_id": 5,
                                 "requirement": "需求", "pages": None,
                                 "requirement_id": None, "provider": "claude"},
                          ref_kind="ai_task", ref_id=at.id)
    with patch("app.services.generators.get_provider", return_value=engine), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    return _s.get(AiJob, job.id), _s.get(AiTask, at.id)


def test_gen_job_uses_shards():
    """handler 应把 K 个分片都跑到,每片各落一条(证明不是单次调用)。"""
    from app.models import TestCase
    from app.services.claude_runner import plan_shards
    _clear()
    eng = _ShardAwareEngine()
    job, at = _run_gen_job(eng)
    want = [s["id"] for s in plan_shards(100)]      # 项目无 api 契约 → 不含 api 片
    assert sorted(eng.seen) == sorted(want), f"应逐片调用:{eng.seen} vs {want}"
    assert job.status == "done", (job.status, job.error)
    assert json.loads(job.result)["case_count"] == len(want), job.result
    titles = sorted(t.title for t in _s.query(TestCase).filter(TestCase.ai_task_id == at.id))
    assert titles == sorted(f"{s} 用例" for s in want), titles
    assert at.error is None, f"全片成功不该留错误:{at.error}"
    print(f"OK 分片并行 handler({len(want)} 片)")


def test_gen_job_partial_shard_failure():
    """一片失败不整批失败:其余照常落库,失败片写进 AiTask.error 供前端提示。"""
    from app.models import TestCase
    from app.services.claude_runner import plan_shards
    _clear()
    want = [s["id"] for s in plan_shards(100)]
    eng = _ShardAwareEngine(fail_ids=[want[0]])
    job, at = _run_gen_job(eng)
    assert job.status == "done", (job.status, job.error)
    assert json.loads(job.result)["case_count"] == len(want) - 1
    assert len(_s.query(TestCase).filter(TestCase.ai_task_id == at.id).all()) == len(want) - 1
    assert at.error and "部分分片未产出" in at.error and want[0] in at.error, at.error
    assert json.loads(job.result)["partial_errors"], "partial_errors 应回传给前端"
    print("OK 分片部分失败仍落库")


def test_gen_job_single_shard_fallback():
    """AI_SHARD_CONCURRENCY<=1 → 回落单次调用(不传 prompt_builder),行为与分片改造前一致。"""
    from app.core.config import settings
    _clear()
    eng = _ShardAwareEngine()
    old = settings.AI_SHARD_CONCURRENCY
    settings.AI_SHARD_CONCURRENCY = 1
    try:
        job, at = _run_gen_job(eng)
    finally:
        settings.AI_SHARD_CONCURRENCY = old
    assert eng.seen == ["full"], f"单片回退不应带 shard:{eng.seen}"
    assert job.status == "done" and json.loads(job.result)["case_count"] == 1
    print("OK 单片回退路径")


def test_run_job_eval_summary():
    from app.models import EvalRun
    from app.models.ai_eval import EvalTask
    _clear()
    if not _s.get(Project, 100):
        _s.add(Project(id=100, name="P1", code="p1"))
    _s.flush()
    task = EvalTask(project_id=100, name="评价任务", last_batch_id="B1")
    _s.add(task); _s.flush()
    _s.add(EvalRun(project_id=100, eval_task_id=task.id, batch_id="B1", runner="m", status="done",
                   answer="回答内容", verdict="pass", score=4,
                   payload=json.dumps({"title": "题1", "prompt": "问1"})))
    _s.commit()

    class _FakeSummaryEngine:
        def is_available(self): return True
        def stream_generate(self, *_a, **_kw):
            yield {"type": "result", "text": "<h2>综合评价</h2><p>整体优秀</p>"}

    job = ai_jobs.enqueue(_s, "eval_summary", provider="claude", project_id=100, user_id=1,
                          input={"task_id": task.id, "batch_id": "B1", "provider": "claude"},
                          ref_kind="eval_task", ref_id=task.id)
    with patch("app.services.generators.get_provider", return_value=_FakeSummaryEngine()), \
         patch("app.services.generators.normalize_provider", return_value="claude"):
        ai_jobs.run_job(_Session, job.id)
    _s.expire_all()
    got = _s.get(AiJob, job.id)
    assert got.status == "done", (got.status, got.error)
    t2 = _s.get(EvalTask, task.id)
    assert t2.summary_status == "done" and t2.summary_html and "综合评价" in t2.summary_html, \
        (t2.summary_status, t2.summary_html)
    print("OK run_job eval_summary")


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
    test_run_job_eval_judge()
    test_run_job_script_gen()
    test_run_job_testcase_gen()
    test_gen_job_uses_shards()
    test_gen_job_partial_shard_failure()
    test_gen_job_single_shard_fallback()
    test_run_job_eval_summary()
    print("OK test_ai_jobs")


if __name__ == "__main__":
    main()
