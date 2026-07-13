from datetime import datetime

from sqlalchemy import String, Text, DateTime, Boolean, Enum, ForeignKey, JSON, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import IntegrationEventStatus
from app.db.session import Base


class Integration(Base):
    """集成配置（扩展层）。P0 建表预留，业务在 P3 使用。

    credential_ref 指向密钥存储引用，不明文存凭证。
    """

    __tablename__ = "integration"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), nullable=True, index=True
    )  # NULL = 全局集成
    type: Mapped[str] = mapped_column(String(32), index=True)  # jira/tapd/zentao/jenkins/pytest...
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ApiToken(Base):
    """API Token：供外部代码/CI 推送数据。仅存哈希。P0 建表预留，业务在 P3 使用。"""

    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class IntegrationEvent(Base):
    """Webhook/外部推送落库（审计 + 可重放）。P0 建表预留，业务在 P3 使用。"""

    __tablename__ = "integration_event"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    integration_id: Mapped[int | None] = mapped_column(
        ForeignKey("integration.id", ondelete="SET NULL"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(32), index=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    status: Mapped[IntegrationEventStatus] = mapped_column(
        Enum(IntegrationEventStatus, length=16),
        default=IntegrationEventStatus.received,
        server_default="received",
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
