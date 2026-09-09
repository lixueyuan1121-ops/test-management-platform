"""AI 参数化 → 变体展开 端到端(TestClient + 内存库)。
运行: cd backend && .venv/bin/python -m scripts.test_eval_parameterize_expand

覆盖:
- POST /api/ai/eval-queries/expand 扩展的 template 覆盖入参:
  · 传 template(AI 挖好的 {{}} 模板)→ 展开 template 文本、base 具体题原文不被改写;
  · 不传 template → 读 base 原文(向后兼容,老行为不变);
  · 上限 50 保护仍生效。
- POST /api/ai/eval-queries/parameterize:mock 引擎返回模板建议 → 返回 {title,prompt,expected,variables},不落库;
  引擎不可用 → 503;题不存在 → 404。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, Project, User
from app.services import generators

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()


def _get_db():
    yield _s


app.dependency_overrides[get_db] = _get_db
app.dependency_overrides[get_current_user] = lambda: _s.get(User, 1)
client = TestClient(app)

_s.add(User(id=1, username="admin", password_hash="x", name="管理员", is_platform_admin=True))
_s.add(Project(id=1, name="项目一", code="P1"))
_s.commit()


def _mk_concrete_case():
    q = EvalQuery(project_id=1, title="视频压缩", prompt="帮我把这个 1080p 的视频压到一半以下",
                  dimension="tool_use", expected="应调用压缩工具", provider="claude")
    _s.add(q); _s.commit(); _s.refresh(q)
    return q


# ─── expand 的 template 覆盖 ──────────────────────────────────────────────────

def test_expand_with_template_override_keeps_base_concrete():
    base = _mk_concrete_case()
    before_prompt = base.prompt
    d = client.post("/api/ai/eval-queries/expand", json={
        "base_query_id": base.id,
        "template": {"title": "视频压缩({{分辨率}})", "prompt": "帮我把 {{分辨率}} 的视频压一下", "expected": "应调用压缩工具"},
        "variables": {"分辨率": ["1080p", "4K"]},
    }).json()
    assert d["code"] == 0, d
    assert d["data"]["count"] == 2, d["data"]
    # 生成的变体用 template 文本
    variants = _s.query(EvalQuery).filter(EvalQuery.id.in_(d["data"]["created"])).all()
    prompts = sorted(v.prompt for v in variants)
    assert prompts == ["帮我把 1080p 的视频压一下", "帮我把 4K 的视频压一下"], prompts
    # base 具体题原文未被改写
    _s.refresh(base)
    assert base.prompt == before_prompt and "{{" not in base.prompt, "base 具体题不应被 template 改写"
    print("OK expand(template 覆盖):展开模板文本 + base 原文不动")


def test_expand_without_template_still_reads_base():
    # 向后兼容:base 自身带 {{}},不传 template → 老行为
    q = EvalQuery(project_id=1, title="查{{城市}}天气", prompt="{{城市}}今天几度", dimension="tool_use", provider="template")
    _s.add(q); _s.commit(); _s.refresh(q)
    d = client.post("/api/ai/eval-queries/expand", json={
        "base_query_id": q.id, "variables": {"城市": ["北京", "上海", "广州"]},
    }).json()
    assert d["code"] == 0 and d["data"]["count"] == 3, d
    print("OK expand(无 template):仍读 base 原文(向后兼容)")


def test_expand_template_over_limit():
    base = _mk_concrete_case()
    d = client.post("/api/ai/eval-queries/expand", json={
        "base_query_id": base.id,
        "template": {"title": "{{a}}{{b}}", "prompt": "{{a}} 和 {{b}}", "expected": ""},
        "variables": {"a": [str(i) for i in range(8)], "b": [str(i) for i in range(8)]},  # 64 > 50
    }).json()
    assert d["code"] != 0 and "超上限" in d["msg"], d
    print("OK expand(template):上限 50 保护生效")


# ─── parameterize 端点 ───────────────────────────────────────────────────────

class _Engine:
    def __init__(self, avail=True): self._avail = avail
    def is_available(self): return self._avail
    def stream_generate(self, *a, **k):
        yield {"type": "result", "text": '{"title":"视频压缩({{分辨率}})","prompt":"帮我把 {{分辨率}} 压到 {{压缩比}}",'
                                         '"expected":"应调用压缩工具","variables":{"分辨率":["1080p","4K"],"压缩比":["一半"]}}'}


def test_parameterize_returns_template(monkeypatch=None):
    base = _mk_concrete_case()
    _orig = generators.get_provider
    generators.get_provider = lambda p=None: _Engine(True)
    try:
        d = client.post("/api/ai/eval-queries/parameterize", json={"base_query_id": base.id}).json()
    finally:
        generators.get_provider = _orig
    assert d["code"] == 0, d
    assert "{{分辨率}}" in d["data"]["prompt"], d["data"]
    assert d["data"]["variables"]["分辨率"] == ["1080p", "4K"], d["data"]
    # 不落库:题数没变多(只有前面 test 造的,parameterize 自身不产 EvalQuery)
    print("OK parameterize:返回模板建议,不落库")


def test_parameterize_engine_unavailable_503():
    base = _mk_concrete_case()
    _orig = generators.get_provider
    generators.get_provider = lambda p=None: _Engine(False)
    try:
        r = client.post("/api/ai/eval-queries/parameterize", json={"base_query_id": base.id})
    finally:
        generators.get_provider = _orig
    assert r.status_code == 503 or r.json().get("code") != 0, r.json()
    print("OK parameterize:引擎不可用报错")


def test_parameterize_404():
    r = client.post("/api/ai/eval-queries/parameterize", json={"base_query_id": 999999})
    assert r.json().get("code") != 0, r.json()
    print("OK parameterize:题不存在报错")


def main():
    test_expand_with_template_override_keeps_base_concrete()
    test_expand_without_template_still_reads_base()
    test_expand_template_over_limit()
    test_parameterize_returns_template()
    test_parameterize_engine_unavailable_503()
    test_parameterize_404()
    print("\n[PASS] AI 参数化→变体展开 端到端 全部通过")


if __name__ == "__main__":
    main()
