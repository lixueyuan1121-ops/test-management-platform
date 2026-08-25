"""ai-funnel 聚合端点自测(TestClient + 依赖覆盖 + 内存库)。
运行: cd backend && python -m scripts.test_ai_funnel

覆盖:漏斗五级口径(生成/采纳/可自动化/已执行/通过)、时间窗过滤、
真bug计数、选择器待补存量卡点(不限窗)、采纳率/省时计算、非法 days 回落。
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.core.enums import ReviewStatus
from app.db.session import Base, get_db
from app.models import AiTask, ExecRun, Project, TestCase, User

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _seed():
    u = User(username="admin", name="管理员", password_hash="x", is_platform_admin=True)
    p = Project(name="P1", code="p1")
    _s.add_all([u, p])
    _s.flush()
    at = AiTask(project_id=p.id, user_id=u.id, kind="testcase_gen", input_ref="r")
    _s.add(at)
    _s.flush()

    def tc(review, exec_kind, kind_reason=None):
        c = TestCase(ai_task_id=at.id, project_id=p.id, title="t",
                     exec_kind=exec_kind, review_status=review, kind_reason=kind_reason)
        _s.add(c)
        return c

    # 窗口内 5 条生成：3 采纳(gui/api 可自动化 + manual)、1 否决、1 待定
    tc(ReviewStatus.adopted, "gui")
    tc(ReviewStatus.adopted, "api")
    tc(ReviewStatus.adopted, "manual")
    tc(ReviewStatus.rejected, "gui")
    tc(ReviewStatus.pending, "gui")
    # 选择器待补存量卡点：adopted + 标记，90 天前(窗外) → 不计 generated 但计 selector_pending
    old = tc(ReviewStatus.adopted, "gui",
             kind_reason="[选择器待补] 补齐选择器 key:xBtn 后即可执行 gui")
    _s.flush()
    old.created_at = datetime.now() - timedelta(days=90)

    def run(status, fail_kind=None, days_ago=0):
        r = ExecRun(project_id=p.id, runner="m", payload="{}", status=status, fail_kind=fail_kind)
        _s.add(r)
        if days_ago:
            _s.flush()
            r.created_at = datetime.now() - timedelta(days=days_ago)

    # 窗口内：2 passed + 1 failed(business) + 1 blocked(selector) + 1 running(不计 executed)
    run("passed")
    run("passed")
    run("failed", "business")
    run("blocked", "selector")
    run("running")
    # 窗口外(40 天前)：1 passed 不计
    run("passed", days_ago=40)
    _s.commit()


_seed()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    r = client.get("/api/stats/ai-funnel", params={"days": 30})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    stages = {s["stage"]: s["count"] for s in d["funnel"]}
    assert stages["generated"] == 5, stages     # 窗口内 5(90 天前那条不计)
    assert stages["adopted"] == 3, stages
    assert stages["automatable"] == 2, stages   # gui+api,manual 不算
    assert stages["executed"] == 4, stages      # passed2+failed1+blocked1(running/窗外不计)
    assert stages["passed"] == 2, stages
    assert d["bugs_found"] == 1, d
    assert d["selector_pending"] == 1, d        # 不限窗的存量卡点
    assert d["adopt_rate"] == 60.0, d           # 3/5
    assert d["saved_hours"] == round(4 * 5 / 60, 1), d
    # 漏斗 label 齐全(前端直接渲染)
    assert [s["label"] for s in d["funnel"]] == ["AI 生成", "已采纳", "可自动化", "已执行", "执行通过"]

    # 非法 days 回落默认 30
    r2 = client.get("/api/stats/ai-funnel", params={"days": -1})
    assert r2.json()["code"] == 0 and r2.json()["data"]["days"] == 30

    print("OK test_ai_funnel")


if __name__ == "__main__":
    main()
