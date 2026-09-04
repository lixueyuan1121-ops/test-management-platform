from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class FailCluster(Base):
    """一次版本失败聚类的一个「根因簇」。规则粗聚 + AI 命名的产物。

    run_ids/requirement_ids 存 JSON 字符串列表。issue_id 建缺陷后回填（幂等）。
    batch_key 标识一次聚类批次；重跑生成新 batch_key，同 fingerprint 迁移旧 issue_id。
    """
    __tablename__ = "fail_cluster"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_record.id", ondelete="SET NULL"), nullable=True, index=True)
    root_cause_title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    triage_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(255), index=True)
    run_ids: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON list[int]
    requirement_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[int]
    member_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    issue_id: Mapped[int | None] = mapped_column(
        ForeignKey("remaining_issue.id", ondelete="SET NULL"), nullable=True, index=True)
    batch_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
