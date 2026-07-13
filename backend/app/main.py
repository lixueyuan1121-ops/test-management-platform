import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.security import hash_password
from app.db.session import Base, engine, SessionLocal
from app.db.migrate import ensure_task_columns
from app.models import User  # noqa: F401  (触发模型注册)

logger = logging.getLogger("test_platform")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def init_db() -> None:
    """建表 + 种子平台管理员。P0 用 create_all；生产建议用 alembic 迁移。"""
    Base.metadata.create_all(bind=engine)
    ensure_task_columns()
    db = SessionLocal()
    try:
        admin = db.query(User).filter_by(username=settings.SEED_ADMIN_USERNAME).first()
        if not admin:
            admin = User(
                username=settings.SEED_ADMIN_USERNAME,
                password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                name=settings.SEED_ADMIN_NAME,
                is_platform_admin=True,
            )
            db.add(admin)
            db.commit()
            logger.info("已创建种子管理员: %s", settings.SEED_ADMIN_USERNAME)
    finally:
        db.close()


def create_app() -> FastAPI:
    app = FastAPI(title="测试管理平台 API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/api/health", tags=["meta"])
    def health():
        return {"code": 0, "msg": "ok", "data": {"status": "up", "version": "0.1.0"}}

    @app.on_event("startup")
    def _startup():
        init_db()

    return app


app = create_app()
