"""对话测评用例「模板导入 + 按维度/测评任务搜索」自测。
运行: cd backend && python -m scripts.test_eval_import

覆盖:
- parse_eval_template: CSV/TSV(飞书 sheets)分隔符自适应、中英文表头别名、维度中文名→key、
  缺必需列(标题/提问)整行跳过并记行号、多轮分组(conversation_group+turn_index)、
  BOM 与含逗号的带引号字段、飞书多表 `# 表名`/重复表头行跳过、表头缺列 → ValueError。
- POST /api/ai/eval-queries/import: dry_run 预览(不落库)、真导入落库(provider=import)、
  挂进测评任务(有序去重)、feishu_url 走 extractors 取文(mock)。
- GET /api/ai/eval-queries?dimension=&eval_task_id=: 维度过滤、测评任务过滤、二者叠加(AND)。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.deps import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models import EvalQuery, Project, User
from app.models.ai_eval import EvalTask
from app.services.eval_import import parse_eval_template

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


# ─── 解析器单元测试(纯函数,无 DB) ──────────────────────────────────────────────

def test_parse_csv_basic():
    text = (
        "标题,维度,提问prompt,预期expected,对话组,轮次\n"
        "查北京天气,tool_use,帮我查北京今天天气,应联网搜索给出温度,g1,0\n"
    )
    rows, skipped = parse_eval_template(text)
    assert not skipped, skipped
    assert len(rows) == 1
    r = rows[0]
    assert r["title"] == "查北京天气"
    assert r["dimension"] == "tool_use"
    assert r["prompt"] == "帮我查北京今天天气"
    assert r["expected"] == "应联网搜索给出温度"
    assert r["conversation_group"] == "g1"
    assert r["turn_index"] == 0
    print("OK parse csv basic")


def test_parse_tsv_feishu_sheet_with_title_line():
    # 飞书 sheets 取文形态:`# 表名` 前缀 + Tab 分隔
    text = (
        "# Sheet1\n"
        "标题\t维度\t提问prompt\t预期expected\t对话组\t轮次\n"
        "查天气\ttool_use\t查北京天气\t给出温度\tg1\t0\n"
    )
    rows, skipped = parse_eval_template(text)
    assert len(rows) == 1, (rows, skipped)
    assert rows[0]["prompt"] == "查北京天气"
    assert rows[0]["conversation_group"] == "g1"
    print("OK parse tsv feishu sheet")


def test_parse_dimension_chinese_and_unknown():
    text = (
        "标题,维度,提问prompt\n"
        "题1,工具·MCP调用,问1\n"      # 中文标签 → tool_use
        "题2,不存在的维度,问2\n"       # 未知 → None
        "题3,thinking,问3\n"          # key 原样
    )
    rows, skipped = parse_eval_template(text)
    assert [r["dimension"] for r in rows] == ["tool_use", None, "thinking"], rows
    print("OK parse dimension chinese/unknown")


def test_parse_skips_missing_required_and_records_row():
    text = (
        "标题,维度,提问prompt\n"
        "有标题无提问,thinking,\n"      # 缺 prompt → 跳过
        ",thinking,无标题有提问\n"      # 缺 title → 跳过
        "好题,thinking,好提问\n"        # 正常
    )
    rows, skipped = parse_eval_template(text)
    assert len(rows) == 1 and rows[0]["title"] == "好题", rows
    assert len(skipped) == 2, skipped
    # 行号按数据行(不含表头)计,便于用户定位
    assert skipped[0]["line"] == 1 and skipped[1]["line"] == 2, skipped
    assert "提问" in skipped[0]["reason"] and "标题" in skipped[1]["reason"], skipped
    print("OK parse skips missing required")


def test_parse_multiturn_grouping():
    text = (
        "标题,维度,提问prompt,对话组,轮次\n"
        "推荐电影,multi_turn,推荐一部电影,g2,0\n"
        "推荐电影,multi_turn,换成喜剧的,g2,1\n"
    )
    rows, _ = parse_eval_template(text)
    assert [r["conversation_group"] for r in rows] == ["g2", "g2"]
    assert [r["turn_index"] for r in rows] == [0, 1]
    print("OK parse multiturn grouping")


def test_parse_bom_and_quoted_comma():
    # Excel 存 CSV 常带 UTF-8 BOM;含逗号的字段用双引号包裹
    text = (
        "﻿标题,维度,提问prompt\n"
        '带逗号,thinking,"你好,请问今天,天气如何"\n'
    )
    rows, skipped = parse_eval_template(text)
    assert not skipped, skipped
    assert rows[0]["title"] == "带逗号"
    assert rows[0]["prompt"] == "你好,请问今天,天气如何", rows
    print("OK parse bom + quoted comma")


def test_parse_english_headers_and_repeated_header_skipped():
    text = (
        "title,dimension,prompt,expected\n"
        "t1,thinking,p1,e1\n"
        "title,dimension,prompt,expected\n"   # 多表拼接时重复表头 → 跳过,不当数据
        "t2,thinking,p2,e2\n"
    )
    rows, _ = parse_eval_template(text)
    assert [r["title"] for r in rows] == ["t1", "t2"], rows
    print("OK parse english headers + repeated header skipped")


def test_parse_missing_required_header_raises():
    text = "维度,预期\nthinking,e1\n"  # 无 标题/提问 列
    try:
        parse_eval_template(text)
    except ValueError as e:
        assert "标题" in str(e) or "提问" in str(e), e
        print("OK parse missing header raises")
        return
    raise AssertionError("表头缺必需列应抛 ValueError")


# ─── 端点测试(导入 + 搜索) ──────────────────────────────────────────────────────

from unittest.mock import patch  # noqa: E402

_CSV_2VALID_1SKIP = (
    "标题,维度,提问prompt,预期expected,对话组,轮次\n"
    "查天气,tool_use,查北京天气,给出温度,,0\n"          # 单轮,无对话组 → 导入后应补唯一组名
    "推荐,thinking,推荐一部电影,给出推荐,g2,0\n"
    "坏行,thinking,,缺提问,,0\n"                        # 缺 prompt → 跳过
)


def _seed():
    _s.add(User(id=1, username="admin", password_hash="x", name="管理员", is_platform_admin=True))
    _s.add(Project(id=1, name="项目一", code="P1"))
    _s.add(Project(id=2, name="搜索项目", code="P2"))
    _s.commit()


def _count_queries(pid: int) -> int:
    return _s.query(EvalQuery).filter(EvalQuery.project_id == pid).count()


def test_import_dry_run_preview():
    before = _count_queries(1)
    d = client.post("/api/ai/eval-queries/import",
                    json={"project_id": 1, "text": _CSV_2VALID_1SKIP, "dry_run": True}).json()
    assert d["code"] == 0, d
    data = d["data"]
    assert data["dry_run"] is True and data["count"] == 2, data
    assert len(data["skipped"]) == 1 and data["skipped"][0]["line"] == 3, data
    assert len(data["preview"]) == 2, data
    assert _count_queries(1) == before, "dry_run 不应落库"
    print("OK import dry_run preview")


def test_import_real_persists():
    before = _count_queries(1)
    d = client.post("/api/ai/eval-queries/import",
                    json={"project_id": 1, "text": _CSV_2VALID_1SKIP, "dry_run": False}).json()
    assert d["code"] == 0, d
    assert d["data"]["count"] == 2, d
    assert _count_queries(1) == before + 2, d
    new = (_s.query(EvalQuery).filter(EvalQuery.project_id == 1)
           .order_by(EvalQuery.id.desc()).limit(2).all())
    assert all(q.provider == "import" for q in new), [q.provider for q in new]
    # 单轮无对话组的那条应被补上唯一组名(不为空)
    assert all(q.conversation_group for q in new), [q.conversation_group for q in new]
    print("OK import real persists")


def test_import_attach_to_task():
    task = EvalTask(project_id=1, name="导入任务", query_ids=json.dumps([]))
    _s.add(task); _s.commit(); _s.refresh(task)
    d = client.post("/api/ai/eval-queries/import",
                    json={"project_id": 1, "text": _CSV_2VALID_1SKIP,
                          "eval_task_id": task.id, "dry_run": False}).json()
    assert d["code"] == 0, d
    assert d["data"]["attached"] == 2, d
    _s.refresh(task)
    assert len(json.loads(task.query_ids)) == 2, task.query_ids
    print("OK import attach to task")


def test_import_from_feishu_url_mock():
    tsv = "# Sheet1\n标题\t维度\t提问prompt\n飞书题\ttool_use\t飞书里的提问\n"
    before = _count_queries(1)
    with patch("app.api.ai_eval.extractors.extract_from_url", return_value=("表标题", tsv)):
        d = client.post("/api/ai/eval-queries/import",
                        json={"project_id": 1, "feishu_url": "https://x.feishu.cn/sheets/abc",
                              "dry_run": False}).json()
    assert d["code"] == 0 and d["data"]["count"] == 1, d
    assert _count_queries(1) == before + 1, d
    print("OK import from feishu url (mock)")


def test_import_feishu_url_must_be_feishu_host():
    # SSRF 护栏:非飞书主机的 URL 必须在取文前被拒(extract_from_url 不得被调用)
    from unittest.mock import MagicMock
    m = MagicMock()
    with patch("app.api.ai_eval.extractors.extract_from_url", m):
        r = client.post("/api/ai/eval-queries/import",
                        json={"project_id": 1, "feishu_url": "http://169.254.169.254/latest/meta-data",
                              "dry_run": True})
    assert r.status_code == 400, r.text
    assert "飞书" in r.json().get("msg", ""), r.text
    m.assert_not_called()
    print("OK import rejects non-feishu url (SSRF guard)")


def test_import_requires_source():
    d = client.post("/api/ai/eval-queries/import", json={"project_id": 1, "dry_run": True}).json()
    assert d["code"] != 0, "无 text/feishu_url 应报错"
    print("OK import requires source")


def test_import_bad_header_400():
    r = client.post("/api/ai/eval-queries/import",
                    json={"project_id": 1, "text": "维度,预期\nthinking,e1\n", "dry_run": True})
    assert r.status_code == 400, r.text
    print("OK import bad header 400")


def test_search_by_dimension():
    _s.add(EvalQuery(project_id=2, title="t1", prompt="p1", dimension="thinking", provider="manual"))
    _s.add(EvalQuery(project_id=2, title="t2", prompt="p2", dimension="tool_use", provider="manual"))
    _s.commit()
    d = client.get("/api/ai/eval-queries", params={"project_id": 2, "dimension": "thinking"}).json()
    assert d["code"] == 0, d
    assert d["data"] and all(q["dimension"] == "thinking" for q in d["data"]), d
    assert all(q["title"] != "t2" for q in d["data"]), d
    print("OK search by dimension")


def test_search_by_eval_task():
    q_in = EvalQuery(project_id=2, title="任务内", prompt="pp", dimension="thinking", provider="manual")
    q_out = EvalQuery(project_id=2, title="任务外", prompt="pp2", dimension="thinking", provider="manual")
    _s.add_all([q_in, q_out]); _s.commit(); _s.refresh(q_in)
    task = EvalTask(project_id=2, name="搜索任务", query_ids=json.dumps([q_in.id]))
    _s.add(task); _s.commit(); _s.refresh(task)
    d = client.get("/api/ai/eval-queries", params={"project_id": 2, "eval_task_id": task.id}).json()
    ids = [q["id"] for q in d["data"]]
    assert ids == [q_in.id], (ids, q_in.id, q_out.id)
    print("OK search by eval task")


def test_search_dimension_and_task():
    qa = EvalQuery(project_id=2, title="A", prompt="p", dimension="thinking", provider="manual")
    qb = EvalQuery(project_id=2, title="B", prompt="p", dimension="tool_use", provider="manual")
    _s.add_all([qa, qb]); _s.commit(); _s.refresh(qa); _s.refresh(qb)
    task = EvalTask(project_id=2, name="混合", query_ids=json.dumps([qa.id, qb.id]))
    _s.add(task); _s.commit(); _s.refresh(task)
    d = client.get("/api/ai/eval-queries",
                   params={"project_id": 2, "eval_task_id": task.id, "dimension": "tool_use"}).json()
    ids = [q["id"] for q in d["data"]]
    assert ids == [qb.id], (ids, qa.id, qb.id)
    print("OK search dimension AND task")


def _endpoint_tests():
    _seed()
    test_import_dry_run_preview()
    test_import_real_persists()
    test_import_attach_to_task()
    test_import_from_feishu_url_mock()
    test_import_feishu_url_must_be_feishu_host()
    test_import_requires_source()
    test_import_bad_header_400()
    test_search_by_dimension()
    test_search_by_eval_task()
    test_search_dimension_and_task()


def _parser_tests():
    test_parse_csv_basic()
    test_parse_tsv_feishu_sheet_with_title_line()
    test_parse_dimension_chinese_and_unknown()
    test_parse_skips_missing_required_and_records_row()
    test_parse_multiturn_grouping()
    test_parse_bom_and_quoted_comma()
    test_parse_english_headers_and_repeated_header_skipped()
    test_parse_missing_required_header_raises()


def main():
    _parser_tests()
    _endpoint_tests()
    print("OK test_eval_import")


if __name__ == "__main__":
    main()
