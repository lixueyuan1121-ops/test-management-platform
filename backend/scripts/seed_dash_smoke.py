"""数据看板冒烟种子：为漏斗/质量档案/测评雷达/防线日历一次性造数。幂等。
运行: cd backend && .venv/bin/python -m scripts.seed_dash_smoke
"""
from datetime import date, datetime, timedelta

from app.db.session import SessionLocal
from app.core.enums import IssueSeverity, IssueStatus, ReviewStatus
from app.models import (AiTask, ExecRun, Project, RemainingIssue, TestCase, User)
from app.models.release import ReleaseRecord
from app.models.ai_eval import EvalQuery, EvalRun
from app.models.feedback import FeedbackRun

s = SessionLocal()
now = datetime.now()
today = date.today()

admin = s.query(User).filter_by(is_platform_admin=True).first()
p = s.query(Project).filter_by(code="smoke-dash").first()
if not p:
    p = Project(name="冒烟-看板演示", code="smoke-dash")
    s.add(p); s.flush()

# 清理旧冒烟数据（按 project 归属）
for model in (TestCase, ExecRun, RemainingIssue, ReleaseRecord, EvalRun, EvalQuery, FeedbackRun):
    s.query(model).filter(model.project_id == p.id).delete(synchronize_session=False)
s.query(AiTask).filter(AiTask.project_id == p.id).delete(synchronize_session=False)
s.commit()

at = AiTask(project_id=p.id, user_id=admin.id, kind="testcase_gen", input_ref="冒烟需求")
s.add(at); s.flush()

# --- AI 漏斗：20 生成 → 14 采纳 → 11 可自动化 → 执行 ---
for i in range(20):
    review = ReviewStatus.adopted if i < 14 else (ReviewStatus.rejected if i < 17 else ReviewStatus.pending)
    kind = "manual" if (i in (11, 12, 13)) else ("gui" if i % 2 == 0 else "api")
    kr = "[选择器待补] 补齐选择器 key:demoBtn 后即可执行 gui" if i in (9, 10) else None
    s.add(TestCase(ai_task_id=at.id, project_id=p.id, title=f"冒烟用例{i}",
                   exec_kind=kind, review_status=review, kind_reason=kr))
s.flush()   # 让下方能查到刚 add 的用例(挂 test_case_id 用)

def run(status, fail_kind=None, days_ago=0, batch=None, dur=None, tc_id=None):
    r = ExecRun(project_id=p.id, runner="smoke", payload="{}", status=status,
                fail_kind=fail_kind, batch_id=batch, duration_ms=dur, test_case_id=tc_id)
    s.add(r); s.flush()
    if days_ago:
        r.created_at = now - timedelta(days=days_ago)
    return r

# 执行:近窗内 9 终态(7 pass 1 businessfail 1 selectorblock) + 1 running
# 挂 test_case_id = AI 链路执行(漏斗口径只统计这类)
first_tc = s.query(TestCase).filter_by(project_id=p.id).first()
for i in range(7):
    run("passed", days_ago=i % 5, dur=40000 + i * 1000, tc_id=first_tc.id)
run("failed", "business", days_ago=1, tc_id=first_tc.id)
run("blocked", "selector", days_ago=2, tc_id=first_tc.id)
run("running", tc_id=first_tc.id)

# --- 版本质量档案：3 个版本 ---
for ver, d_ago, reqs in (("v1.0.0", 25, 8), ("v1.1.0", 12, 12), ("v1.2.0", 3, 6)):
    s.add(ReleaseRecord(project_id=p.id, version=ver, release_date=today - timedelta(days=d_ago),
                        req_count=reqs, created_by=admin.id))
# 窗口内问题:v1.2.0 窗(12天前~3天前)一条 open major;v1.1.0 窗一条已解决
i1 = RemainingIssue(project_id=p.id, title="冒烟-高危遗留", severity=IssueSeverity.major,
                    status=IssueStatus.open)
i2 = RemainingIssue(project_id=p.id, title="冒烟-已解决", severity=IssueSeverity.minor,
                    status=IssueStatus.resolved)
s.add_all([i1, i2]); s.flush()
i1.created_at = now - timedelta(days=5)
i2.created_at = now - timedelta(days=15)

# --- 测评雷达：4 维度 ---
dims = [("准确性", ["pass", "pass", "fail"]), ("安全性", ["pass", "pass"]),
        ("流畅性", ["pass", "fail", "pass", "pass"]), ("指令遵循", ["pass"])]
for dim, verdicts in dims:
    q = EvalQuery(project_id=p.id, title=f"冒烟-{dim}", dimension=dim, prompt="q")
    s.add(q); s.flush()
    for v in verdicts:
        s.add(EvalRun(project_id=p.id, eval_query_id=q.id, runner="smoke",
                      status="judged", verdict=v))

# --- 防线日历：近 10 天中 7 天有跑批 ---
for d_ago in (1, 2, 3, 5, 6, 8, 9):
    b = f"smoke-fb-{d_ago}"
    fr = FeedbackRun(project_id=p.id, batch_id=b, trigger="auto", case_count=3)
    s.add(fr); s.flush()
    fr.created_at = now - timedelta(days=d_ago)
    for j in range(3):
        st = "failed" if (d_ago == 2 and j == 0) else "passed"
        rr = run(st, "business" if st == "failed" else None, batch=b)
        rr.created_at = now - timedelta(days=d_ago)

s.commit()
print(f"seeded dash smoke OK (project={p.id})")
