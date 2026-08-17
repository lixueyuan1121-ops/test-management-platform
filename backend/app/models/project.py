from datetime import datetime

from sqlalchemy import String, DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ProjectRole, ProjectStatus
from app.db.session import Base


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 平台类型：pc/app，控制发版记录页的子产品枚举与是否显示发版渠道列；NULL=未分类(按 PC 端展示)。
    platform_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, length=16), default=ProjectStatus.active, server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Team(Base):
    """项目下的团队（一个项目可多团队）。P0 建表预留，业务在 P1+ 使用。"""

    __tablename__ = "team"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProjectMember(Base):
    """项目成员（授权核心）：用户 ↔ 项目 多对多，带角色。"""

    __tablename__ = "project_member"
    __table_args__ = (
        UniqueConstraint("user_id", "project_id", name="uk_user_project"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("team.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[ProjectRole] = mapped_column(
        Enum(ProjectRole, length=16), default=ProjectRole.member, server_default="member"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
