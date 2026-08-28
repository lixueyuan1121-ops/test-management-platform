"""通用测试计划（TestPlan）——主用例库的「可保存集合 + 定时回归」。

把 FeedbackRegressionSet 验证过的「命名集合 + cron 定时 + 一键整集跑」模式泛化到主用例库
（test_case 体系）：对标主流平台（TestRail Test Plan / MeterSphere 测试计划）的基础形态。
反馈通道保持独立不动——两边用例体系不同（feedback_case vs test_case），强行合并反而耦合。

- TestPlan：计划元信息 + 目标 runner + cron 定时配置。
- TestPlanCase：计划 ↔ 用例 多对多（防重复）。
- TestPlanRun：一次计划执行的批次元数据（对位 FeedbackRun；结果按 batch_id 聚合 exec_run 现算，
  遵循平台「不建独立统计表」惯例）。

三张均为新表：create_all 自动建；sql/schema.sql 已同步 DDL（MySQL/docker 初始化用）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TestPlan(Base):
    __tablename__ = "test_plan"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 5 段 cron；空=未设
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    runner: Mapped[str] = mapped_column(String(64), default="mac-01", server_default="mac-01")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TestPlanCase(Base):
    """计划 ↔ 用例 多对多关联（防重复入计划）。"""

    __tablename__ = "test_plan_case"
    __table_args__ = (
        UniqueConstraint("plan_id", "case_id", name="uq_test_plan_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plan.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TestPlanRun(Base):
    """一次计划执行的批次元数据（对位 FeedbackRun）。

    total/passed/failed 不存——按 batch_id 聚合 exec_run 现算。case_count 冗余存便于列表展示。
    """

    __tablename__ = "test_plan_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_plan.id", ondelete="SET NULL"), nullable=True, index=True
    )
    batch_id: Mapped[str] = mapped_column(String(32), index=True)   # 关联 exec_run.batch_id
    trigger: Mapped[str] = mapped_column(String(8), default="manual", server_default="manual")  # auto/manual
    case_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
