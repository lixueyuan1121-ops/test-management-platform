from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ApiEnv(Base):
    """项目级 api 测试环境（被测业务系统的 base_url + 鉴权 + 接口契约）。

    一个 project 一条（project_id 唯一）。auth_json/contract 用 TEXT 存 JSON 字符串
    （兼容 MySQL 5.6 无原生 JSON）。auth_type: fixed（固定 header/token，存 auth_json）
    或 login（token 由用例内登录步骤 extract，auth_json 存登录接口信息，可空）。
    contract: Swagger 导入结果 / 手写清单 / curl 解析累积，注入生成 prompt。
    """

    __tablename__ = "api_env"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_apienv_project"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    base_url: Mapped[str] = mapped_column(String(255), default="", server_default="")
    auth_type: Mapped[str] = mapped_column(String(16), default="fixed", server_default="fixed")
    auth_json: Mapped[str] = mapped_column(Text, default="{}")
    contract: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
