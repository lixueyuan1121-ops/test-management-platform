"""对话式测试指挥官(Commander) 能力注册表 + read 能力 自测。

跑法：cd backend && .venv/bin/python -m scripts.test_commander
"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///./tmp_test_commander.db"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp_test_commander.db")
if os.path.exists(_DB):
    os.remove(_DB)

from app.main import app  # noqa: F401,E402
from app.db.session import Base, engine  # noqa: E402

Base.metadata.create_all(engine)

# 首批 read 能力（薄封装既有服务）。缺任一即视为注册回退，测试失败。
EXPECTED_READ = [
    "list_releases",
    "rts_recommendation",
    "rts_candidates",
    "fail_cluster_list",
    "stats_overview",
    "stats_ai_funnel",
]


def test_import_side_effect_registers():
    """import app.services.commander 必须触发 caps 注册（__init__ 的 import 副作用）。

    若漏了 `from . import caps`，REGISTRY 会是空的、所有问题静默落空——此测专防这条
    （同 ai_jobs 的 import 副作用注册教训）。
    """
    import app.services.commander as commander
    assert commander.REGISTRY, "REGISTRY 为空：caps 未被注册（检查 __init__ 的 `from . import caps`）"
    for name in EXPECTED_READ:
        assert name in commander.REGISTRY, f"缺 read 能力：{name}"


def test_get_capability_hit_and_miss():
    import app.services.commander as commander
    cap = commander.get_capability("list_releases")
    assert cap is not None and cap.name == "list_releases", cap
    assert commander.get_capability("不存在的能力") is None, "未命中应返回 None"


def test_capability_shape():
    """每个 Capability 有 name/desc/params/kind/runner，且首批均为 kind=='read'。"""
    import app.services.commander as commander
    for name in EXPECTED_READ:
        cap = commander.REGISTRY[name]
        assert cap.name == name, cap
        assert isinstance(cap.desc, str) and cap.desc, f"{name} 缺 desc"
        assert isinstance(cap.params, dict), f"{name} 的 params 应为 dict"
        assert cap.kind == "read", f"{name} kind 应为 read，实为 {cap.kind}"
        assert callable(cap.runner), f"{name} runner 应可调用"


def test_list_capabilities():
    import app.services.commander as commander
    items = commander.list_capabilities()
    assert isinstance(items, list) and len(items) >= len(EXPECTED_READ), items
    for it in items:
        assert set(it.keys()) == {"name", "desc", "params", "kind"}, it


# ── Task2: 两跳路由 + draft 能力 自测 ──────────────────────────────────────────────

class _FakeEngine:
    """假引擎:每次 stream_generate 按序返回一个 result 文本;记录调用次数以证明第二跳是否被跳过。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def is_available(self):
        return True

    def stream_generate(self, *a, **k):
        i = self.calls
        self.calls += 1
        text = self._responses[i] if i < len(self._responses) else ""
        yield {"type": "result", "text": text}


def _patch_engine(fake):
    """把 rts._pick_provider 换成返回假引擎;返回还原函数。"""
    from app.services import rts
    orig = rts._pick_provider
    rts._pick_provider = lambda name=None: ("claude", fake)
    return lambda: setattr(rts, "_pick_provider", orig)


def _admin_user():
    from types import SimpleNamespace
    return SimpleNamespace(id=1, is_platform_admin=True)


