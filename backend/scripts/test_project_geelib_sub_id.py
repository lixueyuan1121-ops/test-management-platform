"""项目 geelib_sub_id 编辑端点自测（TDD）。

跑法（backend 目录下，venv 已装依赖）：
    python -m scripts.test_project_geelib_sub_id

覆盖：
- 平台管理员设置/清空 geelib_sub_id（走 DB，免重启）
- ProjectOut 回显 geelib_sub_id（列表/详情能看到当前映射）
- 非法值（<=0）被拒 400
- 非平台管理员被拒 403
- 不传 geelib_sub_id 时不动已有值（model_fields_set 语义，与 platform_type 一致）
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_project_geelib.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 每次跑干净库：删旧文件（幂等，避免上次结构/数据残留）
_DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_project_geelib.db")
if os.path.exists(_DB_FILE):
    os.remove(_DB_FILE)

from fastapi.testclient import TestClient

from app.main import app                       # 触发所有 model 定义注册到 Base.metadata
from app.db.session import Base, engine, SessionLocal
from app.core.security import create_access_token, hash_password

Base.metadata.create_all(engine)


def _auth(uid):
    return {"Authorization": f"Bearer {create_access_token(str(uid))}"}


def _seed():
    """建平台管理员 + 普通用户 + 一个项目，返回 (admin_id, member_id, project_id)。"""
    from app.models import User, Project
    db = SessionLocal()
    try:
        admin = User(username="geelib_admin", password_hash=hash_password("x"),
                     name="管理员", is_platform_admin=True)
        member = User(username="geelib_member", password_hash=hash_password("x"),
                      name="成员", is_platform_admin=False)
        db.add_all([admin, member])
        db.flush()
        proj = Project(name="纳米Work", code="NW-GEELIB-TEST")
        db.add(proj)
        db.commit()
        return admin.id, member.id, proj.id
    finally:
        db.close()


def main():
    admin_id, member_id, pid = _seed()
    client = TestClient(app)

    # 1) 管理员设置 geelib_sub_id → 200 且响应回显
    r = client.patch(f"/api/projects/{pid}", json={"geelib_sub_id": 419}, headers=_auth(admin_id))
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    assert r.json()["data"]["geelib_sub_id"] == 419, r.text

    # 2) list 回显 geelib_sub_id（ProjectOut 含该字段，UI 表格靠它展示映射）
    rl = client.get("/api/projects", params={"include_internal": True}, headers=_auth(admin_id))
    assert rl.json()["code"] == 0, rl.text
    row = next(p for p in rl.json()["data"] if p["id"] == pid)
    assert row["geelib_sub_id"] == 419, row

    # 3) 只改 name、不带 geelib_sub_id → 不动已有值（model_fields_set 语义）
    r3 = client.patch(f"/api/projects/{pid}", json={"name": "纳米Work改名"}, headers=_auth(admin_id))
    assert r3.json()["code"] == 0, r3.text
    assert r3.json()["data"]["geelib_sub_id"] == 419, r3.text

    # 4) 清空（显式传 null）→ None（解除映射，回退 GEELIB_SUB_MAP）
    r4 = client.patch(f"/api/projects/{pid}", json={"geelib_sub_id": None}, headers=_auth(admin_id))
    assert r4.json()["code"] == 0, r4.text
    assert r4.json()["data"]["geelib_sub_id"] is None, r4.text

    # 5) 非法值 <=0 → 拒绝（sub_id 是正整数）
    r5 = client.patch(f"/api/projects/{pid}", json={"geelib_sub_id": 0}, headers=_auth(admin_id))
    assert r5.json()["code"] != 0, "geelib_sub_id=0 应被拒"
    r5b = client.patch(f"/api/projects/{pid}", json={"geelib_sub_id": -3}, headers=_auth(admin_id))
    assert r5b.json()["code"] != 0, "geelib_sub_id<0 应被拒"

    # 6) 非平台管理员 → 403
    r6 = client.patch(f"/api/projects/{pid}", json={"geelib_sub_id": 419}, headers=_auth(member_id))
    assert r6.status_code == 403, r6.text

    print("OK test_project_geelib_sub_id")


if __name__ == "__main__":
    main()
