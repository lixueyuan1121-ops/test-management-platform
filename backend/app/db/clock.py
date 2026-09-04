"""数据库时钟基准:取 DB 自己的 func.now(),消除进程 utcnow 与 DB(生产东八区)错配。"""
import logging
from datetime import datetime

logger = logging.getLogger("test_platform")


def db_now(db) -> datetime:
    from sqlalchemy import func, select
    try:
        val = db.execute(select(func.now())).scalar()
        if isinstance(val, datetime):
            return val
        if isinstance(val, str) and val:
            return datetime.fromisoformat(val)
    except Exception:
        logger.exception("取数据库时钟失败,回退进程 utcnow")
    return datetime.utcnow()
