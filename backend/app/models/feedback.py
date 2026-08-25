"""反馈测试模块数据模型（5 表，feedback_ 前缀，与 test_case 体系完全隔离）。

数据流（详见 docs/superpowers/specs/2026-08-24-反馈测试模块-design.md）：
  机器人 POST /api/feedback/ingest 推 md/zip
    → feedback_import(批次) + feedback_case(结构化用例，AI 补 script)
    → feedback_regression_set(回归集) ←→ feedback_case（feedback_set_case 多对多）
    → 定时/手动回归/勾选执行 → 下发内核写 exec_run（source 靠专用项目隔离）
    → feedback_run(批次元数据；total/passed 按 batch_id 聚合 exec_run 现算)

设计要点：
- 所有结构化数据（script/steps/expected/feedback_summary）用 Text 存 JSON/长文本，兼容 MySQL5.6。
- exec_run **零改动**：下发时 test_case_id=None，payload 里带 feedback_case_id 软关联追溯。
- 启动时 Base.metadata.create_all 自动建表（新表，无需 migrate）。
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import FeedbackCaseStatus, FeedbackImportStatus
from app.db.session import Base


class FeedbackImport(Base):
    """一次机器人推送批次（单 md 或 zip 打包多 md）。"""

    __tablename__ = "feedback_import"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    source_bot: Mapped[str | None] = mapped_column(String(64), nullable=True)   # 机器人标识
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)    # 上传原始名（zip 名/单 md 名）
    file_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    case_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[FeedbackImportStatus] = mapped_column(
        Enum(FeedbackImportStatus, length=16),
        default=FeedbackImportStatus.parsing, server_default="parsing",
    )
    script_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")   # 已补 script 条数（进度）
    script_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 需补 script 条数
    note: Mapped[str | None] = mapped_column(Text, nullable=True)               # 机器人附带备注
    error: Mapped[str | None] = mapped_column(Text, nullable=True)             # 逐 md 解析/补 script 累计错误
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FeedbackCase(Base):
    """从 md 拆解出的可执行用例（列直接对齐 md 12 列表格）。"""

    __tablename__ = "feedback_case"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    import_id: Mapped[int] = mapped_column(
        ForeignKey("feedback_import.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    # ---- 需求 / 反馈来源（来自 md 头部，同一 md 内多用例共享）----
    req_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    req_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    feedback_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 测试点 / 用例（来自表格）----
    point_code: Mapped[str | None] = mapped_column(String(32), nullable=True)   # P-101
    point_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    case_no: Mapped[str | None] = mapped_column(String(16), nullable=True)      # C-1
    title: Mapped[str] = mapped_column(String(512))
    precondition: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(16), nullable=True)     # 功能/异常/边界/兼容
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)      # P0-P3
    # ---- 自动化（拆自「自动化(可行/优先级/理由)」列）----
    auto_feasible: Mapped[str] = mapped_column(String(8), default="no", server_default="no")  # yes/partial/no
    auto_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- 执行（AI 补）----
    exec_kind: Mapped[str] = mapped_column(String(8), default="manual", server_default="manual")  # gui/api/cli/e2e/manual
    script: Mapped[str | None] = mapped_column(Text, nullable=True)             # 结构化步骤 JSON 字符串（AI 补）
    script_error: Mapped[str | None] = mapped_column(Text, nullable=True)       # 上次补 script 失败原因
    page: Mapped[str | None] = mapped_column(String(255), nullable=True)        # 关联选择器页面（按 script key 推断）
    status: Mapped[FeedbackCaseStatus] = mapped_column(
        Enum(FeedbackCaseStatus, length=16),
        default=FeedbackCaseStatus.draft, server_default="draft", index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FeedbackRegressionSet(Base):
    """回归用例集：一组反馈用例 + 定时配置。"""

    __tablename__ = "feedback_regression_set"

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


class FeedbackSetCase(Base):
    """回归集 ↔ 反馈用例 多对多关联（防重复入集）。"""

    __tablename__ = "feedback_set_case"
    __table_args__ = (
        UniqueConstraint("set_id", "case_id", name="uq_feedback_set_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("feedback_regression_set.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("feedback_case.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FeedbackRun(Base):
    """一次回归/执行的批次元数据。

    total/passed/failed/blocked 不存——查询时按 batch_id + project_id 聚合 exec_run 现算
    （遵循平台「不建独立统计表」惯例）。case_count 冗余存，便于列表快速展示。
    """

    __tablename__ = "feedback_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    set_id: Mapped[int | None] = mapped_column(
        ForeignKey("feedback_regression_set.id", ondelete="SET NULL"), nullable=True, index=True
    )  # ad-hoc 勾选执行时为 None
    batch_id: Mapped[str] = mapped_column(String(32), index=True)   # 关联 exec_run.batch_id
    trigger: Mapped[str] = mapped_column(String(8), default="manual", server_default="manual")  # auto/manual
    case_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )  # 手动触发人；定时为 None
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
