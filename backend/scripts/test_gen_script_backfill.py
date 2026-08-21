"""gen-script 确定性回填快路径 端点自测(TestClient + 内存库 + 会爆炸的桩引擎)。
运行: cd backend && python -m scripts.test_gen_script_backfill

验证改动2b:「选择器待补」用例若库里已存原始 script、且其引用的 key 现已全部注册,
点重生应走**确定性回填**(不调 AI)→ 恢复 exec_kind、清待补标识。桩引擎故意抛异常:
一旦被调用即测试失败,以此证明"回填路径确实没碰 AI"。
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import AiTask, TestCase, Project, SelectorKey
from app.services import generators

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
from app.api import ai as _ai_mod
_ai_mod.SessionLocal = _Session

_OLD_SCRIPT = (
    '[{"action":"connect","target":{},"args":{},"desc":"连"},'
    '{"action":"click","target":{"key":"submitOrderBtn"},"args":{},"desc":"点下单"},'
    '{"action":"assert_visible","target":{"key":"submitOrderBtn"},"args":{},"desc":"看"}]'
)

_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))
# 待补用例:已保留原始 script(引用 submitOrderBtn),仍带待补标识/manual。
_s.add(TestCase(id=1, ai_task_id=1, project_id=1, title="下单按钮", steps="进入→点下单",
                exec_kind="manual", review_status="pending", script=_OLD_SCRIPT,
                kind_reason="[选择器待补] 补齐选择器 key:submitOrderBtn 后即可执行 gui"))
# 关键:submitOrderBtn 现已注册到项目级共享('') → 回填校验应通过。
# candidates 列是 Text(JSON 字符串,镜像生产 MySQL 5.6 无 JSON 类型),故存字符串而非 list。
_s.add(SelectorKey(project_id=1, sub_product="", key="submitOrderBtn", frame="auto",
                   candidates='[{"by": "text", "value": "下单"}]'))
_s.commit()


def _override_db():
    yield _s


def _boom(*a, **k):
    raise AssertionError("确定性回填路径不应调用 AI 引擎")


# 桩引擎:可用,但 generate_script 一被调用就炸(证明回填没走 AI)。
generators.get_provider = lambda name=None: SimpleNamespace(is_available=lambda: True, generate_script=_boom)

app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def main():
    r = client.post("/api/ai/testcases/1/gen-script")
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    d = r.json()["data"]
    assert d["exec_kind"] == "gui", f"回填应恢复 gui,实际 {d['exec_kind']}"
    assert d["selector_fix"] is False, "回填成功应清除待补标识"
    assert not d["kind_reason"], f"kind_reason 应清空,实际 {d['kind_reason']}"
    # 回填用的是旧 script,应仍引用 submitOrderBtn
    assert "submitOrderBtn" in (d["script"] or ""), "回填应沿用旧 script(引用 submitOrderBtn)"
    _s.expire_all()
    row = _s.get(TestCase, 1)
    assert row.exec_kind == "gui" and row.kind_reason is None
    print("OK test_gen_script_backfill")


if __name__ == "__main__":
    main()