def _seed_release_with_case(code_suffix):
    """建 项目+发版+需求+AiTask+采纳gui用例,返回 (project_id, release_id, version, case_id)。"""
    from datetime import date
    from app.db.session import SessionLocal
    from app.models import Project, ReleaseRecord, Requirement, AiTask, TestCase
    from app.core.enums import ReviewStatus
    db = SessionLocal()
    try:
        pj = Project(name=f"P-cmd-{code_suffix}", code=f"P-CMD-{code_suffix}"); db.add(pj); db.flush()
        rel = ReleaseRecord(project_id=pj.id, version=f"v-{code_suffix}", release_date=date(2026, 9, 1))
        db.add(rel); db.flush()
        req = Requirement(project_id=pj.id, title="需求CMD", release_id=rel.id); db.add(req); db.flush()
        at = AiTask(project_id=pj.id, user_id=1, kind="testcase_gen", input_ref="r"); db.add(at); db.flush()
        tc = TestCase(ai_task_id=at.id, project_id=pj.id, title="用例CMD", requirement_id=req.id,
                      exec_kind="gui", review_status=ReviewStatus.adopted, priority="P1")
        db.add(tc); db.commit()
        return pj.id, rel.id, rel.version, tc.id
    finally:
        db.close()


def test_parse_intent():
    """合法 JSON / 带```json围栏 / 垃圾文本(降级为 clarify) 三种输入。"""
    from app.services.commander import router
    # (a) 干净 JSON
    d = router.parse_intent('{"intent":"list_releases","params":{"limit":5},"missing":[],"clarify":"","reply_if_none":""}')
    assert d["intent"] == "list_releases" and d["params"] == {"limit": 5}, d
    assert d["missing"] == [] and d["clarify"] == "", d
    # (b) 带 ```json 围栏
    d2 = router.parse_intent('```json\n{"intent":"stats_overview","params":{}}\n```')
    assert d2["intent"] == "stats_overview", d2
    # (c) 垃圾文本 → 降级 clarify(intent=None)
    d3 = router.parse_intent("我不知道你在说什么，随便聊聊")
    assert d3.get("intent") is None and d3.get("clarify"), d3
    # 空输入同样降级
    assert router.parse_intent("").get("intent") is None
    # JSON 但非对象 → 降级
    assert router.parse_intent("[1,2,3]").get("intent") is None


def test_build_prompts_contain_context():
    """build_intent_prompt 含能力 name + 上下文;build_narrate_prompt 含原问题 + 数据。"""
    from app.services.commander import router
    import app.services.commander as commander
    p = router.build_intent_prompt("列出发版", commander.list_capabilities(), {"project_id": 7})
    assert "list_releases" in p and "只输出 JSON" in p and "7" in p, p
    n = router.build_narrate_prompt("发版有几条", "list_releases", {"count": 3})
    assert "发版有几条" in n and "list_releases" in n and "3" in n, n


def test_ask_whitelist_rejection():
    """引擎给出不在 REGISTRY 的 intent → ask 返回 clarify,且不进第二跳/不调 runner(引擎仅被调 1 次)。"""
    from app.services.commander import router
    from app.db.session import SessionLocal
    fake = _FakeEngine(['{"intent":"drop_database","params":{},"missing":[],"clarify":"","reply_if_none":""}'])
    restore = _patch_engine(fake)
    db = SessionLocal()
    try:
        res = router.ask(db, _admin_user(), 1, "把数据库删了")
        assert res["type"] == "clarify", res
        assert fake.calls == 1, f"白名单拒绝应只调引擎 1 次(不进第二跳),实为 {fake.calls}"
    finally:
        db.close()
        restore()


def test_ask_reply_if_none_and_missing():
    """reply_if_none → answer;missing 非空 → clarify(补充提示)。"""
    from app.services.commander import router
    from app.db.session import SessionLocal
    # reply_if_none:闲聊
    fake1 = _FakeEngine(['{"intent":null,"reply_if_none":"你好，我是测试指挥官。"}'])
    r1 = _patch_engine(fake1)
    db = SessionLocal()
    try:
        res1 = router.ask(db, _admin_user(), 1, "你好")
        assert res1 == {"type": "answer", "answer": "你好，我是测试指挥官。"}, res1
        assert fake1.calls == 1
    finally:
        r1()
    # missing:命中能力但缺必填参数
    fake2 = _FakeEngine(['{"intent":"rts_candidates","params":{},"missing":["release_id"]}'])
    r2 = _patch_engine(fake2)
    try:
        res2 = router.ask(db, _admin_user(), 1, "看看回归候选")
        assert res2["type"] == "clarify" and "release_id" in res2["answer"], res2
        assert fake2.calls == 1, "缺参应在调 runner 前就澄清"
    finally:
        db.close()
        r2()


