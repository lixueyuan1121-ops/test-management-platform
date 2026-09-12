from datetime import datetime
from sqlalchemy import Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.db.session import Base


class SelectorRevision(Base):
    __tablename__ = "selector_revision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_id: Mapped[int] = mapped_column(Integer, index=True)
    revision: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[str] = mapped_column(Text)
    changed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
