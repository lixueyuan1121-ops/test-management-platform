from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 数据库：两种配法，二选一。
    # 1) 分字段（推荐用于 MySQL）：设 DB_HOST 即启用，密码含特殊字符也无需手动编码。
    # 2) 整串：DATABASE_URL（默认 SQLite，开箱即用）。仅当未设 DB_HOST 时生效。
    DATABASE_URL: str = "sqlite:///./test_platform.db"
    DB_HOST: str = ""
    DB_PORT: int = 3306
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_NAME: str = ""
    DB_CHARSET: str = "utf8mb4"

    JWT_SECRET: str = "please-change-this-secret-in-production"
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8080"

    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "admin123"
    SEED_ADMIN_NAME: str = "平台管理员"

    # ---- 本地执行 runner（勾选用例下发到目标机执行）----
    # runner 是无人值守进程，用单独长期 token 鉴权（与用户 JWT 分离）；
    # runner 端 .env 的 RUNNER_TOKEN 须填相同值。空则拒绝一切 runner 请求。
    RUNNER_TOKEN: str = ""

    # ---- QA Copilot（AI 生成测试点，subprocess 调 claude CLI）----
    AI_ENABLED: bool = True
    CLAUDE_BIN: str = ""            # 空则运行时 shutil.which("claude")
    AI_MODEL: str = ""             # 空则用 claude CLI 默认模型
    AI_TIMEOUT_SECONDS: int = 240  # 单次生成硬超时
    AI_MAX_CONCURRENCY: int = 2    # 全局并发上限（控成本，超出即拒绝）

    # ---- 飞书 OpenAPI（读取需求文档 docx/wiki/sheets/base）----
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_BASE: str = "https://open.feishu.cn"  # 私有化/国际站可改

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """最终连接串。设了 DB_HOST 则由分字段拼 MySQL（密码自动 URL 编码，
        兼容 # ^ ! * 等特殊字符）；否则回落到 DATABASE_URL。"""
        if self.DB_HOST:
            user = quote(self.DB_USER, safe="")
            pwd = quote(self.DB_PASSWORD, safe="")
            return (
                f"mysql+pymysql://{user}:{pwd}@{self.DB_HOST}:{self.DB_PORT}"
                f"/{self.DB_NAME}?charset={self.DB_CHARSET}"
            )
        return self.DATABASE_URL


settings = Settings()
