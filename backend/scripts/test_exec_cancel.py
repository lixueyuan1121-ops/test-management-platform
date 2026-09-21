"""Cancel API integration with an isolated SQLite database (no live app/db)."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.session import Base, get_db
from app.models import Project, RunnerDevice, ExecRun
from app.api.exec_queue import router, _maybe_auto_retry
from app.core.deps import get_current_user, require_runner_ctx
from app.services.selector_device import reap_stale_exec_locks

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()
db.add(Project(id=1, name="cancel-test", code="cancel-test", status="active"))
db.add_all([RunnerDevice(id=i, owner_id=i, runner_id=f"device-{i}", name="test", token=f"cancel-test-token-{i}") for i in (1, 2)])
db.commit()
app = FastAPI()
app.include_router(router)
app.dependency_overrides[get_db] = lambda: db
app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
device_id = 1
app.dependency_overrides[require_runner_ctx] = lambda: SimpleNamespace(device=db.get(RunnerDevice, device_id))
client = TestClient(app)
def add(status="pending", **kw):
    row = ExecRun(project_id=1, runner="device-1", runner_device_id=1, kind="gui",
                  status=status, payload='{"title":"cancel-test"}', **kw)
    db.add(row); db.commit()
    return row.id
def post(rid, action):
    return client.post(f"/api/exec-queue/{rid}/{action}?runner=device-1")
def main():
    global device_id
    pending = add()
    assert post(pending, "cancel").json()["data"]["cancelled"]
    assert post(pending, "claim").status_code == 409
    assert post(pending, "cancel").json()["data"]["cancelled"]
    run, next_run = add(), add()
    assert post(run, "claim").status_code == 200
    assert post(run, "retry").status_code == 409
    assert client.patch(f"/api/exec-queue/{run}/verdict", json={"verdict":"pass"}).status_code == 409
    stopped = post(run, "cancel").json()["data"]
    assert stopped["status"] == "running" and stopped["cancel_requested"]
    assert post(next_run, "claim").status_code == 409, "keep device lock until worker stops"
    assert post(run, "heartbeat").json()["data"] == {"alive":True, "cancel_requested":True}
    device_id = 2
    assert post(run, "heartbeat").status_code == 403
    assert client.patch(f"/api/exec-queue/{run}?runner=device-1",json={"verdict":"pass"}).status_code == 403
    device_id = 1
    ack = client.patch(f"/api/exec-queue/{run}?runner=device-1",json={"verdict":"pass","report":[]})
    assert ack.json()["data"]["cancelled"], ack.text
    assert post(run, "heartbeat").json()["data"]["alive"] is False
    late = client.patch(f"/api/exec-queue/{run}?runner=device-1",json={"verdict":"pass"})
    assert late.json()["data"]["cancelled"], "late pass must not revive cancellation"
    assert not _maybe_auto_retry(db, db.get(ExecRun, run))
    assert post(next_run, "claim").status_code == 200, "ack releases device"
    assert post(next_run, "cancel").status_code == 200
    row = db.get(ExecRun, next_run); row.heartbeat_at = datetime.utcnow()-timedelta(minutes=6); db.commit()
    assert reap_stale_exec_locks(db,1)==1
    assert db.get(ExecRun,next_run).fail_kind == "cancelled"
    denied = add()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=2,is_platform_admin=False)
    assert post(denied, "cancel").status_code == 403
    assert db.get(ExecRun, denied).status == "pending"
    print("PASS cancel: queued/running/ack/late-report/retry/permission/stale-lock")
if __name__ == "__main__": main()
