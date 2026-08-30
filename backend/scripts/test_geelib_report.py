"""极库云(geelib)缺陷上报自测。
运行: cd backend && python -m scripts.test_geelib_report

覆盖:
- get_app_token: 解析 qihoo-sso-cli JSON / 缓存命中 / errcode!=0 抛错 / CLI 缺失抛错
- resolve_sub_id: Project.geelib_sub_id 优先 > GEELIB_SUB_MAP > None
- report_defect: 未启用 → ok=False;启用 → POST /openapi/Matter/add,errno=2000 取 id,errno!=2000 抛错
- build_defect_body: 严重度中文标签 + 描述 + 来源链接
- 端点 report_issue_to_geelib: 成功回填 external_ref;已上报幂等;通道关 409;缺 sub_id 409;失败 502
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.enums import IssueSeverity, IssueStatus
from app.db.session import Base
from app.models import Project, RemainingIssue
from app.services import geelib

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _reset_token_cache():
    geelib._token_cache["token"] = None
    geelib._token_cache["exp"] = 0.0


class _FakeProc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b"x"

    def json(self):
        return self._payload


def test_get_app_token():
    _reset_token_cache()
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _FakeProc(json.dumps({"errcode": 0, "app_token": "TОKEN123"}))

    orig_run, orig_which = geelib.subprocess.run, geelib.shutil.which
    geelib.subprocess.run = fake_run
    geelib.shutil.which = lambda _n: "/usr/bin/qihoo-sso-cli"
    try:
        tok = geelib.get_app_token()
        assert tok == "TОKEN123", tok
        # 缓存命中：第二次不再调 CLI
        tok2 = geelib.get_app_token()
        assert tok2 == "TОKEN123" and len(calls) == 1, f"缓存未命中 calls={len(calls)}"
        # 命令构造正确
        assert calls[0][0] == "/usr/bin/qihoo-sso-cli"
        assert "-app" in calls[0] and "geelib" in calls[0]
    finally:
        geelib.subprocess.run, geelib.shutil.which = orig_run, orig_which
    print("OK get_app_token + cache")


def test_get_app_token_errors():
    _reset_token_cache()
    orig_run, orig_which = geelib.subprocess.run, geelib.shutil.which
    # CLI 缺失
    geelib.shutil.which = lambda _n: None
    try:
        try:
            geelib.get_app_token()
            assert False, "CLI 缺失应抛错"
        except geelib.GeelibError as e:
            assert "qihoo-sso-cli" in str(e)
    finally:
        geelib.shutil.which = orig_which
    # errcode != 0
    _reset_token_cache()
    geelib.shutil.which = lambda _n: "/x/qihoo-sso-cli"
    geelib.subprocess.run = lambda a, **k: _FakeProc(json.dumps({"errcode": 401, "errmsg": "未授权"}))
    try:
        try:
            geelib.get_app_token()
            assert False, "errcode!=0 应抛错"
        except geelib.GeelibError as e:
            assert "授权失败" in str(e)
    finally:
        geelib.subprocess.run, geelib.shutil.which = orig_run, orig_which
    print("OK get_app_token errors")


def test_resolve_sub_id():
    orig = settings.GEELIB_SUB_MAP
    settings.GEELIB_SUB_MAP = "nw:419, other:512"
    try:
        assert geelib.resolve_sub_id("nw") == 419
        assert geelib.resolve_sub_id("other") == 512
        assert geelib.resolve_sub_id("nope") is None
        # Project.geelib_sub_id 优先于映射
        assert geelib.resolve_sub_id("nw", 999) == 999
        assert geelib.resolve_sub_id(None) is None
    finally:
        settings.GEELIB_SUB_MAP = orig
    print("OK resolve_sub_id")


def test_build_defect_body():
    body = geelib.build_defect_body("断言失败：弹窗未出现", "blocker",
                                    platform_url="http://p/issues", extra=["平台遗留问题 #7"])
    assert "阻断" in body and "断言失败" in body
    assert "平台遗留问题 #7" in body and "http://p/issues" in body
    print("OK build_defect_body")


def test_report_defect_disabled():
    orig = settings.GEELIB_ENABLED
    settings.GEELIB_ENABLED = False
    try:
        res = geelib.report_defect(419, "标题")
        assert res["ok"] is False and "未启用" in res["reason"]
    finally:
        settings.GEELIB_ENABLED = orig
    print("OK report_defect disabled")


def test_report_defect_success_and_fail():
    _reset_token_cache()
    orig_enabled = settings.GEELIB_ENABLED
    orig_post = geelib.requests.post
    orig_token = geelib.get_app_token
    settings.GEELIB_ENABLED = True
    geelib.get_app_token = lambda force=False: "TOK"
    posted = {}

    def fake_post_ok(url, json=None, headers=None, timeout=None):
        posted["url"], posted["body"], posted["headers"] = url, json, headers
        return _FakeResp({"errno": 2000, "data": {"id": 88231}})

    geelib.requests.post = fake_post_ok
    try:
        res = geelib.report_defect(419, "登录页崩溃", description="堆栈…", severity="major")
        assert res["ok"] is True and res["matter_id"] == 88231
        assert res["ref"] == "geelib#88231", res["ref"]
        assert posted["url"].endswith("/openapi/Matter/add")
        assert posted["body"]["sub_id"] == 419 and posted["body"]["type_id"] == settings.GEELIB_DEFECT_TYPE
        assert posted["headers"]["X-Agent-Auth"] == "Bearer TOK"
        assert "主要" in posted["body"]["mkd_content"]  # severity 落进正文

        # errno != 2000 → 抛 GeelibError
        geelib.requests.post = lambda *a, **k: _FakeResp({"errno": 4001, "errmsg": "无权限"})
        try:
            geelib.report_defect(419, "x")
            assert False, "errno!=2000 应抛错"
        except geelib.GeelibError as e:
            assert "无权限" in str(e)
    finally:
        settings.GEELIB_ENABLED = orig_enabled
        geelib.requests.post = orig_post
        geelib.get_app_token = orig_token
    print("OK report_defect success + fail")


def _seed_issue(sev=IssueSeverity.major, ref=None, code="nw", sub=None):
    p = _s.query(Project).filter(Project.code == code).first()
    if not p:
        p = Project(name="纳米Work", code=code, geelib_sub_id=sub)
        _s.add(p); _s.flush()
    elif sub is not None:
        p.geelib_sub_id = sub; _s.flush()
    it = RemainingIssue(project_id=p.id, title="[自动] 回归失败：登录", description="断言失败",
                        severity=sev, status=IssueStatus.open, external_ref=ref)
    _s.add(it); _s.commit()
    return it


def _fake_admin_role():
    """patch assert_project_role 放行（避免建 ProjectMember）。"""
    import app.api.issues as issues_api
    issues_api.assert_project_role = lambda *a, **k: None


def test_endpoint_report_flow():
    import app.api.issues as issues_api
    _fake_admin_role()
    orig_enabled = settings.GEELIB_ENABLED
    orig_report = geelib.report_defect

    # 通道关 → 409
    settings.GEELIB_ENABLED = False
    it = _seed_issue(sub=419)
    try:
        issues_api.report_issue_to_geelib(it.id, db=_s, user=None)
        assert False, "通道关应 409"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409, e

    # 通道开但缺 sub_id → 409
    settings.GEELIB_ENABLED = True
    it2 = _seed_issue(code="nosub", sub=None)
    settings.GEELIB_SUB_MAP = ""  # 确保无映射
    try:
        issues_api.report_issue_to_geelib(it2.id, db=_s, user=None)
        assert False, "缺 sub_id 应 409"
    except Exception as e:
        assert getattr(e, "status_code", None) == 409, e

    # 成功 → 回填 external_ref
    geelib.report_defect = lambda **k: {"ok": True, "matter_id": 777, "ref": "geelib#777", "reason": None}
    it3 = _seed_issue(sub=419)
    resp = issues_api.report_issue_to_geelib(it3.id, db=_s, user=None)
    _s.refresh(it3)
    assert it3.external_ref == "geelib#777", it3.external_ref
    assert resp["data"]["matter_id"] == 777

    # 已上报 → 幂等
    resp2 = issues_api.report_issue_to_geelib(it3.id, db=_s, user=None)
    assert resp2["data"]["already_reported"] is True

    # 上报失败 → 502
    geelib.report_defect = lambda **k: (_ for _ in ()).throw(geelib.GeelibError("极库云 500"))
    it4 = _seed_issue(sub=419)
    try:
        issues_api.report_issue_to_geelib(it4.id, db=_s, user=None)
        assert False, "失败应 502"
    except Exception as e:
        assert getattr(e, "status_code", None) == 502, e

    settings.GEELIB_ENABLED = orig_enabled
    geelib.report_defect = orig_report
    print("OK endpoint report flow")


def main():
    test_get_app_token()
    test_get_app_token_errors()
    test_resolve_sub_id()
    test_build_defect_body()
    test_report_defect_disabled()
    test_report_defect_success_and_fail()
    test_endpoint_report_flow()
    print("OK test_geelib_report")


if __name__ == "__main__":
    main()
