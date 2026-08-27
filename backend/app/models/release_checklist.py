"""上线 checklist 独立表：漏斗末端——从回归用例库勾选出的「待上线验证」用例集合。

漏斗：用例库(全部) → 回归用例库(is_regression) → 上线checklist(本表，待上线验证)。
每项目一份。引用 test_case（不复制）；移除本表行只是从上线清单移除，不影响回归用例/总用例。
test_case 删除时本表行 CASCADE 自动清理。执行走现有 exec-queue enqueue-cases。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ReleaseChecklistItem(Base):
    __tablename__ = "release_checklist_item"
    __table_args__ = (
        UniqueConstraint("project_id", "test_case_id", name="uq_release_checklist_proj_case"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("project.id", ondelete="CASCADE"), index=True
    )
    test_case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
