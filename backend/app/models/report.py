from datetime import date, datetime

from sqlalchemy import String, Text, Date, DateTime, Boolean, Numeric, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class DailyReport(Base):
    """日报反馈：一个 task 在一个 report_date 下只有一条。P0 建表预留，业务在 P1 使用。"""

    __tablename__ = "daily_report"
    __table_args__ = (
        UniqueConstraint("task_id", "report_date", name="uk_task_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    progress_pct: Mapped[int] = mapped_column(default=0)  # 0-100
    is_online: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    online_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    workload_hours: Mapped[float] = mapped_column(Numeric(5, 1), default=0, server_default="0")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
