from datetime import date, datetime

from sqlalchemy import String, Text, Date, DateTime, Enum, ForeignKey, Numeric, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import TaskStatus, TaskPriority
from app.db.session import Base


class Task(Base):
    """每日工作任务分配（管理员下发）。P0 建表预留，业务在 P1 使用。"""

    __tablename__ = "task"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="SET NULL"), nullable=True
    )
    assigned_by: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    assigned_to: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 被测项目/模块
    requirement_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 需求地址
    developer: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 开发
    priority: Mapped[TaskPriority] = mapped_column(
        Enum(TaskPriority, length=8), default=TaskPriority.p2, server_default="p2"
    )
    assigned_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, length=16), default=TaskStatus.pending, server_default="pending"
    )
    # 状态是否已被人工接管：人工（登录用户）改过 status 即置 True，
    # 此后派单同步（agent 的 PATCH）不再覆盖 status，只更新其它元信息。
    status_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 是否是平台自动维护的「线上回归」常驻任务：每项目一个，status 恒为 testing，
    # 用例标记回归时自动挂到它下面（checklist_item 关联）。防误删/误改状态用。
    is_regression_task: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    # 终态时间戳：每次状态「变为」online/closed 时刷新为最新时刻（非首次固定）。
    # 供任务详情显示上线时间，并作为「完成当天」列表带出的依据（见 tasks.list_tasks）。
    online_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 关闭备注：关闭任务时填写的说明，任务详情可见。
    close_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
