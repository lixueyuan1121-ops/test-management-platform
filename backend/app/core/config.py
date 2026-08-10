from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "sqlite:///./test_platform.db"

    JWT_SECRET: str = "please-change-this-secret-in-production"
    JWT_ALG: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:8080"

    SEED_ADMIN_USERNAME: str = "admin"
    SEED_ADMIN_PASSWORD: str = "admin123"
    SEED_ADMIN_NAME: str = "平台管理员"

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


settings = Settings()
