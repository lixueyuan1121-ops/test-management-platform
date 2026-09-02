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
    # platform: web(PC端) / android / ios。标识该执行机能运行哪类用例，派单时做匹配检查。
    platform: Mapped[str] = mapped_column(String(16), default="web", server_default="web", index=True)
    # capabilities: 【已弃用】原静态能力集(func,eval)。改为运行时感知(见 last_exec_at/last_eval_at)后
    # 不再读写此列——一台机能接什么由它实际在跑哪个 runner 动态决定,而非静态配置。列保留不删(生产
    # 若已建则留作死列,避免删列迁移风险);新库仍建它(server_default 全能力),但代码永不据此判断。
    capabilities: Mapped[str] = mapped_column(
        String(64), default="func,eval", server_default="func,eval"
    )
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)  # 专属长期 token(runner 鉴权)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次该设备 runner 拉取(任意队列),在线判定用
    # 运行时 runner 类型感知:一台机同一时刻只能跑一类 runner(功能/测评,抢同一客户端不能并行)。
    # 功能 runner 轮询 exec-queue → 刷 last_exec_at;测评 runner 轮询 eval-queue → 刷 last_eval_at。
    # 「当前在跑哪类」= 对应时间戳在在线窗口内(叠加 running 补偿执行期不轮询的滞后)。看板/派单/拦截皆据此。
    last_exec_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # 最近一次功能 runner(exec-queue)拉取
    last_eval_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # 最近一次测评 runner(eval-queue)拉取
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
