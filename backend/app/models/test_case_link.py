"""用例↔用例关联(前置)表:把「前置用例」挂到「主用例」上,执行主用例时先按序跑前置。

区别于 test_case.precondition(自由文本,描述本用例内部起点),本表是 case→case 的结构化链接。
执行时由 exec-queue 入队侧展开:对每个下发的主用例,先按 sort_order 插入其前置用例的 ExecRun、
再插主用例,因入队顺序即执行顺序(list_pending 按 ExecRun.id 升序),故前置自然先跑。
test_case 删除时两侧外键 CASCADE 自动清理。
"""
from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TestCaseLink(Base):
    __tablename__ = "test_case_link"
    __table_args__ = (
        UniqueConstraint("case_id", "prereq_case_id", name="uq_test_case_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 主用例:执行它时要先跑下面的前置。
    case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), index=True
    )
    # 前置用例:在主用例之前按 sort_order 依次执行。
    prereq_case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", ondelete="CASCADE"), index=True
    )
    # 同一主用例挂多个前置时的先后(小的先跑)。
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