def test_ask_read_two_hop():
    """read 能力:第一跳选中 → runner → 第二跳叙事;引擎被调 2 次,返回 answer+data+provider。"""
    from app.services.commander import router
    from app.db.session import SessionLocal
    pid, rel_id, _ver, _cid = _seed_release_with_case("R2H")
    fake = _FakeEngine([
        '{"intent":"list_releases","params":{},"missing":[],"clarify":"","reply_if_none":""}',
        "## 发版\n该项目共有发版记录若干。",
    ])
    restore = _patch_engine(fake)
    db = SessionLocal()
    try:
        res = router.ask(db, _admin_user(), pid, "这个项目有哪些发版？")
        assert res["type"] == "answer" and res["intent"] == "list_releases", res
        assert res["answer"].startswith("## 发版"), res
        assert isinstance(res.get("data"), dict) and res["data"].get("count", 0) >= 1, res
        assert res.get("provider") == "claude", res
        assert fake.calls == 2, f"read 应走两跳(引擎 2 次),实为 {fake.calls}"
    finally:
        db.close()
        restore()


def test_ask_injects_server_project_id():
    """服务端注入的 project_id 覆盖模型给的 project_id(模型不得选项目)。"""
    from app.services.commander import router
    from app.db.session import SessionLocal
    pid, _rel_id, _ver, _cid = _seed_release_with_case("INJ")
    # 模型故意给一个错的 project_id=999999;应被服务端 pid 覆盖 → runner 仍查到本项目发版
    fake = _FakeEngine([
        '{"intent":"list_releases","params":{"project_id":999999}}',
        "叙事文本",
    ])
    restore = _patch_engine(fake)
    db = SessionLocal()
    try:
        res = router.ask(db, _admin_user(), pid, "发版列表")
        assert res["type"] == "answer", res
        assert res["data"]["project_id"] == pid, f"应被服务端 project_id 覆盖:{res['data']}"
    finally:
        db.close()
        restore()


def test_draft_enqueue_regression_no_write():
    """draft_enqueue_regression 产出草稿(endpoint/method/payload/human_summary) 且不写库。"""
    from app.services.commander import caps
    from app.db.session import SessionLocal
    from app.models import ExecRun
    pid, rel_id, ver, cid = _seed_release_with_case("ENQ")
    db = SessionLocal()
    try:
        before = db.query(ExecRun).count()
        d = caps.draft_enqueue_regression(db, _admin_user(), {"release_id": rel_id, "project_id": pid})
        assert set(d.keys()) == {"action", "endpoint", "method", "payload", "human_summary"}, d
        assert d["action"] == "enqueue_regression", d
        assert d["endpoint"] == "/api/exec-queue/enqueue-cases" and d["method"] == "POST", d
        pl = d["payload"]
        assert pl["project_id"] == pid and pl["release_id"] == rel_id, pl
        assert pl["runner"] == "auto", pl
        assert isinstance(pl["test_case_ids"], list) and cid in pl["test_case_ids"], pl
        assert ver in d["human_summary"], d["human_summary"]
        # 显式传 case_ids 应原样使用(映射到 test_case_ids)
        d2 = caps.draft_enqueue_regression(db, _admin_user(),
                                           {"release_id": rel_id, "case_ids": [111, 222], "runner": "mac-01"})
        assert d2["payload"]["test_case_ids"] == [111, 222] and d2["payload"]["runner"] == "mac-01", d2
        after = db.query(ExecRun).count()
        assert before == after, f"draft 不得写库:ExecRun {before}->{after}"
    finally:
        db.close()


