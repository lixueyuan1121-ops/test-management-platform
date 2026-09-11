from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RecordSession(Base):
    """录制会话：平台建 → runner 拉取并注入捕获脚本 → 增量回传操作事件 → 停止 → 保存为 e2e 用例。

    与一次性的 ProbeRequest 不同,录制是**有状态的交互会话**(开始→操作数分钟→停止),故 events 由 runner
    增量 append。events 存操作步骤 JSON 数组(每步含 action/候选/断言),可能较大 → MySQL 用 LONGTEXT。
    status: pending → recording → stopped → done/failed。
    """

    __tablename__ = "record_session"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    runner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    # 已捕获操作步骤 JSON 数组;runner 每轮 drain 页面缓冲后增量 append(seq 续接)。
    events: Mapped[str] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), default="[]", server_default="[]")
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
