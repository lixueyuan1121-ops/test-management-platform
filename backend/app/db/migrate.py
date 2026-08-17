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


def _indexes(table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {ix["name"] for ix in insp.get_indexes(table)}


def _ensure_index(table: str, name: str, cols: str) -> None:
    """幂等建索引:不存在才建(MySQL/SQLite 通用 CREATE INDEX)。"""
    if not _columns(table) or name in _indexes(table):
        return
    with engine.begin() as conn:
        conn.execute(text(f"CREATE INDEX {name} ON {table} ({cols})"))


def ensure_perf_indexes() -> None:
    """性能索引(高频筛选列):test_case 按项目+采纳态/按 reviewed_at(统计趋势)。"""
    _ensure_index("test_case", "idx_testcase_proj_review", "project_id, review_status")
    _ensure_index("test_case", "idx_testcase_reviewed", "reviewed_at")


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
        # 终态时间戳 + 关闭备注（P2 任务分配调整）：变为 online/closed 时刷新，供详情显示与「完成当天」列表口径。
        if "online_at" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN online_at DATETIME NULL"))
        if "closed_at" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN closed_at DATETIME NULL"))
        if "close_note" not in cols:
            conn.execute(text("ALTER TABLE task ADD COLUMN close_note TEXT NULL"))


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
        if "exec_kind" not in cols:
            # 自动化执行类型 gui/api/cli；老行缺省 gui（下发时回落到 GUI 用例）。
            conn.execute(text(
                "ALTER TABLE test_case ADD COLUMN exec_kind VARCHAR(8) "
                "NOT NULL DEFAULT 'gui'"
            ))
        if "kind_reason" not in cols:
            conn.execute(text("ALTER TABLE test_case ADD COLUMN kind_reason TEXT NULL"))
        if "script" not in cols:
            conn.execute(text("ALTER TABLE test_case ADD COLUMN script TEXT NULL"))
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


def ensure_exec_run_kind() -> None:
    """exec_run.kind/status 的 MySQL 原生 ENUM 放宽到新枚举值(P1 加了 e2e/manual)。

    根因:kind 原为 ENUM('gui','api','cli'),加 e2e/manual 后未同步 MySQL 列定义。
    MySQL 原生 ENUM 遇范围外值**静默存成空串 ''**(不报错)→ runner GET 读回空串,
    `r.kind.value` 抛 'str' object has no attribute 'value' → 整个轮询 500。
    - MySQL:MODIFY 放宽 kind/status 为含新值的定义;并把已被写坏的 '' 行修回默认值。
    - SQLite:非原生 ENUM,存的就是原字符串,无需 DDL;坏行修复同样执行(幂等)。
    """
    if not _columns("exec_run"):
        return  # 表尚未建，交给 create_all
    dialect = engine.dialect.name
    with engine.begin() as conn:
        if dialect == "mysql":
            conn.execute(text(
                "ALTER TABLE exec_run MODIFY COLUMN `kind` "
                "ENUM('gui','api','cli','e2e','manual') NOT NULL DEFAULT 'gui'"
            ))
            conn.execute(text(
                "ALTER TABLE exec_run MODIFY COLUMN `status` "
                "ENUM('pending','running','passed','failed') NOT NULL DEFAULT 'pending'"
            ))
        # 修复:此前 e2e/manual 被静默写成 '' 的坏行 → 归回 manual(不可自动化,不误派)。
        # 两方言都执行(幂等:无坏行则 0 行受影响)。
        conn.execute(text("UPDATE exec_run SET kind='manual' WHERE kind='' OR kind IS NULL"))


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


def ensure_project_columns() -> None:
    """project 表补列 platform_type（如缺失）。pc/app，控制发版页子产品枚举与渠道列；NULL=未分类。"""
    cols = _columns("project")
    if not cols:
        return  # 表尚未建，交给 create_all
    with engine.begin() as conn:
        if "platform_type" not in cols:
            conn.execute(text("ALTER TABLE project ADD COLUMN platform_type VARCHAR(16) NULL"))


def ensure_release_columns() -> None:
    """release_record 表补列 sub_product / channel（如缺失）。

    sub_product：按项目平台类型分两套固定枚举，老库补列后老记录 NULL=未指定。
    channel：仅 APP 端项目用，多渠道逗号分隔存储(MySQL5.6 无 JSON)。
    """
    cols = _columns("release_record")
    if not cols:
        return  # 表尚未建，交给 create_all
    with engine.begin() as conn:
        if "sub_product" not in cols:
            conn.execute(text("ALTER TABLE release_record ADD COLUMN sub_product VARCHAR(32) NULL"))
        if "channel" not in cols:
            conn.execute(text("ALTER TABLE release_record ADD COLUMN channel VARCHAR(255) NULL"))


def ensure_ai_provider_columns() -> None:
    """ai_task / test_case 补列 provider（生成引擎：claude/deepseek/...）。

    老库补列后存量行默认 'claude'（历史用例都由 claude 生成，语义正确）。
    DEFAULT 'claude' 保证新库/存量行都有初值；索引便于战绩墙按引擎分组聚合。
    """
    at_cols = _columns("ai_task")
    if at_cols and "provider" not in at_cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE ai_task ADD COLUMN provider VARCHAR(16) NOT NULL DEFAULT 'claude'"
            ))
        _ensure_index("ai_task", "idx_aitask_provider", "provider")
    tc_cols = _columns("test_case")
    if tc_cols and "provider" not in tc_cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE test_case ADD COLUMN provider VARCHAR(16) NOT NULL DEFAULT 'claude'"
            ))
        _ensure_index("test_case", "idx_testcase_provider", "provider")


def ensure_selector_page_column() -> None:
    """selector_key 表补列 page（页面分组维度，纯组织用，不参与定位）。

    老库补列后存量行 page=''（未分类）；新库由 ensure_selector_tables 依模型建表即含 page。
    幂等：ADD 前先探列。
    """
    cols = _columns("selector_key")
    if not cols:
        return  # 表尚未建，交给 ensure_selector_tables
    if "page" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE selector_key ADD COLUMN page VARCHAR(64) NOT NULL DEFAULT ''"))


def ensure_selector_tables(engine=None) -> None:
    """建 selector_key / selector_scope(幂等)。

    create_all 已能建新表；此处显式 CREATE(checkfirst) 保证老库无需依赖模型 import
    时机也能补出这两张表。engine 缺省用模块级 engine（与其它 ensure_* 一致），
    显式传入便于脚本/冒烟测试复用。
    """
    from app.db.session import engine as _default_engine
    from app.models.selector import SelectorKey, SelectorScope, ProbeRequest
    eng = engine if engine is not None else _default_engine
    SelectorKey.__table__.create(bind=eng, checkfirst=True)
    SelectorScope.__table__.create(bind=eng, checkfirst=True)
    ProbeRequest.__table__.create(bind=eng, checkfirst=True)