def test_draft_create_issue_no_write():
    """draft_create_issue 产出草稿(payload 空,对齐无 body 端点) 且不写库。"""
    from app.services.commander import caps
    from app.db.session import SessionLocal
    from app.models import Project, FailCluster, RemainingIssue
    db = SessionLocal()
    try:
        pj = Project(name="P-cmd-ISS", code="P-CMD-ISS"); db.add(pj); db.flush()
        c = FailCluster(project_id=pj.id, root_cause_title="登录超时根因", fingerprint="fp-iss",
                        member_count=5, severity="major")
        db.add(c); db.commit()
        cid, pid = c.id, pj.id
        before = db.query(RemainingIssue).count()
        d = caps.draft_create_issue(db, _admin_user(), {"cluster_id": cid, "project_id": pid})
        assert set(d.keys()) == {"action", "endpoint", "method", "payload", "human_summary"}, d
        assert d["action"] == "create_issue", d
        assert d["endpoint"] == f"/api/fail-clusters/{cid}/create-issue" and d["method"] == "POST", d
        assert d["payload"] == {}, d
        assert "登录超时根因" in d["human_summary"], d["human_summary"]
        after = db.query(RemainingIssue).count()
        assert before == after, f"draft 不得写库:RemainingIssue {before}->{after}"
    finally:
        db.close()


def test_draft_caps_registered():
    """两个 draft 能力已注册且 kind=='draft'。"""
    import app.services.commander as commander
    for name in ("draft_create_issue", "draft_enqueue_regression"):
        cap = commander.get_capability(name)
        assert cap is not None and cap.kind == "draft", (name, cap)


def test_draft_enqueue_regression_role_gate():
    """草稿闸对齐真实端点 _WRITE_ROLES=(admin, member)：member 可拿草稿，guest/非成员 403。"""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.services.commander import caps
    from app.db.session import SessionLocal
    from app.models import Project, ReleaseRecord, ProjectMember, User
    from app.core.enums import ProjectRole
    from datetime import date

    db = SessionLocal()
    try:
        pj = Project(name="P-cmd-ROLE", code="P-CMD-ROLE"); db.add(pj); db.flush()
        rel = ReleaseRecord(project_id=pj.id, version="v-role", release_date=date(2026, 9, 1))
        db.add(rel); db.flush()
        # 三个真实用户 + 三种角色（platform_admin=False，走 ProjectMember 判权）
        u_mem = User(username="cmd-mem", name="成员", password_hash="x", is_platform_admin=False)
        u_gst = User(username="cmd-gst", name="访客", password_hash="x", is_platform_admin=False)
        u_non = User(username="cmd-non", name="路人", password_hash="x", is_platform_admin=False)
        db.add_all([u_mem, u_gst, u_non]); db.flush()
        db.add_all([
            ProjectMember(user_id=u_mem.id, project_id=pj.id, role=ProjectRole.member),
            ProjectMember(user_id=u_gst.id, project_id=pj.id, role=ProjectRole.guest),
        ])
        db.commit()
        rel_id, mem, gst, non = rel.id, u_mem, u_gst, u_non

        # member：合法，能拿草稿（无 403）
        d = caps.draft_enqueue_regression(db, mem, {"release_id": rel_id, "case_ids": [1]})
        assert d["action"] == "enqueue_regression", d

        # guest：只读角色，403
        for bad in (gst, non):
            try:
                caps.draft_enqueue_regression(db, bad, {"release_id": rel_id, "case_ids": [1]})
                assert False, "guest/非成员应被拒绝"
            except HTTPException as e:
                assert e.status_code == 403, e.status_code
    finally:
        db.close()


