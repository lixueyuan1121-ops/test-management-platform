from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SelectorKey(Base):
    """语义选择器注册表（单一事实源）。

    按 (project_id, sub_product) 分域，每个 key 记一条候选定位策略（candidates
    存 JSON 字符串，兼容 MySQL 5.6 无原生 JSON）。frame 指明该 key 归属外层壳
    还是内嵌 iframe（auto/shell/iframe）。runner/生成侧据此确定性定位元素。
    """

    __tablename__ = "selector_key"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", "key", name="uq_selkey_scope_key"),
        Index("idx_selkey_scope", "project_id", "sub_product"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    key: Mapped[str] = mapped_column(String(64))
    frame: Mapped[str] = mapped_column(String(128), default="auto", server_default="auto")
    page: Mapped[str] = mapped_column(String(64), default="", server_default="")
    desc: Mapped[str] = mapped_column(String(255), default="", server_default="")
    candidates: Mapped[str] = mapped_column(Text, default="[]")  # JSON 字符串
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SelectorScope(Base):
    """每个 (project_id, sub_product) 域的 vm_iframe 配置（内嵌被测页的 iframe 定位）。"""

    __tablename__ = "selector_scope"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", name="uq_selscope"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    vm_iframe: Mapped[str] = mapped_column(String(255), default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProbeRequest(Base):
    """设备探测请求：平台侧下发一次探测，runner 拉取执行并回写 result/error。

    params/result 用 TEXT 存 JSON 字符串（兼容 MySQL 5.6 无原生 JSON）。
    status: pending → running → done/failed。runner 列标识目标执行机。
    """

    __tablename__ = "probe_request"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    runner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    params: Mapped[str] = mapped_column(Text, default="{}")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
