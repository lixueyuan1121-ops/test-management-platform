"""Durable goal orchestration; all transitions and side-effect pointers commit together."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Boolean, UniqueConstraint, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

JSON_TEXT = Text().with_variant(LONGTEXT(), "mysql")


class TestMission(Base):
    __tablename__ = "test_mission"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"))
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    goal: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(16), default="claude")
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey("requirement_analysis.id", ondelete="SET NULL"))
    baseline_id: Mapped[int | None] = mapped_column(ForeignKey("requirement_baseline.id", ondelete="SET NULL"))
    ai_task_id: Mapped[int | None] = mapped_column(ForeignKey("ai_task.id", ondelete="SET NULL"))
    phase: Mapped[str] = mapped_column(String(24), default="analyzing", index=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    active_job_id: Mapped[int | None] = mapped_column(Integer)
    plan: Mapped[str] = mapped_column(JSON_TEXT, default="{}")
    policy: Mapped[str] = mapped_column(JSON_TEXT, default="{}")
    report: Mapped[str] = mapped_column(JSON_TEXT, default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    resume_phase: Mapped[str | None] = mapped_column(String(24))
    interventions: Mapped[int] = mapped_column(Integer, default=0)
    authorized_by: Mapped[int | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class MissionEvent(Base):
    __tablename__ = "test_mission_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("test_mission.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[str] = mapped_column(JSON_TEXT, default="{}")
    actor_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MissionRun(Base):
    __tablename__ = "test_mission_run"
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("test_mission.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("exec_run.id", ondelete="CASCADE"), unique=True)
    case_hash: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[int | None] = mapped_column(Integer)
    criterion_ids: Mapped[str] = mapped_column(JSON_TEXT, default="[]")
    triage_job_id: Mapped[int | None] = mapped_column(Integer)


class MissionAssessment(Base):
    __tablename__ = "test_mission_assessment"
    __table_args__ = (UniqueConstraint("mission_id", "run_id", "source_hash", name="uq_mission_run_evidence"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("test_mission.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("exec_run.id", ondelete="CASCADE"), index=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(JSON_TEXT, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
