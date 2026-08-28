"""执行队列表 exec_run —— 勾选用例下发到目标机 → Claude Code 执行 → 回写的载体。

数据流（详见 tools/qalab-runner/HANDOFF.md）：
  前端勾选清单项 →POST /enqueue 入队(pending)
  → runner 轮询 GET /exec-queue 拉取 →POST claim(running)
  → 本地 Claude Code headless 按 kind 执行被测客户端
  →PATCH 回写 {verdict,reason,evidence}，平台同步 checklist_item.exec_status

设计要点：
- payload 用 Text 存 JSON 字符串（**不用原生 JSON 列**）——兼容生产 MySQL 5.6（见 MEMORY）。
- checklist_item_id 是回写落点：runner 判 pass/fail 后据此更新对应清单项的 exec_status，
  从而复用现有清单展示 / checklist-summary 统计 / 失败转遗留问题等全部下游能力。
- 启动时 Base.metadata.create_all 自动建表（新表，无需 migrate）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import ExecKind, ExecStatus
from app.db.session import Base


class ExecRun(Base):
    __tablename__ = "exec_run"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 回写落点：指向被执行的验收清单项（可空——允许无清单项的裸执行记录）
    checklist_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("checklist_item.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 快照来源与追溯（冗余，便于查询/展示，且清单项被删后仍留痕）
    test_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_case.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # 发版实体关联(可选):下发时显式挂到某次发版 → /releases/quality 优先按实体聚合
    # (无关联版本回落时间窗近似)。对标 TestRail milestone↔run / Xray execution↔fix version。
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_record.id", ondelete="SET NULL"), nullable=True, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    runner: Mapped[str] = mapped_column(String(64), default="mac-01", server_default="mac-01", index=True)
    kind: Mapped[ExecKind] = mapped_column(
        Enum(ExecKind, length=8), default=ExecKind.gui, server_default="gui"
    )
    status: Mapped[ExecStatus] = mapped_column(
        Enum(ExecStatus, length=16), default=ExecStatus.pending, server_default="pending", index=True
    )
    payload: Mapped[str] = mapped_column(Text)  # 用例快照 JSON 字符串（steps/expected/params）
    # 批次号：一次 enqueue 生成一个，该批所有 run 共享（供执行结果页按批次汇总）。老库补列后为 NULL=未分批。
    batch_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # ---- 失败自动重试链(L2.5):auto/ci 批次失败自动补发一次,重试通过=flaky ----
    # retry_of:本行是哪条 run 的重试(纯 int 软指,不建自引用 FK——MySQL5.6 自 FK 加列麻烦且无级联需求)。
    # attempt:第几次尝试(原始=1,重试=2);flaky:重试通过时置真(fail→pass 抖动,Azure DevOps 同语义)。
    # 统计口径:被重试覆盖的原始行(id 出现在他行 retry_of)不计入批次/门禁/质量卡,以链上最终结果为准。
    retry_of: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    flaky: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # AI 失败归因(建议项⑨,人工触发):kind 供筛选(selector/environment/assertion/bug),
    # triage 存完整 JSON {kind,confidence,reason,suggestion,provider,at}。归因是参考非裁决。
    triage_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    triage: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)  # runner 回写原值 pass/fail/blocked
    # 失败性质(L2):selector=选择器/环境阻塞(定位失败/复位失败/掉登录,不计功能失败率);
    # business=断言不通过(真功能 bug)。pass 或旧 runner 回写时为 NULL。老库由 migrate 补列。
    fail_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 逐步执行报告 JSON 字符串（每步 action/desc/ok/截图 URL/错误 + 结论）；gui/e2e 由 runner 回写。Text-JSON 兼容 MySQL5.6。
    report: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enqueued_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