def test_draft_create_issue_role_gate():
    """草稿闸对齐真实端点 create-issue 的 admin-only（app/api/fail_cluster.py 用 (ProjectRole.admin,)）：
    admin 可拿草稿，member/guest 403；反查 cluster 真实 project → 跨项目引用他人簇(IDOR)同样 403。"""
    from fastapi import HTTPException

    from app.services.commander import caps
    from app.db.session import SessionLocal
    from app.models import Project, FailCluster, ProjectMember, User
    from app.core.enums import ProjectRole

    db = SessionLocal()
    try:
        # 项目 A + 一个失败簇
        pjA = Project(name="P-cmd-ISSA", code="P-CMD-ISSA"); db.add(pjA); db.flush()
        cA = FailCluster(project_id=pjA.id, root_cause_title="登录超时根因A", fingerprint="fp-issa",
                         member_count=5, severity="major")
        db.add(cA); db.flush()
        # 项目 B + 一个失败簇（供跨项目 IDOR 用）
        pjB = Project(name="P-cmd-ISSB", code="P-CMD-ISSB"); db.add(pjB); db.flush()
        cB = FailCluster(project_id=pjB.id, root_cause_title="登录超时根因B", fingerprint="fp-issb",
                         member_count=3, severity="major")
        db.add(cB); db.flush()
        # A 项目三种角色用户（platform_admin=False，走 ProjectMember 判权）
        u_adm = User(username="iss-adm", name="管理员", password_hash="x", is_platform_admin=False)
        u_mem = User(username="iss-mem", name="成员", password_hash="x", is_platform_admin=False)
        u_gst = User(username="iss-gst", name="访客", password_hash="x", is_platform_admin=False)
        db.add_all([u_adm, u_mem, u_gst]); db.flush()
        db.add_all([
            ProjectMember(user_id=u_adm.id, project_id=pjA.id, role=ProjectRole.admin),
            ProjectMember(user_id=u_mem.id, project_id=pjA.id, role=ProjectRole.member),
            ProjectMember(user_id=u_gst.id, project_id=pjA.id, role=ProjectRole.guest),
        ])
        db.commit()
        cidA, cidB, adm, mem, gst = cA.id, cB.id, u_adm, u_mem, u_gst

        # (a) admin：合法，拿到草稿（含 endpoint/method/payload/human_summary）
        d = caps.draft_create_issue(db, adm, {"cluster_id": cidA})
        assert set(d.keys()) == {"action", "endpoint", "method", "payload", "human_summary"}, d
        assert d["action"] == "create_issue", d
        assert d["endpoint"] == f"/api/fail-clusters/{cidA}/create-issue" and d["method"] == "POST", d

        # (b) member / guest：建缺陷 admin-only → 403
        for bad in (mem, gst):
            try:
                caps.draft_create_issue(db, bad, {"cluster_id": cidA})
                assert False, "member/guest 应被拒绝（create-issue admin-only）"
            except HTTPException as e:
                assert e.status_code == 403, e.status_code

        # (c) 跨项目 IDOR：A 的 admin 谎报 project_id=A 去引用 B 的簇；draft 反查簇真实 project=B，
        #     A-admin 非 B 成员 → 403（服务端不信 params 的 project_id 授权他人对象）。
        try:
            caps.draft_create_issue(db, adm, {"cluster_id": cidB, "project_id": pjA.id})
            assert False, "跨项目引用他人失败簇应被拒绝（IDOR）"
        except HTTPException as e:
            assert e.status_code == 403, e.status_code
    finally:
        db.close()


