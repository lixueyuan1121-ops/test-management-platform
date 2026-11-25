"""签名任务表：Windows 原生验签和 macOS 公证预检。

链路：用户在平台上传文件(pending) → Windows 校验机 agent 轮询认领(running) → 下载文件、
跑 360Base_test.exe → 回传原始输出 → 后端解析四项签名状态(completed/failed)。

为什么要绕一圈：平台后端在 Linux，而校验程序是 32 位 Windows 程序（依赖 VC++ 2008 x86
运行库和中文区域设置），只能放在一台 Windows 机器上跑。agent 主动轮询平台（同 qalab-runner
模式），校验机不需要对外开端口。

约定（对齐全项目）：
- status/verdict/source 一律 VARCHAR，不用原生 ENUM。
- signatures 用 Text 存 JSON 字符串（兼容生产 MySQL，不依赖原生 JSON 列）。
- 时间戳统一由进程 datetime.utcnow() 写入（naive UTC），出参带 Z。
- 上传的文件只在校验期间保留在 backend/storage/signcheck/，出结果即删，本表不存文件内容。
- 存量 Windows 记录由 ensure_signcheck_columns 填充平台默认值。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SignCheck(Base):
    __tablename__ = "sign_check"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(12), default="windows", server_default="windows")
    team_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # 完整 JSON 和 UTF-8 日志仅在打开单条记录时加载。
    mac_output: Mapped[str | None] = mapped_column(Text().with_variant(LONGTEXT(), "mysql"), nullable=True, deferred=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(16), default="web", server_default="web")  # web / api
    # pending(排队) / running(校验机已认领) / completed(有结果) / failed(未能得到结果)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    # 总体结论：passed / rejected(验签不通过) / invalid / expired / partial / error（未出结果时为空）
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    signatures_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # [{kind,state,detail}]
    exit_code: Mapped[int | None] = mapped_column(BigInteger, nullable=True)   # Windows 退出码可能是 0xC0000135 这类大数
    stdout: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)            # 失败原因 / 排障提示
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)      # 认领的校验机标识
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 校验程序耗时
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SignCheckAgent(Base):
    """校验机心跳：agent 每次轮询刷新 last_seen_at（naive UTC），页面据此显示在线/离线。

    放库里而不是进程内存：后端只要不止一个进程（多 worker、滚动重启时新旧进程并存、
    前面有负载均衡），心跳和页面查询就可能落在不同进程上，表现为「在线/离线来回跳」。
    """
    __tablename__ = "sign_check_agent"

    agent: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MacNotaryAgent(Base):
    """个人启动包与无人值守 Mac 的心跳，同名机器按凭据归属隔离。"""
    __tablename__ = "mac_notary_agent"

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # 0 为共享机
    agent: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
