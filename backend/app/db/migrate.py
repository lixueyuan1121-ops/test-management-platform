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
        if "last_gen_error" not in cols:
            # 上次「重生 script」失败原因（成功清空），供事后回看逐条修复。
            conn.execute(text("ALTER TABLE test_case ADD COLUMN last_gen_error TEXT NULL"))
        if "page" not in cols:
            # 关联的选择器页面（逗号分隔多页），供生成收窄 key + 用例库按页维护。
            conn.execute(text("ALTER TABLE test_case ADD COLUMN page VARCHAR(255) NULL"))
        if "is_regression" not in cols:
            # 是否纳入回归用例库（按页面勾选直接执行，不依赖任务/采纳）。
            conn.execute(text("ALTER TABLE test_case ADD COLUMN is_regression TINYINT(1) NOT NULL DEFAULT 0"))
        # 回填：仅把仍为 pending 且 adopted=1 的老行标记为已采纳（幂等）。
        conn.execute(text(
            "UPDATE test_case SET review_status='adopted', reviewed_at=created_at "
            "WHERE adopted=1 AND review_status='pending'"
        ))
    # 回归筛选高频列索引（在事务块外单独建，避免嵌套 engine.begin 死锁；_ensure_index 幂等）。
    _ensure_index("test_case", "idx_testcase_regression", "project_id, is_regression")


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
            # status 加 blocked(L2:选择器/环境阻塞)。原生 ENUM 遇范围外值静默存 ''(同 kind 的坑),
            # 故上线 L2 前必须放宽此列,否则 runner 回写的 blocked 会被写坏成空串。
            conn.execute(text(
                "ALTER TABLE exec_run MODIFY COLUMN `status` "
                "ENUM('pending','running','passed','failed','blocked') NOT NULL DEFAULT 'pending'"
            ))
        # 修复:此前 e2e/manual 被静默写成 '' 的坏行 → 归回 manual(不可自动化,不误派)。
        # 两方言都执行(幂等:无坏行则 0 行受影响)。
        conn.execute(text("UPDATE exec_run SET kind='manual' WHERE kind='' OR kind IS NULL"))


def ensure_exec_run_report_columns() -> None:
    """exec_run 补列 batch_id / report / fail_kind(执行结果批次汇总 + 逐步截图报告 + L2 失败分类)。

    batch_id：一次 enqueue 生成一个,该批所有 run 共享,供结果页按批分组现算汇总(老行 NULL=未分批)。
    report：runner 回写的逐步执行报告 JSON(每步 action/desc/ok/截图 URL + 结论),截图本身走 uploads/。
    fail_kind(L2)：selector=选择器/环境阻塞(不计功能失败率)/business=功能失败;老行 NULL(旧数据不分类)。
    幂等：ADD 前先探列;索引单独幂等建。
    """
    cols = _columns("exec_run")
    if not cols:
        return  # 表尚未建，交给 create_all
    with engine.begin() as conn:
        if "batch_id" not in cols:
            conn.execute(text("ALTER TABLE exec_run ADD COLUMN batch_id VARCHAR(32) NULL"))
        if "report" not in cols:
            conn.execute(text("ALTER TABLE exec_run ADD COLUMN report TEXT NULL"))
        if "fail_kind" not in cols:
            conn.execute(text("ALTER TABLE exec_run ADD COLUMN fail_kind VARCHAR(16) NULL"))
    _ensure_index("exec_run", "idx_execrun_batch", "batch_id")