def test_ask_draft_branch_skips_hop2():
    """draft 意图:runner 产草稿后直接返回,跳过第二跳(引擎仅 1 次),且不写库。"""
    from app.services.commander import router
    from app.db.session import SessionLocal
    from app.models import ExecRun
    pid, rel_id, _ver, cid = _seed_release_with_case("DFT")
    fake = _FakeEngine(['{"intent":"draft_enqueue_regression","params":{"release_id":%d}}' % rel_id])
    restore = _patch_engine(fake)
    db = SessionLocal()
    try:
        before = db.query(ExecRun).count()
        res = router.ask(db, _admin_user(), pid, "把这个版本的回归用例下发执行")
        assert res["type"] == "draft" and res["intent"] == "draft_enqueue_regression", res
        assert res["draft"]["action"] == "enqueue_regression", res
        assert cid in res["draft"]["payload"]["test_case_ids"], res
        assert fake.calls == 1, f"draft 应跳过第二跳(引擎 1 次),实为 {fake.calls}"
        assert db.query(ExecRun).count() == before, "draft 分支不得写库"
    finally:
        db.close()
        restore()


def test_ask_engine_unavailable_degrades():
    """引擎不可用 → 降级为 answer,不崩。"""
    from app.services.commander import router
    from app.db.session import SessionLocal
    from app.services import rts

    class _Down:
        def is_available(self): return False
        def stream_generate(self, *a, **k): yield {"type": "error", "msg": "down"}

    orig = rts._pick_provider
    rts._pick_provider = lambda name=None: ("claude", _Down())
    db = SessionLocal()
    try:
        res = router.ask(db, _admin_user(), 1, "随便问问")
        assert res["type"] == "answer" and "不可用" in res["answer"], res
    finally:
        db.close()
        rts._pick_provider = orig


# ── Task3: API 端点（/ask + /capabilities）+ 鉴权 自测 ─────────────────────────────
#
# 用 FastAPI TestClient 打真实 HTTP 路由（覆盖 router.py 挂载 + 统一信封 + 鉴权网关）：
#   - GET /capabilities：登录即可，返回非空能力清单。
#   - POST /ask 非成员 → 403（网关在调引擎前就挡住，无需假引擎）。
#   - POST /ask 成员 + 假引擎两跳 → code==0、answer。
#   - POST /ask 跨项目/不存在 release_id → 能力内 IDOR 反查 → 403/404（镜像 test_rts）。
# get_current_user 走 dependency_overrides（同 test_rts）；引擎走 _patch_engine（同上文 Task2）。

def _seed_user_member(project_id, role, uname):
    """建一个真实（非平台管理员）用户并登记为某项目指定角色成员，返回 user_id。"""
    from app.db.session import SessionLocal
    from app.models import ProjectMember, User
    db = SessionLocal()
    try:
        u = User(username=uname, name=uname, password_hash="x", is_platform_admin=False)
        db.add(u); db.flush()
        db.add(ProjectMember(user_id=u.id, project_id=project_id, role=role))
        db.commit()
        return u.id
    finally:
        db.close()


def test_api_capabilities():
    """GET /api/commander/capabilities：登录即可（无需项目角色），返回非空能力清单。"""
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.core.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_platform_admin=True)
    try:
        client = TestClient(app)
        r = client.get("/api/commander/capabilities")
        assert r.json()["code"] == 0, r.text
        caps = r.json()["data"]["capabilities"]
        assert isinstance(caps, list) and len(caps) >= len(EXPECTED_READ), caps
        assert all(set(c.keys()) == {"name", "desc", "params", "kind"} for c in caps), caps
    finally:
        app.dependency_overrides.clear()


def test_api_ask_non_member_403():
    """非项目成员 POST /ask → 403（能力网关在调引擎前就挡住越权提问）。"""
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.core.deps import get_current_user
    pid, _rel, _ver, _cid = _seed_release_with_case("APINM")
    # 非平台管理员、非本项目成员
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=987654, is_platform_admin=False)
    try:
        client = TestClient(app)
        r = client.post("/api/commander/ask", json={"project_id": pid, "question": "发版列表"})
        assert r.json()["code"] == 403, r.text
    finally:
        app.dependency_overrides.clear()


