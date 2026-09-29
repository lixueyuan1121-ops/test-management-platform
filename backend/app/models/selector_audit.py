from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime, Boolean, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base

class SelectorAuditSchedule(Base):
    __tablename__ = 'selector_audit_schedule'
    __table_args__ = (UniqueConstraint('project_id', 'sub_product', name='uq_sel_audit_scope'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default='')
    device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_time: Mapped[str] = mapped_column(String(5), default='09:00')
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_probe_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str] = mapped_column(String(500), default='')

class SelectorAuditRun(Base):
    __tablename__ = 'selector_audit_run'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int] = mapped_column(Integer, index=True)
    probe_id: Mapped[int] = mapped_column(Integer, unique=True)
    trigger: Mapped[str] = mapped_column(String(16), default='manual')
    snapshot: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, 'mysql'), default='{}')
    summary: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, 'mysql'), default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class SelectorAuditObservation(Base):
    __tablename__ = 'selector_audit_observation'
    __table_args__ = (UniqueConstraint('key_id', 'page_token', name='uq_sel_audit_seen'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_id: Mapped[int] = mapped_column(Integer, index=True)
    page_token: Mapped[str] = mapped_column(String(64))
    misses: Mapped[int] = mapped_column(Integer, default=0)
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
