import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.security import hash_password
from app.db.session import Base, engine, SessionLocal
from app.db.migrate import ensure_exec_run_kind, ensure_exec_run_report_columns, ensure_issue_columns, ensure_perf_indexes, ensure_perf_run_columns, ensure_project_columns, ensure_release_columns, ensure_task_columns, ensure_testcase_columns, migrate_task_status, ensure_ai_provider_columns, ensure_selector_tables, ensure_selector_page_column, ensure_selector_frame_width, ensure_probe_screenshot_column, ensure_api_env_table, ensure_eval_query_dimension, ensure_eval_run_target_engine, ensure_eval_run_payload, ensure_eval_run_target_device
from app.models import User  # noqa: F401  (触发模型注册)

logger = logging.getLogger("test_platform")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def init_db() -> None:
    """建表 + 种子平台管理员。P0 用 create_all；生产建议用 alembic 迁移。

    P3 集成层三张占位表（integration/api_token/integration_event）含 JSON 列，
    MySQL 5.6 不支持原生 JSON，且这三张表尚无任何业务代码——建表时排除，
    等真正实现 P3 时再单独处理（届时可把 JSON 降级为 Text 或升级 DB）。
    """
    _SKIP_TABLES = {"integration", "api_token", "integration_event"}
    tables = [t for t in Base.metadata.sorted_tables if t.name not in _SKIP_TABLES]
    Base.metadata.create_all(bind=engine, tables=tables)
    ensure_task_columns()
    ensure_testcase_columns()
    migrate_task_status()
    ensure_issue_columns()
    ensure_exec_run_kind()
    ensure_exec_run_report_columns()
    ensure_release_columns()
    ensure_project_columns()
    ensure_ai_provider_columns()
    ensure_selector_tables()
    ensure_selector_page_column()
    ensure_selector_frame_width()
    ensure_probe_screenshot_column()
    ensure_api_env_table()
    ensure_perf_indexes()
    ensure_perf_run_columns()
    ensure_eval_query_dimension()
    ensure_eval_run_target_engine()
    ensure_eval_run_payload()
    ensure_eval_run_target_device()
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
        # 反馈用例归属的固定专用项目（幂等，机器人 ingest 无需知道 project_id）
        from app.models import Project
        fb = db.query(Project).filter_by(code=settings.FEEDBACK_PROJECT_CODE).first()
        if not fb:
            db.add(Project(
                name=settings.FEEDBACK_PROJECT_NAME,
                code=settings.FEEDBACK_PROJECT_CODE,
                description="机器人反馈用例自动导入的专用项目",
            ))
            db.commit()
            logger.info("已创建反馈测试专用项目: %s", settings.FEEDBACK_PROJECT_CODE)
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
        # 反馈定时回归调度器（APScheduler）：建表后启动 + 从 DB enabled 集重建 job
        try:
            from app.services.scheduler import start_scheduler
            start_scheduler()
        except Exception:
            logger.exception("启动反馈定时调度器失败（不影响主服务）")

    @app.on_event("shutdown")
    def _shutdown():
        try:
            from app.services.scheduler import shutdown_scheduler
            shutdown_scheduler()
        except Exception:
            logger.exception("关闭反馈定时调度器失败")

    _mount_uploads(app)
    _mount_frontend(app)
    return app


def _mount_uploads(app: FastAPI) -> None:
    """托管运行时上传文件（探测截图 uploads/probes/<id>.png 等）。

    与 /assets 同款静态托管，但目录是运行时数据（不入 git，见 .gitignore）。目录不存在
    先建，避免 StaticFiles 因缺目录启动报错。必须在 SPA catch-all 之前挂载
    （create_app 里先于 _mount_frontend 调用），否则 /uploads/* 会被前端路由吞掉。
    """
    uploads_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads",
    )
    os.makedirs(uploads_dir, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")


def _mount_frontend(app: FastAPI) -> None:
    """托管前端构建产物（frontend/dist）。

    前端在开发机用 `npm run build` 生成 dist 并提交入库；服务器无需 Node，
    uvicorn 单进程即可同源服务页面(`/`)与接口(`/api`)。dist 不存在时静默跳过
    （纯本地后端开发仍可用 vite dev + 代理）。
    """
    # app/main.py -> app -> backend -> 仓库根 -> frontend/dist
    dist_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "frontend", "dist",
    )
    index_file = os.path.join(dist_dir, "index.html")
    if not os.path.isfile(index_file):
        logger.warning("未找到前端产物 %s，跳过静态托管（请在开发机 npm run build）", index_file)
        return

    # /assets 等带哈希的静态资源交给 StaticFiles
    app.mount("/assets", StaticFiles(directory=os.path.join(dist_dir, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def _index():
        return FileResponse(index_file)

    # SPA 回退：非 /api、非已知静态文件的路径都返回 index.html，交给前端路由。
    # 防目录穿越：先 realpath 解析（含符号链接），再确认仍在 dist 内，
    # 否则 ../ 或软链可越权读任意文件（如 .env、/etc/passwd）。
    dist_real = os.path.realpath(dist_dir)

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        candidate = os.path.realpath(os.path.join(dist_real, full_path))
        if (
            full_path
            and (candidate == dist_real or candidate.startswith(dist_real + os.sep))
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(index_file)


app = create_app()
