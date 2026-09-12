"""Versioned requirement reviews; confirmation snapshots are immutable.

New tables only: existing generations remain readable without a baseline. JSON and
full source snapshots use LONGTEXT on MySQL (60,000 Chinese characters exceed TEXT).
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

_Document = Text().with_variant(LONGTEXT(), "mysql")


class RequirementSource(Base):
    __tablename__ = "requirement_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_by: Mapped[int] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    url: Mapped[str] = mapped_column(String(2048), default="")
    text: Mapped[str] = mapped_column(_Document)
    # Images stay behind authenticated APIs; never served from public uploads/.
    materials: Mapped[str] = mapped_column(_Document, default="[]")
    warnings: Mapped[str] = mapped_column(_Document, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RequirementAnalysis(Base):
    __tablename__ = "requirement_analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by: Mapped[int] = mapped_column(Integer)
    source_text: Mapped[str] = mapped_column(_Document)
    source_hash: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    source_title: Mapped[str] = mapped_column(String(512), default="")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("requirement_source.id", ondelete="SET NULL"), nullable=True)
    source_info: Mapped[str] = mapped_column(_Document, default="{}")
    provider: Mapped[str] = mapped_column(String(16))
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    draft: Mapped[str | None] = mapped_column(_Document, nullable=True)
    visual_readings: Mapped[str] = mapped_column(_Document, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class RequirementBaseline(Base):
    __tablename__ = "requirement_baseline"
    __table_args__ = (UniqueConstraint("analysis_id", "revision", name="uq_requirement_baseline_revision"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("requirement_analysis.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[str] = mapped_column(_Document)
    confirmed_by: Mapped[int] = mapped_column(Integer)
    confirmation_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RequirementGeneration(Base):
    __tablename__ = "requirement_generation"

    ai_task_id: Mapped[int] = mapped_column(ForeignKey("ai_task.id", ondelete="CASCADE"), primary_key=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("requirement_baseline.id", ondelete="CASCADE"), index=True)


class RequirementCaseLink(Base):
    __tablename__ = "requirement_case_link"
    __table_args__ = (UniqueConstraint("test_case_id", "criterion_id", name="uq_requirement_case_criterion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id", ondelete="CASCADE"), index=True)
    baseline_id: Mapped[int] = mapped_column(ForeignKey("requirement_baseline.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(48))
    criterion_id: Mapped[str] = mapped_column(String(64))
    # Updated only when a person explicitly adopts the case; edits invalidate coverage.
    reviewed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
