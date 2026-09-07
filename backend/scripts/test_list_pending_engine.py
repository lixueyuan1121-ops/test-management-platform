"""list_pending 带 engine 参数时把设备 eval_engine 落库(TestClient + 覆盖 require_runner_ctx + 内存库)。"""
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db.session import Base, get_db
from app.models import RunnerDevice, User
from app.main import app
from app.core.deps import require_runner_ctx

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

# 单一共享会话:真实请求里 get_db 与 require_runner_ctx 共用同一请求作用域会话,
# ctx.device 从该会话查出,db.commit() 才能把 device 的修改落库。测试须复现这点(共用会话)。
_shared = _Session()
_shared.add(User(id=1, username="a", name="A", password_hash="x", is_platform_admin=True, status="active"))
_shared.add(RunnerDevice(id=1, owner_id=1, runner_id="wb-mac", name="wb机", token="tk-wb", eval_engine=None))
_shared.commit()

def _override_db():
    yield _shared

def _override_ctx():
    return SimpleNamespace(device=_shared.get(RunnerDevice, 1))

app.dependency_overrides[get_db] = _override_db
app.dependency_overrides[require_runner_ctx] = _override_ctx
client = TestClient(app)

# 带 engine=workbuddy 调用 → 设备 eval_engine 应落 workbuddy
r = client.get("/api/eval-queue", params={"engine": "workbuddy"})
assert r.json()["code"] == 0, r.json()
assert _shared.get(RunnerDevice, 1).eval_engine == "workbuddy", f"eval_engine 未落库: {_shared.get(RunnerDevice,1).eval_engine}"

# 非法 engine 不落(仍是 workbuddy)
client.get("/api/eval-queue", params={"engine": "gpt"})
assert _shared.get(RunnerDevice, 1).eval_engine == "workbuddy", "非法 engine 不应覆盖"
print("PASS: list_pending 带 engine 落库 + 非法拒绝")
