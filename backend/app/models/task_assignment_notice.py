from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TaskAssignmentNotice(Base):
    """与人工指派同事务写入的推推消息，发送失败可跨进程重启重试。"""
    __tablename__ = 'task_assignment_notice'
    __table_args__ = (UniqueConstraint('task_id', 'revision', name='uq_task_assignment_revision'),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('task.id', ondelete='CASCADE'), index=True)
    revision: Mapped[int]
    group_id: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0, server_default='0')
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
