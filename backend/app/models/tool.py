"""测试工具广场：工具分类 + 工具模型。

对应 P3 集成层的"工具注册"能力——自研辅助测试工具在此登记、上下线、供团队成员浏览下载。
"""
from datetime import datetime

from sqlalchemy import String, Text, Boolean, DateTime, Integer, ForeignKey, Enum, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ToolStatus
from app.db.session import Base


class ToolCategory(Base):
    """工具分类"""
    __tablename__ = "tool_category"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TestTool(Base):
    """测试工具"""
    __tablename__ = "test_tool"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("tool_category.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    doc_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(255), nullable=True)  # emoji 或 URL
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[ToolStatus] = mapped_column(
        Enum(ToolStatus, length=16), default=ToolStatus.online, server_default="online"
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
