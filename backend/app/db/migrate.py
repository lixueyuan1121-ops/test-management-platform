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
        if "status_locked" not in cols:
            conn.execute(text(
                "ALTER TABLE task ADD COLUMN status_locked TINYINT(1) NOT NULL DEFAULT 0"
            ))


def ensure_testcase_columns() -> None:
    """test_case 表补列 review_status / reviewed_at（如缺失），并回填老数据。

    三态评审字段（供「AI 战绩墙」统计采纳率）。回填规则：老库里已 adopted=1
    的行视为「已采纳」——review_status='adopted'、reviewed_at=created_at；其余保持
    pending。ADD COLUMN 的 DEFAULT 'pending' 保证新库/存量行都有初值。
    幂等：ADD 前先探列；回填 UPDATE 带 review_status='pending' 条件，改过的行
    （已是 adopted）不再命中，重复执行不改变数值。
    """
    cols = _columns("test_case")
    if not cols:
        return  # 表尚未建，交给 create_all
    with engine.begin() as conn:
        if "review_status" not in cols:
            conn.execute(text(
                "ALTER TABLE test_case ADD COLUMN review_status VARCHAR(16) "
                "NOT NULL DEFAULT 'pending'"
            ))
        if "reviewed_at" not in cols:
            conn.execute(text("ALTER TABLE test_case ADD COLUMN reviewed_at DATETIME NULL"))
        # 回填：仅把仍为 pending 且 adopted=1 的老行标记为已采纳（幂等）。
        conn.execute(text(
            "UPDATE test_case SET review_status='adopted', reviewed_at=created_at "
            "WHERE adopted=1 AND review_status='pending'"
        ))


def migrate_task_status() -> None:
    """任务状态枚举改版：doing/done → testing/online 语义映射。

    旧枚举 pending/doing/done → 新枚举 pending/testing/blocked/online/closed。
    映射：doing→testing、done→online、pending 不变。
    注意：closed 现在是**独立状态**（已关闭：不再跟进/取消/合并），不再归并到 online。
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
        if dialect == "mysql":
            # 收紧为最终新定义（含 closed 独立态）
            conn.execute(text(
                "ALTER TABLE task MODIFY COLUMN `status` "
                "ENUM('pending','testing','blocked','online','closed') "
                "NOT NULL DEFAULT 'pending'"
            ))


def ensure_issue_columns() -> None:
    """remaining_issue 表补列 task_id / checklist_item_id（如缺失），并放宽 report_id 可空。

    放宽 report_id：SQLite 列约束宽松，NOT NULL 不阻塞新路径的 NULL 插入无需 DDL；
    MySQL 需 MODIFY COLUMN 去掉 NOT NULL。加列/放宽都幂等：ADD 前探列，MODIFY 重复执行安全。
    """
    cols = _columns("remaining_issue")
    if not cols:
        return  # 表尚未建，交给 create_all
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if "task_id" not in cols:
            conn.execute(text("ALTER TABLE remaining_issue ADD COLUMN task_id BIGINT NULL"))
        if "checklist_item_id" not in cols:
            conn.execute(text("ALTER TABLE remaining_issue ADD COLUMN checklist_item_id BIGINT NULL"))
        if dialect == "mysql":
            # report_id 放宽为可空。MySQL 不允许直接 MODIFY 被 FK 引用的列（errno 1832
            # "Cannot change column ... used in a foreign key constraint"），需先 drop 该 FK
            # → MODIFY → 重建 FK。FK 名不硬编码：用 inspector 按 constrained_columns 查其真实名
            # （老库多为自动命名 remaining_issue_ibfk_1）。幂等：report_id 已可空则整段跳过；
            # 重建统一命名为 fk_issue_report（与 schema.sql 对齐），后续 startup 再跑时因已可空跳过。
            insp = inspect(engine)
            rid = next((c for c in insp.get_columns("remaining_issue")
                        if c["name"] == "report_id"), None)
            if rid is not None and rid["nullable"] is False:
                fk_name = next(
                    (fk["name"] for fk in insp.get_foreign_keys("remaining_issue")
                     if fk.get("constrained_columns") == ["report_id"] and fk.get("name")),
                    None,
                )
                if fk_name:
                    conn.execute(text(f"ALTER TABLE remaining_issue DROP FOREIGN KEY `{fk_name}`"))
                conn.execute(text("ALTER TABLE remaining_issue MODIFY COLUMN `report_id` BIGINT NULL"))
                if fk_name:
                    conn.execute(text(
                        "ALTER TABLE remaining_issue ADD CONSTRAINT `fk_issue_report` "
                        "FOREIGN KEY (`report_id`) REFERENCES `daily_report`(`id`) ON DELETE CASCADE"
                    ))
