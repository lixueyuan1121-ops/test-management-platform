from datetime import date, datetime

from sqlalchemy import String, Text, Date, DateTime, Integer, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ReleaseRecord(Base):
    """发版记录(按项目):版本号/发版日期/需求数/上线内容/备忘,供看板统计与详情回溯。"""

    __tablename__ = "release_record"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    version: Mapped[str] = mapped_column(String(64))
    # 子产品：按项目平台类型分两套固定枚举（PC/APP），可空=未指定。校验白名单集中在 api/release.py。
    sub_product: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 发版渠道：仅 APP 端项目使用，多渠道逗号分隔存储(MySQL5.6 无 JSON)；API 层收发数组。
    channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    release_date: Mapped[date] = mapped_column(Date, index=True)
    req_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
