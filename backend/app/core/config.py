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
    # 连接回收秒数（仅 MySQL 等连接池生效）：存活超此值的连接下次使用前被换新，
    # 避免 MySQL wait_timeout / 中间层空闲断连导致 `2013 Lost connection`。
    # 取值须小于服务端 wait_timeout 与 LB/代理空闲超时。默认 280s（<常见 300s 阈值）。
    DB_POOL_RECYCLE: int = 280

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
    AI_TIMEOUT_SECONDS: int = 600  # 单次生成硬超时(P3 后 prompt 大、需产 script;配合 SSE 心跳防网关空闲切断)
    AI_MAX_CONCURRENCY: int = 2    # 全局并发上限（控成本，超出即拒绝）
    # gui/e2e 用例生成时注入的语义选择器注册表路径（runner 侧 gui-mcp/selectors.json）。
    # 空则用默认：相对本仓库 tools/qalab-runner/gui-mcp/selectors.json。让 AI 只用库内 key 写 script。
    SELECTORS_PATH: str = ""

    # ---- DeepSeek 引擎（QA Copilot 多引擎之一，OpenAI 兼容端点直调）----
    # 与 claude 引擎并列，可在生成入口切换。目标只是"用 DeepSeek 生成测试点文本"，
    # 端点是标准 OpenAI 兼容接口（内网 360 网关 / 官方 / 自建），后端用 requests 直接
    # POST /chat/completions，零平台依赖、无额外安装。不配置时前端置灰，claude 照常。
    # 注：部分网关（如 360）要求非空 API_KEY，网关不校验时也需给占位值。
    DEEPSEEK_ENABLED: bool = False
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = ""            # OpenAI 兼容端点，如 http://host/v1；留空走官方 deepseek.com
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_MAX_TOKENS: int = 49152       # 推理模型 reasoning 占用多，过小会在正文产出前被截断（POC 实测）

    # ---- 飞书 OpenAPI（读取需求文档 docx/wiki/sheets/base）----
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_BASE: str = "https://open.feishu.cn"  # 私有化/国际站可改

    # multica(异常会话详细分析平台)对接。契约待细化,默认 off 不推。
    MULTICA_MODE: str = "off"          # off / http / cli
    MULTICA_URL: str = ""              # http 模式:创建分析任务的 endpoint
    MULTICA_TOKEN: str = ""            # http 模式:Bearer token(如需)
    MULTICA_CLI_TEMPLATE: str = ""     # cli 模式:命令模板,如 'multica push --link {share_link} --run {run_id}'

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
