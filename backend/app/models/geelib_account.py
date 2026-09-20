from sqlalchemy import BigInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GeelibAccount(Base):
    """个人授权和一次性绑定会话；凭据加密保存，不进入用户信息接口。"""

    __tablename__ = "geelib_account"

    # 历史部署的 user.id 有 INTEGER/BIGINT 两种，独立主键避免新表外键类型不兼容。
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    account_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_auth: Mapped[str | None] = mapped_column(Text, nullable=True)
