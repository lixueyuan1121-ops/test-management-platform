"""测评任务一条龙(auto pipeline)编排自测。
运行: cd backend && python -m scripts.test_eval_pipeline

覆盖:
- on_batch_maybe_done:门闩抢占只一次(并发/重复触发去重)、未完成不触发、开关关不触发、非任务批次不触发
- run_pipeline:四步顺序 + 四条通知 + 判定/评价被调 + 门闩落 done;综合评价失败不阻断收尾
- _result_summary:通过/失败/异常/均分 + A/B 胜率统计正确
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.enums import EvalRunStatus
from app.db.session import Base
from app.models import EvalQuery, EvalRun, Project, User
from app.models.ai_eval import EvalTask
from app.services import eval_pipeline
from app.api import eval_judge as judge_mod
from app.api import eval_task as task_mod
from app.services import notify as notify_mod

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()

# ── stub 外部依赖(判定引擎/综合评价引擎/飞书),只测编排逻辑 ──
_notifications = []          # 收集分步通知 (title, lines)
_judge_calls = []            # 记录批量判定调用
_summary_result = {"ok": True}   # 可切换成 error 测「不阻断」


def _fake_run_batch_judge(db, project_id, batch_id, provider=None):
    _judge_calls.append(batch_id)
    # 模拟判定:把该批 done → judged 并给 verdict/score
    rows = db.query(EvalRun).filter(EvalRun.batch_id == batch_id,
                                    EvalRun.status == EvalRunStatus.done).all()
    for i, r in enumerate(rows):
        r.status = EvalRunStatus.judged
        r.verdict = "pass" if i % 2 == 0 else "fail"
        r.score = 4 if i % 2 == 0 else 2
    db.commit()
    return len(rows)


def _fake_summary(db, task, batch_id, provider=None):
    return dict(_summary_result)


def _fake_notify(task_name, project_id, title, lines, color="blue"):
    _notifications.append((title, list(lines)))


judge_mod._run_batch_judge = _fake_run_batch_judge
task_mod.generate_task_summary_headless = _fake_summary
notify_mod.notify_eval_pipeline = _fake_notify

# on_batch_maybe_done 起线程 → 换成同步记录(不真起线程,便于断言门闩)
_spawned = []
eval_pipeline._spawn_pipeline = lambda *a: _spawned.append(a)


def _seed():
    _s.add_all([
        User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"),
        Project(id=1, name="P", code="P1", status="active"),
    ])
    _s.commit()


def _task(auto=True, batch="b1", pstatus=None):
    t = EvalTask(project_id=1, name="任务X", query_ids=json.dumps([1]),
                 last_batch_id=batch, auto_pipeline=auto, pipeline_status=pstatus)
    _s.add(t); _s.commit()
    return t


def _run(task_id, batch, status, verdict=None, score=None, abnormal=False, compare=None):
    payload = json.dumps({"compare_group": compare} if compare else {})
    r = EvalRun(eval_query_id=1, project_id=1, batch_id=batch, eval_task_id=task_id,
                runner="r1", status=status, payload=payload, verdict=verdict, score=score,
                is_abnormal=abnormal)
    _s.add(r); _s.commit()
    return r


def test_claim_once():
    _spawned.clear()
    t = _task(auto=True, batch="bc")
    _run(t.id, "bc", EvalRunStatus.done)
    _run(t.id, "bc", EvalRunStatus.judged)
    # 首次触发:抢占成功
    assert eval_pipeline.on_batch_maybe_done(_s, "bc") is True
    _s.refresh(t)
    assert t.pipeline_status == "running", t.pipeline_status
    assert len(_spawned) == 1, "应起一次编排"
    # 重复触发(模拟并发回写/reaper):门闩已 running,不再抢
    assert eval_pipeline.on_batch_maybe_done(_s, "bc") is False
    assert eval_pipeline.on_batch_maybe_done(_s, "bc") is False
    assert len(_spawned) == 1, "重复触发不应再起编排(门闩去重)"
    print("OK 门闩只抢占一次")


def test_not_settled():
    _spawned.clear()
    t = _task(auto=True, batch="bp")
    _run(t.id, "bp", EvalRunStatus.done)
    _run(t.id, "bp", EvalRunStatus.running)   # 还有一条在跑
    assert eval_pipeline.on_batch_maybe_done(_s, "bp") is False, "未全部终态不应触发"
    assert len(_spawned) == 0
    print("OK 未完成不触发")


def test_switch_off():
    _spawned.clear()
    t = _task(auto=False, batch="bo")
    _run(t.id, "bo", EvalRunStatus.done)
    assert eval_pipeline.on_batch_maybe_done(_s, "bo") is False, "开关关不应触发"
    assert len(_spawned) == 0
    print("OK 开关关不触发")


def test_non_task_batch():
    _spawned.clear()
    # 无对应 EvalTask.last_batch_id 的批次(普通题库下发)
    assert eval_pipeline.on_batch_maybe_done(_s, "ghost-batch") is False
    assert len(_spawned) == 0
    print("OK 非任务批次不触发")


def test_run_pipeline_four_steps():
    _notifications.clear(); _judge_calls.clear()
    global _summary_result
    _summary_result = {"ok": True}
    t = _task(auto=True, batch="br", pstatus="running")
    _run(t.id, "br", EvalRunStatus.done)
    _run(t.id, "br", EvalRunStatus.done)
    eval_pipeline.run_pipeline(_s, t.id, 1, "任务X", "br")
    # 四条通知,顺序含关键词
    titles = [n[0] for n in _notifications]
    assert len(titles) == 4, f"应发 4 条通知,得 {titles}"
    assert "对话" in titles[0] and "判定" in titles[1] and "综合评价" in titles[2] and "执行完毕" in titles[3], titles
    assert _judge_calls == ["br"], "应触发一次批量判定"
    _s.refresh(t)
    assert t.pipeline_status == "done", "编排完成门闩应落 done"
    assert t.pipeline_at is not None
    print("OK 四步编排 + 通知 + 门闩落 done")


def test_summary_failure_not_block():
    _notifications.clear()
    global _summary_result
    _summary_result = {"error": "引擎不可用"}
    t = _task(auto=True, batch="bf", pstatus="running")
    _run(t.id, "bf", EvalRunStatus.done)
    eval_pipeline.run_pipeline(_s, t.id, 1, "任务X", "bf")
    titles = [n[0] for n in _notifications]
    assert len(titles) == 4, "评价失败也应发满 4 步(含最终摘要)"
    assert "未生成" in titles[2] or "综合评价" in titles[2]
    _s.refresh(t)
    assert t.pipeline_status == "done", "评价失败不阻断,门闩仍落 done"
    print("OK 综合评价失败不阻断收尾")


def test_result_summary():
    t = _task(auto=True, batch="bs")
    # A 组:2 pass;B 组:1 pass 1 fail;另 1 条异常
    _run(t.id, "bs", EvalRunStatus.judged, verdict="pass", score=5, compare="A")
    _run(t.id, "bs", EvalRunStatus.judged, verdict="pass", score=4, compare="A")
    _run(t.id, "bs", EvalRunStatus.judged, verdict="pass", score=3, compare="B")
    _run(t.id, "bs", EvalRunStatus.judged, verdict="fail", score=1, compare="B", abnormal=True)
    _run(t.id, "bs", EvalRunStatus.judged, verdict="error")   # 判定失败/无法定论,须在摘要可见
    _run(t.id, "bs", EvalRunStatus.cancelled, verdict="pass", score=5)  # cancelled 不计
    s = eval_pipeline._result_summary(_s, t.id, "bs")
    assert s["total"] == 5, s          # 排除 cancelled(含 error 那条)
    assert s["passed"] == 3 and s["failed"] == 1, s
    assert s["abnormal"] == 1, s
    assert s["errored"] == 1, s        # error(判定失败)须单独统计,否则人不知有几条待重判
    assert s["avg_score"] == round((5 + 4 + 3 + 1) / 4, 2), s
    assert s["ab_line"] and "A 组" in s["ab_line"] and "B 组" in s["ab_line"], s["ab_line"]
    print("OK 结果摘要指标 + A/B 胜率")


def test_run_pipeline_creates_defect_draft():
    """一条龙收尾:判定出的 fail run 自动建缺陷草稿,最终摘要通知提及草稿数。"""
    from app.models import RemainingIssue
    _notifications.clear(); _judge_calls.clear()
    global _summary_result
    _summary_result = {"ok": True}
    _s.query(RemainingIssue).delete(); _s.commit()
    t = _task(auto=True, batch="bd", pstatus="running")
    _run(t.id, "bd", EvalRunStatus.done)   # 判定后 i=0 → pass(不建)
    _run(t.id, "bd", EvalRunStatus.done)   # 判定后 i=1 → fail(建草稿)
    eval_pipeline.run_pipeline(_s, t.id, 1, "任务X", "bd")
    drafts = _s.query(RemainingIssue).filter(RemainingIssue.eval_run_id.isnot(None)).all()
    assert len(drafts) == 1, f"fail 的 run 应建 1 条缺陷草稿,实际 {len(drafts)}"
    assert drafts[0].title.startswith("[自动] 测评失败"), drafts[0].title
    assert "缺陷草稿" in str(_notifications[-1]), _notifications[-1]
    print("OK pipeline 收尾建缺陷草稿 + 通知提及")


def test_pipeline_summary_reports_errored():
    """一条龙收尾:批次含 error(判定失败/无法定论)时,结果摘要通知须显式提示待重判条数。"""
    _notifications.clear()
    global _summary_result
    _summary_result = {"ok": True}
    t = _task(auto=True, batch="be", pstatus="running")
    # 造两条 done;fake judge 会把它们判成 pass/fail。再直接塞一条 error(judged)。
    _run(t.id, "be", EvalRunStatus.done)
    _run(t.id, "be", EvalRunStatus.done)
    _run(t.id, "be", EvalRunStatus.judged, verdict="error")
    eval_pipeline.run_pipeline(_s, t.id, 1, "任务X", "be")
    final = str(_notifications[-1])
    assert "1" in final and ("重判" in final or "判定失败" in final), final
    print("OK 摘要通知提示 error 待重判")


def main():
    _seed()
    test_claim_once()
    test_not_settled()
    test_switch_off()
    test_non_task_batch()
    test_run_pipeline_four_steps()
    test_summary_failure_not_block()
    test_result_summary()
    test_run_pipeline_creates_defect_draft()
    test_pipeline_summary_reports_errored()
    print("OK test_eval_pipeline")


if __name__ == "__main__":
    main()
