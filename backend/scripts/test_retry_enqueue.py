"""重试:对已执行的用例重新入队。运行: cd backend && python -m scripts.test_retry_enqueue (需 httpx)"""
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, ExecRun

eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(eng)
S = sessionmaker(bind=eng); s = S()
s.add(Project(id=2, name="P", code="test", status="active"))
s.add(AiTask(id=1, project_id=2, user_id=1, input_type="text", status="done"))  # test_case.ai_task_id NOT NULL
s.add(TestCase(id=5, ai_task_id=1, project_id=2, title="T", exec_kind="gui", review_status="adopted",
               script='[{"action":"connect","target":{},"args":{},"desc":"连"}]'))
s.add(ExecRun(id=100, test_case_id=5, project_id=2, runner="bendi-win", kind="gui", status="failed", payload="{}"))
s.commit()
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = lambda: (yield s)
client = TestClient(app)

def main():
    r = client.post("/api/exec-queue/100/retry")
    assert r.json()["code"] == 0, r.text
    # 应新增一条 pending 的 exec_run(同 test_case_id)
    n = s.query(ExecRun).filter_by(test_case_id=5).filter(ExecRun.status.in_(["pending","queued"])).count()
    assert n >= 1, f"重试应新增待执行 exec_run,实际 {n}"
    print("OK test_retry_enqueue")

if __name__ == "__main__":
    main()
