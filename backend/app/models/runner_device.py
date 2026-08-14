"""执行设备表 runner_device —— 平台成员登记的自有执行机(runner)。

背景:此前 runner 用单一共享 RUNNER_TOKEN、runner_id 硬编码 mac-01/win-01,与成员账号无绑定。
本表让每个成员登记自己的设备(拿专属 token 填进自己机器的 runner .env),下发时只选"我的设备",
runner 用专属 token 鉴权 → 平台据 token 反查设备归属,天然只拉到派给自己的执行项。

- token:注册时生成的长随机串(secrets.token_hex),runner 端 .env 的 RUNNER_TOKEN 填它。
- runner_id:设备的稳定标识(下发时写入 exec_run.runner;runner .env 的 RUNNER_ID 填它)。
  以 owner 维度唯一(同一人不能有两个同名设备);全局用 token 区分,runner_id 可跨人重名。
- 启动时 Base.metadata.create_all 自动建表。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RunnerDevice(Base):
    __tablename__ = "runner_device"
    __table_args__ = (
        UniqueConstraint("owner_id", "runner_id", name="uk_owner_runner"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    runner_id: Mapped[str] = mapped_column(String(64), index=True)   # 下发写入 exec_run.runner;runner .env 的 RUNNER_ID
    name: Mapped[str] = mapped_column(String(128))                   # 展示名(如「我的 Mac」)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # 专属长期 token(runner 鉴权)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次该设备 runner 拉取时间
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