def ensure_perf_run_columns() -> None:
    """perf_run 表补列 report_set_id / prompt / signal_seq（如缺失）。

    report_set_id：报告集拆分功能；prompt/signal_seq：交互采集的平台控制功能。
    老库启动时补上。两方言通用 ADD COLUMN，幂等：ADD 前先探列。
    """
    cols = _columns("perf_run")
    if not cols:
        return  # 表尚未建，交给 create_all
    with engine.begin() as conn:
        if "report_set_id" not in cols:
            conn.execute(text("ALTER TABLE perf_run ADD COLUMN report_set_id BIGINT NULL"))
        if "prompt" not in cols:
            conn.execute(text("ALTER TABLE perf_run ADD COLUMN prompt TEXT NULL"))
        if "signal_seq" not in cols:
            conn.execute(text("ALTER TABLE perf_run ADD COLUMN signal_seq INT NOT NULL DEFAULT 0"))


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


def ensure_selector_frame_width() -> None:
    """selector_key.frame 列宽放宽到 128（容纳 url:<hostname> 深层 frame 定位）。

    老库 frame 原为 VARCHAR(8)（仅存 shell/vm/auto）。MySQL 需 MODIFY 放宽；SQLite 声明长度
    不强制、写入不截断，无需 DDL（仅 MySQL 执行）。幂等：重复 MODIFY 安全。
    """
    if not _columns("selector_key"):
        return  # 表尚未建
    if engine.dialect.name == "mysql":
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE selector_key MODIFY COLUMN `frame` VARCHAR(128) NOT NULL DEFAULT 'auto'"))


def ensure_probe_screenshot_column() -> None:
    """probe_request 补列 screenshot_path（探测整页截图的相对路径，存 uploads/ 下）。

    截图走独立文件通道（不进 result TEXT，MySQL 5.6 TEXT 上限 64KB 会截断 base64）。
    幂等：ADD 前先探列。
    """
    cols = _columns("probe_request")
    if not cols:
        return  # 表尚未建
    if "screenshot_path" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE probe_request ADD COLUMN screenshot_path VARCHAR(255) NULL"))


def ensure_probe_result_longtext() -> None:
    """probe_request.result 放宽 TEXT→LONGTEXT。

    真实复杂页面(如 namiclaw 业务 iframe)单帧可交互元素达数百个，探测结果 JSON
    (groups×elements×candidates)常 200KB+，超 MySQL TEXT 的 64KB 上限会截断成坏 JSON，
    前端解析失败→空白。SQLite TEXT 无长度限制，仅 MySQL 需 MODIFY。幂等：重复 MODIFY 安全。
    """
    cols = _columns("probe_request")
    if not cols or "result" not in cols:
        return  # 表尚未建
    if engine.dialect.name == "mysql":
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE probe_request MODIFY COLUMN `result` LONGTEXT NULL"))


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


def ensure_api_env_table(engine=None) -> None:
    """建 api_env 表(幂等)。create_all 已能建;此处显式 CREATE(checkfirst)保证
    老库无需依赖模型 import 时机也能补出该表(与 ensure_selector_tables 一致)。"""
    from app.db.session import engine as _default_engine
    from app.models.api_env import ApiEnv
    eng = engine if engine is not None else _default_engine
    ApiEnv.__table__.create(bind=eng, checkfirst=True)


