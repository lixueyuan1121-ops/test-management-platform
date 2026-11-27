"""Short, replayable database steps for defect and attachment receipts."""
import logging
from functools import wraps

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

logger = logging.getLogger("test_platform")


def is_disconnect(error):
    codes = getattr(error.orig, "args", ())
    return error.connection_invalidated or bool(codes and codes[0] in (2006, 2013, 2055))


def database_unavailable():
    return HTTPException(503, "平台数据库连接中断，请刷新任务详情查看已保存的工单；回执待核对的候选可点击「核对回执」。")


def report_db_step(operation):
    """Only wrap reads or idempotent local writes. Never replay a skill invocation."""
    @wraps(operation)
    def execute(bind, *args):
        for attempt in range(2):
            try:
                with Session(bind) as db:
                    return operation(db, *args)
            except DBAPIError as error:
                if not is_disconnect(error):
                    raise
                logger.warning("缺陷上报数据库连接中断，步骤 %s，第 %d 次", operation.__name__, attempt + 1)
                if attempt:
                    raise database_unavailable() from error
    return execute
