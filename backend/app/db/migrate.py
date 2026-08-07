"""轻量增量迁移：对已存在的库补列，避免丢数据。

生产建议改用 alembic；P1 这里只做"启动时检测 + ALTER ADD COLUMN"，
覆盖 P0→P1 task 表新增 requirement_url / developer 两列的场景。
"""
from sqlalchemy import inspect, text

from app.db.session import engine


def _columns(table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def ensure_task_columns() -> None:
    """task 表补列 requirement_url / developer（如缺失）。"""
    cols = _columns("task")
    if not cols:
        return  # 表尚未建，交给 create_all
    with engine.begin() as conn:
        if "requirement_url" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN requirement_url VARCHAR(512)"))
        if "developer" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN developer VARCHAR(64)"))


def migrate_task_status() -> None:
    """任务状态枚举改版：doing/done/closed → testing/blocked/online 语义映射。

    旧枚举 pending/doing/done/closed → 新枚举 pending/testing/blocked/online。
    映射：doing→testing、done→online、closed→online、pending 不变。
    - MySQL：ENUM 列须先放宽定义（含新旧全部值）再 UPDATE，最后收紧为新定义。
    - SQLite：status 实际存为 TEXT，直接 UPDATE 即可。
    幂等：无旧值时 UPDATE 影响 0 行；重复执行安全。
    """
    if not _columns("task"):
        return  # 表尚未建
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "mysql":
            # 放宽为新旧并集，避免 UPDATE 时旧值/新值任一不在定义内而报错
            conn.execute(text(
                "ALTER TABLE task MODIFY COLUMN `status` "
                "ENUM('pending','doing','done','closed','testing','blocked','online') "
                "NOT NULL DEFAULT 'pending'"
            ))
        conn.execute(text("UPDATE task SET status='testing' WHERE status='doing'"))
        conn.execute(text("UPDATE task SET status='online'  WHERE status='done'"))
        conn.execute(text("UPDATE task SET status='online'  WHERE status='closed'"))
        if dialect == "mysql":
            # 收紧为最终新定义
            conn.execute(text(
                "ALTER TABLE task MODIFY COLUMN `status` "
                "ENUM('pending','testing','blocked','online') "
                "NOT NULL DEFAULT 'pending'"
            ))
