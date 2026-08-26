from datetime import datetime

from sqlalchemy import String, Text, Integer, DateTime, Enum, Boolean, Numeric, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import AiTaskStatus, AiInputType, ReviewStatus
from app.db.session import Base


class AiTask(Base):
    """一次 AI 生成任务（M1：QA Copilot 生成测试点）。

    记录输入来源、执行状态、原始产物与成本指标（duration/token/cost），
    成本字段供后续「AI 战绩墙」统计。两张 AI 表与核心业务表解耦，
    仅通过 project_id / task_id 软关联，删项目/任务时级联或置空。
    """

    __tablename__ = "ai_task"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="testcase_gen", server_default="testcase_gen")
    # 生成引擎:claude / deepseek / ...（供「AI 战绩墙」按引擎对比）。老库由 migrate 补列，缺省 claude。
    provider: Mapped[str] = mapped_column(String(16), default="claude", server_default="claude", index=True)
    input_type: Mapped[AiInputType] = mapped_column(
        Enum(AiInputType, length=8), default=AiInputType.text, server_default="text"
    )
    input_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # 需求原文 / URL / 文件名
    status: Mapped[AiTaskStatus] = mapped_column(
        Enum(AiTaskStatus, length=16), default=AiTaskStatus.running, server_default="running"
    )
    output_raw: Mapped[str | None] = mapped_column(Text, nullable=True)  # claude 原始输出全文
    error: Mapped[str | None] = mapped_column(Text, nullable=True)       # 失败原因
    case_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TestCase(Base):
    """AI 生成的结构化测试点/用例。可被「采纳」标记（人工确认保留）。"""

    __tablename__ = "test_case"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ai_task_id: Mapped[int] = mapped_column(ForeignKey("ai_task.id", ondelete="CASCADE"), index=True)
    # 生成引擎:claude / deepseek / ...（冗余自 ai_task.provider，便于用例库/日报直接展示与筛选，免 join）。
    provider: Mapped[str] = mapped_column(String(16), default="claude", server_default="claude", index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 功能/边界/异常/兼容/性能
    title: Mapped[str] = mapped_column(String(512))
    steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)  # P0-P3
    # 自动化执行类型：gui/api/cli/e2e/manual（下发到 runner 时决定 Claude Code 怎么跑）。
    # 缺省 gui（被测客户端主要是 Electron GUI）。老库由 migrate.ensure_testcase_columns 补列。
    exec_kind: Mapped[str] = mapped_column(String(8), default="gui", server_default="gui")
    # 平台：web(PC端) / android / ios。默认 web 保持 PC 端存量数据不变。
    # 控制用例派给哪类 runner；与 exec_kind 正交（web+gui、android+gui 都合法）。
    platform: Mapped[str] = mapped_column(String(16), default="web", server_default="web", index=True)
    # AI 判定该 kind 的理由（供人工复核参考；P2 由生成侧填充）
    kind_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 结构化可执行步骤 JSON（步骤 DSL；P3 由生成侧填充，runner 确定性执行）。存 Text-JSON 兼容 MySQL 5.6。
    script: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 上次「重生 script」失败原因（缺哪个 key / 哪步没断言等）；重生成功即清空。供事后回看逐条修复。
    last_gen_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 关联的选择器页面（逗号分隔多页，仿 channel 惯例兼容 MySQL5.6 无 JSON）。
    # 生成/重生时按 script 用到的 key 自动推断；无 key 用例回落生成时所选页面；用例库可手动改。
    page: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 是否纳入「回归用例库」：长期稳定复用、可按页面勾选直接执行(不依赖任务/采纳)。老库缺省 0。
    is_regression: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    adopted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 三态评审：pending/adopted/rejected（adopted 布尔保留做兼容，见 migrate.ensure_testcase_columns）
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, length=16), default=ReviewStatus.pending, server_default="pending"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
