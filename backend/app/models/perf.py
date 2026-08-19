"""性能测试记录表 perf_run —— nami-perfdog 采集结果的载体 + 平台下发单。

双轨数据来源（source）：
- dispatch：平台下发任务(pending) → 本地 perf-agent 轮询认领(running) → 跑 nami-perfdog
  → PATCH 回传 meta/samples/events(completed)。适合可无人值守的场景（长监控/cron）。
- upload：本地用 .bat 人工采集（冷启动/对话等需人工介入的场景）→ perf-agent upload
  一次性建 run 并直接填结果(completed)。绕过队列，保留本地交互体验。

两轨最终落到同一张表、同一个报告页（前端复用 report-logic 的分组/胜负/KPI 口径）。

设计要点（对齐全项目约定）：
- scenario/source/status/outcome 一律用 VARCHAR 存，**不用原生 ENUM**——规避 MySQL
  原生 ENUM 遇范围外值静默存空串的坑（见 migrate.ensure_exec_run_kind 的教训），
  且性能场景名允许自定义扩展（自定义场景），字符串最灵活。
- meta/samples/events 用 Text 存 JSON 字符串（兼容生产 MySQL，不依赖原生 JSON 列）。
  samples 回传前已按 LTTB 抽稀（每曲线≤2000 点），JSON 体积可控。
- 启动时 Base.metadata.create_all 自动建表（新表，无需 migrate）。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PerfRun(Base):
    __tablename__ = "perf_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 可空：性能测试更像全局工具，可不依附具体项目；绑了则便于按项目归集/统计。
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 报告集：把本次采集归入某个可命名报告（下发/上传时指定）；集删除时置空、run 保留。
    report_set_id: Mapped[int | None] = mapped_column(
        ForeignKey("perf_report_set.id", ondelete="SET NULL"), nullable=True, index=True
    )
    runner: Mapped[str] = mapped_column(String(64), default="win-01", server_default="win-01", index=True)

    # ---- 采集参数（下发时填，upload 时从回传数据回填）----
    scenario: Mapped[str] = mapped_column(String(32), index=True)   # 对话/切换对话/冷启动/热启动/杀进程/首次安装/长监控/自定义名
    variant: Mapped[str] = mapped_column(String(64), default="default", server_default="default", index=True)  # 被测对象标签(版本号/竞品名)
    proc: Mapped[str | None] = mapped_column(String(128), nullable=True)   # 竞品进程名(--proc)，逗号分隔多进程
    duration: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 长监控时长(--duration，如 12h/30m)

    source: Mapped[str] = mapped_column(String(16), default="dispatch", server_default="dispatch")  # dispatch / upload
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)  # pending/running/completed/failed/canceled

    # ---- 回传结果 ----
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)  # perfdog meta.outcome: completed/failed/timeout/interrupted
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)      # 完整 meta.json（结构化摘要，报告直接用）
    samples_json: Mapped[str | None] = mapped_column(Text, nullable=True)   # 抽稀时序 [{t,source,metric,value}]
    events_json: Mapped[str | None] = mapped_column(Text, nullable=True)    # 阶段标记 [{t,phase,detail}]
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 冗余 meta.durations.totalMs，便于排序/查询
    error: Mapped[str | None] = mapped_column(Text, nullable=True)          # 失败原因（agent 回传或采集异常）

    # 交互采集控制（冷启动/对话等需人工按提示推进的场景）：
    # prompt = agent 转发的 perfdog 当前提示行（null=无待办）；signal_seq = 平台点「继续」的累计次数，
    # agent 消费到更大的值就往 perfdog stdin 写一个回车，把"人在窗口按回车"变成"浏览器点按钮"。
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # 采集起止（来自 meta.startedAt/endedAt 的 epoch ms → DateTime）
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    enqueued_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
