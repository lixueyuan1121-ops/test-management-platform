"""md 任务执行记录（提测文档 → 测试机 codex 执行）。

为什么独立建表，不往 exec_run 里加一种 kind：
exec_run 被统计、质量门禁、失败聚类、回归智选、发版质量、自动重试等多处引用，
混入"整份文档交给 agent 自由执行"这种非结构化任务会污染这些口径。
本表与 perf_run 同思路：独立表 + 独立队列 + 独立页面，现有链路零改动。

状态一律 VARCHAR（不建 MySQL 原生 ENUM）：新状态不需要 ALTER，也不会因越界静默空串。
"""
import json
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

_TEXT = Text().with_variant(MEDIUMTEXT(), "mysql")


class MdRun(Base):
    __tablename__ = "md_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 项目可为空：md 任务更像"某次提测的整份验证"，不强制挂在项目下；绑了才能上报极库云
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 提测号/需求号：展示与缺陷标题前缀用，不保证唯一（同一需求可多次提测）
    req_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))

    # ---- 下发参数 ----
    runner: Mapped[str] = mapped_column(String(64), default="mac-01", server_default="mac-01", index=True)
    # 测试机本地 hosts 分组的名字（不是 IP）。分组内容只存在执行机上，平台不存。
    env_group: Mapped[str] = mapped_column(String(64))
    client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 来源：page=前端页面；api=命令行提交
    source: Mapped[str] = mapped_column(String(16), default="page", server_default="page")

    # ---- 任务内容（md 原文）----
    content: Mapped[str] = mapped_column(_TEXT)

    # ---- 执行状态 ----
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    # pending / running / passed / failed / blocked / canceled
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)     # pass / fail / blocked
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_md: Mapped[str | None] = mapped_column(_TEXT)                       # agent 产出的结果文档
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)          # JSON: [{title,detail,severity,external_ref}]
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 环境留痕：{group, verify:[{domain,want,got}], restored:true}（不含 IP 原文以外的任何凭据）
    env_check: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    enqueued_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def findings_list(self) -> list:
        try:
            data = json.loads(self.findings) if self.findings else []
        except (ValueError, TypeError):
            return []
        return data if isinstance(data, list) else []