def test_api_ask_happy_path():
    """成员 + 假引擎两跳 → code==0，data.type=='answer'，intent 命中 list_releases，
    且服务端注入的 project_id 生效（data.data.project_id==本项目）。"""
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.core.deps import get_current_user
    from app.core.enums import ProjectRole
    pid, _rel, _ver, _cid = _seed_release_with_case("APIHP")
    uid = _seed_user_member(pid, ProjectRole.member, "cmd-api-hp")
    fake = _FakeEngine([
        '{"intent":"list_releases","params":{},"missing":[],"clarify":"","reply_if_none":""}',
        "## 发版\n该项目共有若干发版记录。",
    ])
    restore = _patch_engine(fake)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid, is_platform_admin=False)
    try:
        client = TestClient(app)
        r = client.post("/api/commander/ask", json={"project_id": pid, "question": "这个项目有哪些发版？"})
        assert r.json()["code"] == 0, r.text
        data = r.json()["data"]
        assert data["type"] == "answer" and data["intent"] == "list_releases", data
        assert data["data"]["project_id"] == pid, data
        assert fake.calls == 2, f"read 应走两跳(引擎 2 次)，实为 {fake.calls}"
    finally:
        app.dependency_overrides.clear()
        restore()


def test_api_ask_cross_project_idor():
    """跨项目 IDOR：A 项目成员提问却引用 B 项目的 release_id → 能力内反查鉴权 → 403；
    引用不存在的 release_id → 404。镜像 test_rts 的越权/BAD 断言范式，且证明越权不进第二跳。"""
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from app.core.deps import get_current_user
    from app.core.enums import ProjectRole
    pidA, _relA, _verA, _cidA = _seed_release_with_case("APIA")
    pidB, relB, _verB, _cidB = _seed_release_with_case("APIB")
    uid = _seed_user_member(pidA, ProjectRole.member, "cmd-api-xp")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uid, is_platform_admin=False)
    try:
        # 引用 B 项目的 release（A 成员对 B 无权）→ runner 反查真实归属 → 403
        fake = _FakeEngine(['{"intent":"rts_candidates","params":{"release_id":%d}}' % relB])
        restore = _patch_engine(fake)
        try:
            client = TestClient(app)
            r = client.post("/api/commander/ask", json={"project_id": pidA, "question": "看看回归候选"})
            assert r.json()["code"] == 403, r.text
            assert fake.calls == 1, f"越权应在 runner 反查处即拒，不进第二跳，实为 {fake.calls}"
        finally:
            restore()
        # 引用不存在的 release → 404（不泄漏存在性）
        BAD = 10_000_000
        fake2 = _FakeEngine(['{"intent":"rts_candidates","params":{"release_id":%d}}' % BAD])
        restore2 = _patch_engine(fake2)
        try:
            client = TestClient(app)
            r2 = client.post("/api/commander/ask", json={"project_id": pidA, "question": "看看回归候选"})
            assert r2.json()["code"] == 404, r2.text
        finally:
            restore2()
    finally:
        app.dependency_overrides.clear()


def main():
    test_import_side_effect_registers()
    test_get_capability_hit_and_miss()
    test_capability_shape()
    test_list_capabilities()
    # Task2
    test_parse_intent()
    test_build_prompts_contain_context()
    test_draft_caps_registered()
    test_draft_enqueue_regression_role_gate()
    test_draft_create_issue_role_gate()
    test_draft_enqueue_regression_no_write()
    test_draft_create_issue_no_write()
    test_ask_whitelist_rejection()
    test_ask_reply_if_none_and_missing()
    test_ask_read_two_hop()
    test_ask_injects_server_project_id()
    test_ask_draft_branch_skips_hop2()
    test_ask_engine_unavailable_degrades()
    # Task3: API 端点 + 鉴权
    test_api_capabilities()
    test_api_ask_non_member_403()
    test_api_ask_happy_path()
    test_api_ask_cross_project_idor()
    print("OK test_commander")


if __name__ == "__main__":
    main()
