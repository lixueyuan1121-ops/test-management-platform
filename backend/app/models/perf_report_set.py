"""性能报告集 perf_report_set —— 把若干次采集归入一个可命名、独立展示的报告。

需求背景：原来所有采集全堆在一个报告页。引入"报告集"后：下发/上传前先建或选一个
报告集（默认用时间戳命名、支持重命名），采集归入该集；报告页按报告集切换，各集独立。
例：本次验证 A+B 归一个集，下次验证 A+C 归另一个集，互不干扰。

- name：展示名，默认时间戳（前端建集时给），可重命名。
- created_by：建集人（删集权限校验用）。
- perf_run.report_set_id 指向本表；集删除时 run 的外键 SET NULL（run 记录保留，只是脱离该集）。
- thresholds_json：性能红线(阈值告警,优化项)。JSON {metricKey: {max?: n, min?: n}},
  metricKey 对齐前端 perf-report-logic 的 KPI 键(cpuPeak/memPeak/ttftMs/fpsAvg …)。
  采集 completed 时逐指标比对,超线推飞书告警(见 services/perf_guard.py)。空=未设不告警。
- 启动时 Base.metadata.create_all 自动建表。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PerfReportSet(Base):
    __tablename__ = "perf_report_set"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    # 性能红线 JSON(Text 兼容 MySQL5.6);空/NULL=未设。
    thresholds_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
