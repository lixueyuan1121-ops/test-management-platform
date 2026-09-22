"""Durable staging; LONGTEXT supports a full 20-case evidence package on MySQL 5.6."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

large_text = Text().with_variant(LONGTEXT(), "mysql")

class VerifiedImportJob(Base):
    __tablename__ = "verified_import_job"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "external_id", name="uq_verified_submission"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    # No project FK: MySQL FK inserts would wait on the worker project lock.
    # Project existence and membership are checked by the acceptance API.
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(Integer)
    external_id: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64))
    requirement: Mapped[str] = mapped_column(String(512))
    payload: Mapped[str] = mapped_column(large_text, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class VerifiedImportItem(Base):
    __tablename__ = "verified_import_item"
    __table_args__ = (UniqueConstraint("job_id", "position", name="uq_verified_item"),
                     Index("ix_verified_ready", "status", "next_attempt_at"))
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("verified_import_job.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    plan: Mapped[str | None] = mapped_column(large_text, nullable=True, deferred=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    receipt: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
