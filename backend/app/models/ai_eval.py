"""对话测评链路的数据模型：eval_query（测评题）+ eval_run（一次执行+判定）。

与 models/ai.py 的 test_case（功能测试点）是不同领域，故独立文件。
生成任务复用现有 AiTask（kind='eval_query_gen'），此处不重复建生成任务表。
一切结构化数据用 Text 存 JSON 字符串（兼容 MySQL 5.6，不用原生 JSON 列）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import EvalDeviceKind, EvalRunStatus, ReviewStatus
from app.db.session import Base


class EvalQuery(Base):
    """一道对话测评题：发给被测大模型的 query 及其执行参数。类比 test_case。"""

    __tablename__ = "eval_query"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 哪次 AI 生成的（复用 AiTask，kind='eval_query_gen'）；人工录入为 NULL。
    ai_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 生成引擎 claude/deepseek，冗余自 ai_task.provider（免 join），default claude。
    provider: Mapped[str] = mapped_column(String(16), default="claude", server_default="claude", index=True)
    title: Mapped[str] = mapped_column(String(512))
    # 该 query 主考的对话测评维度(thinking/tool_use/artifact/multi_turn/instruction);生成侧填,可空
    dimension: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)  # query 正文（发给被测模型的提问）
    attachments: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: [{name,file_token?/url?}]
    # 多轮分组键（对齐 CLI conversationId）；NULL/空 = 单轮独立会话。
    conversation_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    turn_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 同组内第几轮（0 起）
    dialog_options: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: {model?,chatMode?,thinkingDepth?}
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)  # 期望产物/行为（判定参照；可空）
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, length=16), default=ReviewStatus.pending, server_default="pending"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EvalRun(Base):
    """一次对话测评执行 + 判定结果。类比 exec_run，但承载会话全过程轨迹 + 三维判定。

    一道题可派到不同设备/多次执行，各留一条。判定与执行合并在此表（一对一）。
    """

    __tablename__ = "eval_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # —— 下发 ——（题删了执行记录仍留痕，故 SET NULL，学 exec_run.test_case_id）
    eval_query_id: Mapped[int | None] = mapped_column(
        ForeignKey("eval_query.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)  # 一次下发一批
    runner: Mapped[str] = mapped_column(String(64), default="mac-01", server_default="mac-01", index=True)
    device_kind: Mapped[EvalDeviceKind] = mapped_column(
        Enum(EvalDeviceKind, length=8), default=EvalDeviceKind.web, server_default="web"
    )
    # 被测引擎(namiwork/codex/claude...);本阶段只实现 namiwork。留空兼容。
    target_engine: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[EvalRunStatus] = mapped_column(
        Enum(EvalRunStatus, length=16), default=EvalRunStatus.pending, server_default="pending", index=True
    )
    # 下发时的题面快照 JSON 字符串（prompt/attachments/dialog_options/conversation_group/turn_index）：
    # 执行器据此驱动对话，用"下发那一刻"的配置避免执行时 eval_query 被改导致漂移（学 exec_run.payload）。
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    # —— CLI 抓回的会话数据 ——
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # work.n.cn 会话 UUID
    share_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artifact_share_link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)  # 最终回答正文
    trace: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 会话全过程轨迹（见 spec §5.4）
    reported_duration: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 平台上报耗时（秒）
    bean_cost: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 算力豆变动
    tokens: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 本次 tokens（仅记录）
    # —— 大模型判定 ——
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)  # EvalVerdict 值；NULL=未判定
    verdict_dims: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: 三维结论（见 spec §5.5）
    verdict_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # 判定理由汇总
    judged_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # 判定用的引擎
    is_abnormal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", index=True)
    pushed_multica: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    multica_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)  # multica 侧任务 id/链接
    # —— 通用 ——
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # 执行失败/未完成原因
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 墙钟耗时
    enqueued_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
