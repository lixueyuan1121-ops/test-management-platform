"""录制表兼容 MySQL 5.6/5.7，且新会话仍以空事件开始。
运行：cd backend && python -m scripts.test_record_session_model
"""
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.models.record_session import RecordSession


def test_mysql_ddl_has_no_text_default():
    ddl = str(CreateTable(RecordSession.__table__).compile(dialect=mysql.dialect()))
    events_line = next(line.strip() for line in ddl.splitlines() if line.strip().startswith("events "))
    assert "LONGTEXT NOT NULL" in events_line, events_line
    assert "DEFAULT" not in events_line.upper(), events_line
    print("OK MySQL DDL: LONGTEXT without server default")


def test_new_session_uses_empty_events():
    engine = create_engine("sqlite:///:memory:")
    try:
        RecordSession.__table__.create(engine)
        with Session(engine) as session:
            # 不显式传 events，验证 ORM 默认值仍会写入数据库。
            first = RecordSession(project_id=1, runner="model-test")
            session.add(first)
            session.commit()
            assert first.events == "[]", first.events
            first.events = '[{"action":"click"}]'
            second = RecordSession(project_id=1, runner="model-test")
            session.add(second)
            session.commit()
            session.expire_all()
            saved = session.scalars(select(RecordSession).order_by(RecordSession.id)).all()
            assert [row.events for row in saved] == ['[{"action":"click"}]', "[]"]
        print("OK ORM default: each session starts with empty events")
    finally:
        engine.dispose()


def main():
    test_mysql_ddl_has_no_text_default()
    test_new_session_uses_empty_events()
    print("ALL OK test_record_session_model")


if __name__ == "__main__":
    main()
