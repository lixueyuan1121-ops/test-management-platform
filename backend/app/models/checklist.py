from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ChecklistStatus
from app.db.session import Base


class ChecklistItem(Base):
    """验收清单项：把采纳的测试点(test_case)挂到任务(task)下，带执行状态。

    采纳测试点时自动 upsert（若测试点有 task_id）；也可手动补挂。
    (task_id, test_case_id) 唯一，防重复挂。执行失败可一键转 RemainingIssue。
    """

    __tablename__ = "checklist_item"
    __table_args__ = (UniqueConstraint("task_id", "test_case_id", name="uq_checklist_task_case"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"), index=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    exec_status: Mapped[ChecklistStatus] = mapped_column(
        Enum(ChecklistStatus, length=16), default=ChecklistStatus.pending, server_default="pending"
    )
    executed_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
