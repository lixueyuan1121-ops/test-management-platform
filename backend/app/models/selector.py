from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint, Index
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SelectorKey(Base):
    """语义选择器注册表（单一事实源）。

    按 (project_id, sub_product) 分域，每个 key 记一条候选定位策略（candidates
    存 JSON 字符串，兼容 MySQL 5.6 无原生 JSON）。frame 指明该 key 归属外层壳
    还是内嵌 iframe（auto/shell/iframe）。runner/生成侧据此确定性定位元素。
    """

    __tablename__ = "selector_key"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", "key", name="uq_selkey_scope_key"),
        Index("idx_selkey_scope", "project_id", "sub_product"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    # platform: web(PC端) / android / ios。默认 web 保持存量数据语义不变。
    platform: Mapped[str] = mapped_column(String(16), default="web", server_default="web", index=True)
    key: Mapped[str] = mapped_column(String(64))
    frame: Mapped[str] = mapped_column(String(128), default="auto", server_default="auto")
    page: Mapped[str] = mapped_column(String(64), default="", server_default="")
    desc: Mapped[str] = mapped_column(String(255), default="", server_default="")
    candidates: Mapped[str] = mapped_column(Text, default="[]")  # JSON 字符串
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SelectorScope(Base):
    """每个 (project_id, sub_product) 域的 vm_iframe 配置（内嵌被测页的 iframe 定位）。"""

    __tablename__ = "selector_scope"
    __table_args__ = (
        UniqueConstraint("project_id", "sub_product", name="uq_selscope"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    vm_iframe: Mapped[str] = mapped_column(String(255), default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SelectorLearned(Base):
    """运行时自学习候选评审队列(self-healing 上报落点)。

    runner 全候选失败→按 key 语义找回元素→铸造候选执行成功后上报到此。上报即
    把最优候选(带 src:"learned" 标)追加到 selector_key.candidates 尾部试用——
    下次同 key 失败可直接命中;人工「转正」去掉试用标、「拒绝」从注册表移除且
    同候选再上报不再入表(status=rejected 挡重复)。candidate/evidence 存 JSON
    字符串(兼容 MySQL 5.6)。同 (scope,key,by,value) 去重,重复上报只 hit_count+1。
    """

    __tablename__ = "selector_learned"
    __table_args__ = (
        Index("idx_sellearn_scope", "project_id", "sub_product"),
        Index("idx_sellearn_status", "status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    key: Mapped[str] = mapped_column(String(64))
    cand_by: Mapped[str] = mapped_column(String(16))
    cand_value: Mapped[str] = mapped_column(String(255))
    candidate: Mapped[str] = mapped_column(Text, default="{}")   # 完整候选 JSON(含 name/src)
    all_candidates: Mapped[str] = mapped_column(Text, default="[]")  # 本次铸造的全部候选(评审参考)
    evidence: Mapped[str] = mapped_column(Text, default="{}")    # {matched,text,tag,score,frame_url}
    runner: Mapped[str] = mapped_column(String(64), default="", server_default="")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 来源 exec_run(软关联)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")  # pending/approved/rejected
    hit_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    reviewed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProbeRequest(Base):
    """设备探测请求：平台侧下发一次探测，runner 拉取执行并回写 result/error。

    params/result 用 TEXT 存 JSON 字符串（兼容 MySQL 5.6 无原生 JSON）。
    status: pending → running → done/failed。runner 列标识目标执行机。
    """

    __tablename__ = "probe_request"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    sub_product: Mapped[str] = mapped_column(String(32), default="", server_default="")
    runner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", index=True)
    params: Mapped[str] = mapped_column(Text, default="{}")
    # result 存整页探测结果 JSON（groups×elements×candidates）。真实复杂页面单帧可达数百元素，
    # JSON 常 200KB+，远超 MySQL TEXT 的 64KB 上限（会截断成坏 JSON）→ MySQL 用 LONGTEXT；
    # SQLite 的 TEXT 无长度限制，variant 只对 mysql 生效。老库由 migrate.ensure_probe_result_longtext 放宽。
    result: Mapped[str | None] = mapped_column(Text().with_variant(LONGTEXT, "mysql"), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
