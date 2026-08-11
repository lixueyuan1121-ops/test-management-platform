from datetime import datetime

from sqlalchemy import String, Text, DateTime, Enum, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import IssueSeverity, IssueStatus
from app.db.session import Base


class RemainingIssue(Base):
    """遗留问题（从日报结构化拆出，便于跟踪与统计）。P0 建表预留，业务在 P2 使用。"""

    __tablename__ = "remaining_issue"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("daily_report.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    checklist_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("checklist_item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[IssueSeverity] = mapped_column(
        Enum(IssueSeverity, length=16), default=IssueSeverity.minor, server_default="minor"
    )
    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus, length=16), default=IssueStatus.open, server_default="open"
    )
    owner: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    external_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 关联 Jira/Tapd 缺陷ID
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