def ensure_eval_query_dimension() -> None:
    """eval_query 补 dimension 列(对话测评题主考维度)。老库已建表故走 ALTER;新库 create_all 已含,探到即跳过(幂等)。"""
    if not _columns("eval_query"):
        return  # 表还没建(全新库 create_all 尚未跑到)——create_all 会带上该列,无需 ALTER
    if "dimension" not in _columns("eval_query"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_query ADD COLUMN dimension VARCHAR(16) NULL"))


def ensure_eval_run_target_engine() -> None:
    """eval_run 补 target_engine 列(被测引擎)。老库已建表走 ALTER;新库 create_all 已含,幂等跳过。"""
    if not _columns("eval_run"):
        return
    if "target_engine" not in _columns("eval_run"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN target_engine VARCHAR(32) NULL"))


def ensure_eval_run_payload() -> None:
    """eval_run 补 payload 列(下发时的题面快照 JSON)。老库已建表走 ALTER;新库 create_all 已含,幂等跳过。"""
    if not _columns("eval_run"):
        return
    if "payload" not in _columns("eval_run"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN payload TEXT NULL"))


def ensure_eval_run_target_device() -> None:
    """eval_run 补 target_device 列(目标设备 vm_id)。老库已建表走 ALTER;新库 create_all 已含,幂等跳过。"""
    if not _columns("eval_run"):
        return
    if "target_device" not in _columns("eval_run"):
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN target_device VARCHAR(64) NULL"))


def ensure_eval_task_tables() -> None:
    """建 eval_task 表(幂等) + eval_run 补 eval_task_id 列(测评任务子分类)。

    新库 create_all 自带;老库这里显式补表/补列(与 ensure_selector_tables 同款套路)。
    eval_task_id 不加 FK(老库 ALTER 加 FK 在 MySQL 上易因既有数据/引擎设置失败,查询按 id 关联即可)。
    """
    from app.models.ai_eval import EvalTask
    EvalTask.__table__.create(bind=engine, checkfirst=True)
    cols = _columns("eval_run")
    if cols and "eval_task_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN eval_task_id BIGINT NULL"))
        _ensure_index("eval_run", "idx_evalrun_task", "eval_task_id")
    # eval_task 补 dialog_options 列(最近一次执行的对话选项快照;建表早于该字段的老库走 ALTER)
    tcols = _columns("eval_task")
    if tcols and "dialog_options" not in tcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_task ADD COLUMN dialog_options TEXT NULL"))
    # eval_task 补定时执行四列(CI 回归守卫)
    if tcols and "schedule_cron" not in tcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_task ADD COLUMN schedule_cron VARCHAR(64) NULL"))
            conn.execute(text("ALTER TABLE eval_task ADD COLUMN schedule_enabled TINYINT NOT NULL DEFAULT 0"))
            conn.execute(text("ALTER TABLE eval_task ADD COLUMN schedule_runner VARCHAR(64) NULL"))
            conn.execute(text("ALTER TABLE eval_task ADD COLUMN last_auto_run_at DATETIME NULL"))
    # eval_run 补 score 列(判定引擎 1-5 总体评分;NULL=未评/老数据)
    rcols = _columns("eval_run")
    if rcols and "score" not in rcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN score INT NULL"))
    # eval_run 补人工复核两列(失败收敛标注:误报/漏报/认可 + 说明)
    if rcols and "review_mark" not in rcols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN review_mark VARCHAR(16) NULL"))
            conn.execute(text("ALTER TABLE eval_run ADD COLUMN review_note TEXT NULL"))


def ensure_platform_columns() -> None:
    """selector_key / test_case / runner_device 三表补 platform 列（APP 端支持）。

    platform 值域：web(PC端) / android / ios。默认 web 保证所有存量数据语义不变。
    新表由 create_all 自带，老库在此补列（幂等：ADD 前先探列）。
    同步建索引 idx_selkey_platform / idx_testcase_platform / idx_runnerdev_platform
    便于按平台筛选选择器、用例、设备。
    """
    sk_cols = _columns("selector_key")
    if sk_cols and "platform" not in sk_cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE selector_key ADD COLUMN platform VARCHAR(16) NOT NULL DEFAULT 'web'"
            ))
        _ensure_index("selector_key", "idx_selkey_platform", "platform")

    tc_cols = _columns("test_case")
    if tc_cols and "platform" not in tc_cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE test_case ADD COLUMN platform VARCHAR(16) NOT NULL DEFAULT 'web'"
            ))
        _ensure_index("test_case", "idx_testcase_platform", "platform")

    rd_cols = _columns("runner_device")
    if rd_cols and "platform" not in rd_cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE runner_device ADD COLUMN platform VARCHAR(16) NOT NULL DEFAULT 'web'"
            ))
        _ensure_index("runner_device", "idx_runnerdev_platform", "platform")
