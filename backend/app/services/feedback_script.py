"""反馈用例补 script：导入后对可自动化用例（auto_feasible∈{yes,partial}）调 AI 引擎生成结构化 script。

复用功能测试的生成引擎（generators）。关键：generate_script 调 claude CLI 可阻塞数十秒~数分钟，
参照 ai.py 的教训——AI 阻塞期间**不持有 DB 连接**（否则生产 MySQL 空闲超时断连），
故取字段快照后关 session、逐条调 AI、再开短 session 写回。
"""
import json
import logging

from app.core.enums import FeedbackCaseStatus
from app.db.session import SessionLocal
from app.models import FeedbackCase
from app.services import generators
from app.services.claude_runner import pages_for_script

logger = logging.getLogger("test_platform")


def fill_scripts_for_import(import_id: int) -> None:
    """后台线程入口：对该批可自动化用例逐条补 script。引擎不可用则整批跳过（case 保持 draft）。"""
    engine = generators.get_provider(generators.DEFAULT_PROVIDER)
    if not engine.is_available():
        logger.warning("反馈补 script 跳过：生成引擎「%s」不可用", generators.DEFAULT_PROVIDER)
        return

    # ---- 阶段 1：取字段快照后关闭 session（避免 AI 阻塞期间持连接）----
    db = SessionLocal()
    try:
        rows = (
            db.query(
                FeedbackCase.id, FeedbackCase.title, FeedbackCase.steps,
                FeedbackCase.expected, FeedbackCase.project_id,
            )
            .filter(
                FeedbackCase.import_id == import_id,
                FeedbackCase.auto_feasible.in_(["yes", "partial"]),
                FeedbackCase.script.is_(None),   # 只补还没 script 的（续补幂等：中断后重跑只补未成功的）
            )
            .all()
        )
        # 选择器来源项目（被测产品）：反馈项目自己不建选择器，补 script 借用被测产品的库
        from app.api.feedback import selector_project_id
        sel_pid = selector_project_id(db, rows[0].project_id) if rows else None
        snapshots = [(r.id, r.title, r.steps or "", r.expected or "") for r in rows]
    finally:
        db.close()

    logger.info("反馈补 script 开始：import=%s，%s 条待补（选择器项目=%s）", import_id, len(snapshots), sel_pid)

    # ---- 阶段 2：逐条调 AI（不持 session）→ 短 session 写回 ----
    for cid, title, steps, expected in snapshots:
        try:
            script, err = engine.generate_script("gui", title, steps, expected, project_id=sel_pid)
        except Exception as e:  # 引擎异常不影响其余
            script, err = None, str(e)

        s = SessionLocal()
        try:
            fc = s.get(FeedbackCase, cid)
            if fc:
                if err or not script:
                    fc.script_error = (err or "空 script")[:2000]
                else:
                    fc.script = json.dumps(script, ensure_ascii=False)
                    fc.script_error = None
                    p = pages_for_script(script, sel_pid)
                    if p:
                        fc.page = p
                    fc.status = FeedbackCaseStatus.ready
            s.commit()
        finally:
            s.close()

    logger.info("反馈补 script 完成：import=%s", import_id)
