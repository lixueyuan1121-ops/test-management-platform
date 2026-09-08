"""EvalRun.raw_message 字段(WorkBuddy「复制 message」原始 JSON 回填)自测。
运行: python -m scripts.test_eval_raw_message

背景:WorkBuddy 消息工具栏「更多操作 → 复制 message」复制出完整结构化 JSON
(requestId/traceId/conversationId/完整思维链 reasoning/modelId/工具调用数/时间戳),
价值远超已有 answer(仅最终正文)。新增 EvalRun.raw_message(TEXT)存原始串,供后续分析。
本测覆盖:①模型有该字段且可存长文本 ②_to_out 序列化含 raw_message ③reset 清空它。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import EvalRun, Project
from app.core.enums import EvalRunStatus
from app.api.eval_queue import _to_out, reset_run_for_retry

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)

_SAMPLE = '{"requestId":"req-123","traceId":"abc","messages":[{"content":[{"type":"reasoning","text":"思考过程"}]}]}'


def test_model_stores_raw_message():
    s = _Session()
    s.add(Project(id=1, name="P", code="P1", status="active"))
    r = EvalRun(project_id=1, status=EvalRunStatus.done, raw_message=_SAMPLE)
    s.add(r); s.commit()
    got = s.query(EvalRun).first()
    assert got.raw_message == _SAMPLE, f"raw_message 应原样存取,得 {got.raw_message!r}"
    print("✓ 模型存取 raw_message(结构化 JSON 串)")


def test_to_out_includes_raw_message():
    s = _Session()
    r = s.query(EvalRun).first()
    out = _to_out(r)
    assert "raw_message" in out, "_to_out 应含 raw_message 键"
    assert out["raw_message"] == _SAMPLE, "序列化值应等于存入值"
    print("✓ _to_out 序列化含 raw_message")


def test_reset_clears_raw_message():
    s = _Session()
    r = s.query(EvalRun).first()
    assert r.raw_message  # 前置:有值
    reset_run_for_retry(r)
    assert r.raw_message is None, "重跑重置应清空 raw_message(避免旧批次数据串到新批次)"
    print("✓ reset 清空 raw_message")


def main():
    test_model_stores_raw_message()
    test_to_out_includes_raw_message()
    test_reset_clears_raw_message()
    print("\n✅ EvalRun.raw_message 全部通过")


if __name__ == "__main__":
    main()
