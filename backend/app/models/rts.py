from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RtsRecommendation(Base):
    """一次 RTS 回归智选的 AI 叙事产物（风险分现算不存，只存叙事供复看）。

    按 release_id 取最新一条；重跑覆盖旧的。focus_points 存 JSON 列表字符串。
    """
    __tablename__ = "rts_recommendation"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_record.id", ondelete="SET NULL"), nullable=True, index=True)
    overall_risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    focus_points: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list[str]
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recommended_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
