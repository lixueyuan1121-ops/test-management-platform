"""AI 任务队列表:ai_job —— 全平台 AI 动作(归因/生成/判定/综合评价…)统一的入队/执行记录。

方案2「AI 任务异步队列」的骨架(见 docs/superpowers/specs/2026-09-01-ai-job-queue-design.md):
特性端点建 pending job 立即返回 job_id;进程内 worker 池抢占消费→调引擎→写域表→done/failed;
前端轮询取结果。只作「执行/队列记录」,域写入(TestCase/ExecRun.triage/…)由各 handler 完成——
故不复用已绑生成域的 AiTask。结构化数据用 Text 存 JSON(兼容 MySQL5.6,不用原生 JSON 列)。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AiJob(Base):
    __tablename__ = "ai_job"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 动作类型:triage/testcase_gen/eval_query_gen/eval_judge/eval_summary/script_gen/feedback_script
    kind: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(16), default="claude", server_default="claude")
    # pending/running/done/failed/cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON:该 kind 所需入参快照
    result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON:结构化结果(回前端渲染)
    output_raw: Mapped[str | None] = mapped_column(Text, nullable=True)  # 模型原始文本(排障)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 回链域对象(如 exec_run/ai_task/eval_task),便于幂等与追溯
    ref_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    worker: Mapped[str | None] = mapped_column(String(48), nullable=True)  # 抢到的线程名(排障)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
