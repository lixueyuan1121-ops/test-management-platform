from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.mysql import LONGTEXT

from app.db.session import Base


class TaskDigest(Base):
    """筛选台账生成的日报快照，独立于逐任务填报与工时统计。"""
    __tablename__ = "task_digest"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    task_ids: Mapped[str] = mapped_column(Text)
    filter_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TaskDigestPublication(Base):
    """Immutable HTML snapshot; reading still requires platform/project access."""
    __tablename__ = "task_digest_publication"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    digest_id: Mapped[int] = mapped_column(ForeignKey("task_digest.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(24), unique=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    html_sha256: Mapped[str] = mapped_column(String(64))
    html: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskDigestGeneration(Base):
    """Input/result snapshots kept outside the queue's MySQL TEXT size limit."""
    __tablename__ = "task_digest_generation"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    input: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"))
    result: Mapped[str | None] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskDigestDeployment(Base):
    """The existing Nami report service's result for an immutable publication."""
    __tablename__ = "task_digest_deployment"
    publication_id: Mapped[int] = mapped_column(ForeignKey("task_digest_publication.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    hosting: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
