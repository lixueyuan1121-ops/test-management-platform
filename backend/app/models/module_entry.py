from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ModuleEntry(Base):
    """模块入口注册:每个模块(= selector_key.page)从首页确定性到达的导航配置。

    按 (project_id, sub_product, page) 分域唯一。nav_keys 存 JSON 数组字符串
    (从首页依次要点的 key,如 ["navAutomation"] 或多级 ["navSettings","settingsTab"]);
    ready_key 为导航后的就绪锚点 key(等它可见=到达)。runner 据此在跑 script 前把页面确定性带到位。
    """

    __tablename__ = "module_entry"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", "page", name="uq_modentry_scope_page"),
        Index("idx_modentry_scope", "project_id", "sub_product"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    page: Mapped[str] = mapped_column(String(64))
    nav_keys: Mapped[str] = mapped_column(Text, default="[]")   # JSON 数组字符串
    ready_key: Mapped[str] = mapped_column(String(64), default="", server_default="")
    desc: Mapped[str] = mapped_column(String(255), default="", server_default="")
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
