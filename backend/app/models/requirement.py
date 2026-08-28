"""需求实体（Requirement）——需求↔用例↔发版的追溯链落点（建议项⑥）。

此前需求只是 Task.requirement_url 一个文本和 ReleaseRecord.req_count 一个数字，
答不出「本版需求覆盖了没、哪条没测」。本表把需求立成轻量实体：

- 对标 Xray 的 requirement↔test「covers」链路与覆盖状态(UNCOVERED/NOTRUN/NOK/OK)、
  MeterSphere 的需求关联用例。
- 本仓库独有的自动挂链：AI 生成用例时本来就在读需求文档(extract-url/飞书取文)，
  生成那一刻按 (project_id, url) 幂等 upsert 本表并给该批用例打 requirement_id——
  覆盖率自动长出来，不靠人工维护矩阵。
- release_id 可选挂版本 → /releases/quality 长出「需求覆盖」腿。

覆盖状态现算不建表（用例挂 test_case.requirement_id 软链，执行态经 exec_run 现查）。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Requirement(Base):
    __tablename__ = "requirement"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    # 需求文档链接(飞书 docx/wiki 等)。同项目同 url 视为同一需求(代码层幂等 upsert,
    # 不建 DB 唯一约束——url 可空且 512 长超 MySQL 索引键长限制)。
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # 所属发版(可空):挂上后该需求进对应版本质量卡的「需求覆盖」统计。
    release_id: Mapped[int | None] = mapped_column(
        ForeignKey("release_record.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
