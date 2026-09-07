"""eval-engines 端点冒烟(TestClient + 覆盖 get_current_user)。"""
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user

_FAKE_ADMIN = type("U", (), {"id": 1, "is_platform_admin": True})()
app.dependency_overrides[get_current_user] = lambda: _FAKE_ADMIN

client = TestClient(app)

r = client.get("/api/ai/eval-engines")
body = r.json()
assert body["code"] == 0, f"code 非 0: {body}"
engines = [e["engine"] for e in body["data"]]
assert "workbuddy" in engines and "namiwork" in engines, f"缺引擎: {engines}"
assert all("label" in e and "device_kind" in e for e in body["data"]), "字段不全"
print("PASS: eval-engines endpoint")
