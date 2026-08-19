"""执行批次汇总 + 报告 + 截图上传 自测(内存库 + TestClient)。
运行: cd backend && python -m scripts.test_exec_batch_report

覆盖:
  A. enqueue-cases 生成 batch_id,同批所有 run 共享同一 batch_id;返回带 batch_id。
  B. report PATCH 落 report JSON;_to_out / history 返回 batch_id + report(解析回对象)。
  C. 截图上传端点:PNG 校验、归属校验、存盘、返回 /uploads URL。
"""
import base64
import os
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user, require_runner_ctx
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, ExecRun

# 1x1 透明 PNG(带正确魔数),供截图上传测试。
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
_s.add(TestCase(id=101, ai_task_id=1, project_id=1, title="用例A", exec_kind="gui", review_status="pending"))
_s.add(TestCase(id=102, ai_task_id=1, project_id=1, title="用例B", exec_kind="e2e", review_status="pending"))
_s.commit()


def _override_db():
    yield _s


# runner ctx 桩:无 device(用 query 的 runner),ctx.device=None
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[require_runner_ctx] = lambda: SimpleNamespace(device=None)
client = TestClient(app)


def test_batch_and_report():
    # A. enqueue-cases → 同批 batch_id
    r = client.post("/api/exec-queue/enqueue-cases",
                    json={"project_id": 1, "runner": "mac-01", "test_case_ids": [101, 102]})
    assert r.json()["code"] == 0, r.text
    data = r.json()["data"]
    batch_id = data["batch_id"]
    run_ids = data["run_ids"]
    assert batch_id and len(run_ids) == 2, data
    _s.expire_all()
    assert all(_s.get(ExecRun, rid).batch_id == batch_id for rid in run_ids), "同批应共享 batch_id"

    # B. report 回写带 report JSON
    rep = [
        {"no": 1, "action": "connect", "desc": "连接", "ok": True},
        {"no": 2, "action": "assert_visible", "desc": "看导航", "ok": True, "shot": "/uploads/execs/1/2.png"},
    ]
    r2 = client.patch(f"/api/exec-queue/{run_ids[0]}?runner=mac-01",
                      json={"verdict": "pass", "reason": "全部通过", "duration_ms": 1200, "report": rep})
    assert r2.json()["code"] == 0, r2.text
    out = r2.json()["data"]
    assert out["batch_id"] == batch_id
    assert isinstance(out["report"], list) and out["report"][1]["shot"] == "/uploads/execs/1/2.png", out["report"]

    # history 也带 batch_id + report
    h = client.get("/api/exec-queue/history", params={"project_id": 1}).json()["data"]
    row = next(x for x in h if x["run_id"] == run_ids[0])
    assert row["batch_id"] == batch_id and isinstance(row["report"], list), row


def test_screenshot_upload():
    r = client.post("/api/exec-queue/enqueue-cases",
                    json={"project_id": 1, "runner": "mac-01", "test_case_ids": [101]})
    rid = r.json()["data"]["run_ids"][0]

    # 合法 PNG 上传 → 200,返回 /uploads URL
    resp = client.post(f"/api/exec-queue/{rid}/screenshot?idx=2&runner=mac-01",
                       files={"file": ("s.png", _PNG, "image/png")})
    assert resp.json()["code"] == 0, resp.text
    url = resp.json()["data"]["screenshot_url"]
    assert url == f"/uploads/execs/{rid}/2.png", url
    # 落盘核验
    uploads = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
    assert os.path.exists(os.path.join(uploads, f"execs/{rid}/2.png")), "截图应落盘"

    # 非 PNG → 400
    bad = client.post(f"/api/exec-queue/{rid}/screenshot?idx=0&runner=mac-01",
                      files={"file": ("x.txt", b"not png", "text/plain")})
    assert bad.json()["code"] != 0, "非 PNG 应拒绝"

    # 归属不符(别的 runner)→ 403
    wrong = client.post(f"/api/exec-queue/{rid}/screenshot?idx=0&runner=other-99",
                        files={"file": ("s.png", _PNG, "image/png")})
    assert wrong.json()["code"] != 0, "非归属执行机应拒绝"

    # 清理落盘测试文件
    try:
        os.remove(os.path.join(uploads, f"execs/{rid}/2.png"))
        os.rmdir(os.path.join(uploads, f"execs/{rid}"))
    except OSError:
        pass


def main():
    test_batch_and_report()
    test_screenshot_upload()
    print("OK test_exec_batch_report")


if __name__ == "__main__":
    main()
