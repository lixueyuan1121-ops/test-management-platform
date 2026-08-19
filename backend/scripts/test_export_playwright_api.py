"""导出 Playwright 脚本 端到端自测(内存库 + TestClient)。
运行: cd backend && .venv/bin/python -m scripts.test_export_playwright_api

覆盖真实路由 + 鉴权 + 非信封响应:
  A. GET  /ai/testcases/{cid}/export-playwright:gui 用例 → 200 + .spec.mjs 附件,正文含
     connectOverCDP、注册表 key 翻成的 locator。
  B. 非 gui/e2e(api)→ 400。
  C. 无 script → 400。
  D. POST /ai/testcases/export-playwright(批量):混入不可导出的被跳过,zip 只含合格用例;
     X-Export-Skipped 头列出被跳过 id。
  E. 全不可导出 → 400。
"""
import io
import json
import zipfile
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.main import app
from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.models import Project, AiTask, TestCase, SelectorKey, SelectorScope

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(AiTask(id=1, project_id=1, user_id=1, input_type="text", status="done"))

# 注册表:项目1 两个 key(shell/vm)+ vmIframe
_s.add(SelectorScope(project_id=1, sub_product="", vm_iframe='iframe[src*=".work.n.cn"]'))
_s.add(SelectorKey(project_id=1, key="loginSubmit", frame="shell", desc="登录按钮",
                   candidates=json.dumps([{"by": "css", "value": "input[type=submit]"}])))
_s.add(SelectorKey(project_id=1, key="sendBtn", frame="vm", desc="发送",
                   candidates=json.dumps([{"by": "role", "value": "button", "name": "发送"}])))

# 用例:
# 301 gui 带 script(可导出)
_s.add(TestCase(id=301, ai_task_id=1, project_id=1, title="登录并发送", exec_kind="gui",
                review_status="pending", is_regression=True,
                script=json.dumps([
                    {"action": "click", "target": {"key": "loginSubmit"}, "desc": "点登录"},
                    {"action": "assert_visible", "target": {"key": "sendBtn"}, "desc": "断言发送可见"},
                ], ensure_ascii=False)))
# 302 gui 无 script(不可导出)
_s.add(TestCase(id=302, ai_task_id=1, project_id=1, title="无脚本gui", exec_kind="gui",
                review_status="pending", is_regression=True, script=None))
# 303 api 用例(不支持)
_s.add(TestCase(id=303, ai_task_id=1, project_id=1, title="接口用例", exec_kind="api",
                review_status="pending", is_regression=True,
                script=json.dumps([{"name": "x", "request": {"method": "GET", "path": "/a"},
                                    "asserts": [{"type": "status", "op": "eq", "value": 200}]}])))
# 304 e2e 带 script(可导出,验证 e2e 放行)
_s.add(TestCase(id=304, ai_task_id=1, project_id=1, title="端到端流程", exec_kind="e2e",
                review_status="pending", is_regression=True,
                script=json.dumps([{"action": "click", "target": {"key": "loginSubmit"}, "desc": "点"}], ensure_ascii=False)))
_s.commit()


def _override_db():
    yield _s


app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
app.dependency_overrides[get_db] = _override_db
client = TestClient(app)


def test_single_export():
    r = client.get("/api/ai/testcases/301/export-playwright")
    assert r.status_code == 200, r.text
    # 非信封:直接是 mjs 文本,不是 {code,msg,data}
    assert "application/json" not in r.headers.get("content-type", "")
    cd = r.headers.get("content-disposition", "")
    assert ".spec.mjs" in cd, cd
    body = r.text
    assert "connectOverCDP" in body
    assert "127.0.0.1:9222" in body
    # 注册表翻译:shell key → page.locator;vm key → vm.getByRole
    assert "page.locator('input[type=submit]').click()" in body, body
    assert "vm.getByRole('button', { name: '发送' })" in body, body
    assert "toBeVisible()" in body
    assert "登录并发送" in body


def test_non_gui_rejected():
    r = client.get("/api/ai/testcases/303/export-playwright")
    assert r.json()["code"] != 0, r.text
    assert "gui" in r.json()["msg"] or "e2e" in r.json()["msg"]


def test_no_script_rejected():
    r = client.get("/api/ai/testcases/302/export-playwright")
    assert r.json()["code"] != 0, r.text


def test_e2e_allowed():
    r = client.get("/api/ai/testcases/304/export-playwright")
    assert r.status_code == 200 and ".spec.mjs" in r.headers.get("content-disposition", ""), r.text


def test_bulk_zip_with_skips():
    # 301(ok) + 302(无script,跳) + 303(api,跳) + 304(ok) → zip 含 2 文件,跳过 2
    r = client.post("/api/ai/testcases/export-playwright", json={"ids": [301, 302, 303, 304]})
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type") == "application/zip"
    skipped = r.headers.get("x-export-skipped", "")
    assert set(skipped.split(",")) == {"302", "303"}, skipped
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert len(names) == 2, names
    assert any("301" in n for n in names) and any("304" in n for n in names), names
    # 内容确实是翻译产物
    assert "connectOverCDP" in zf.read(names[0]).decode("utf-8")


def test_bulk_all_skipped_400():
    r = client.post("/api/ai/testcases/export-playwright", json={"ids": [302, 303]})
    assert r.json()["code"] != 0, r.text


def main():
    test_single_export()
    test_non_gui_rejected()
    test_no_script_rejected()
    test_e2e_allowed()
    test_bulk_zip_with_skips()
    test_bulk_all_skipped_400()
    print("OK test_export_playwright_api")


if __name__ == "__main__":
    main()
