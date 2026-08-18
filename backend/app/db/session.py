from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import settings

# SQLite 需要这个参数以支持多线程（FastAPI 的线程池会用到）
_db_url = settings.sqlalchemy_url
_is_sqlite = _db_url.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# 连接池健壮性（仅 MySQL 等真实连接池有意义；SQLite 无池，参数不适用故不传）。
# - pool_pre_ping：取连接时先 ping，挡掉"已被服务器关闭的空闲连接"。
# - pool_recycle：主动回收存活超过 N 秒的连接，赶在 MySQL wait_timeout / 中间层(LB/代理)
#   空闲断连之前换新连接——这是根治 `2013 Lost connection during query` 的关键
#   （pre_ping 只在取连接时探活，挡不住"查询进行中被掐断"，需靠 recycle 提前换掉超龄连接）。
_engine_kwargs = {"connect_args": _connect_args, "echo": False}
if not _is_sqlite:
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_recycle"] = settings.DB_POOL_RECYCLE

engine = create_engine(_db_url, **_engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
