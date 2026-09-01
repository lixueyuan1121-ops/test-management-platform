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
    AI_TIMEOUT_SECONDS: int = 900  # 单次生成硬超时=15 分钟(放开到最多 100 条用例,产出大、耗时长;配合 SSE 心跳防网关空闲切断)
    AI_MAX_CONCURRENCY: int = 2    # 全局引擎并发上限(信号量)——控成本/机器负载;超限改为排队等待(非拒绝)
    # 超限时最多排队等待多久拿槽(秒);超时才报「繁忙」。0=不等(旧「立即拒绝」行为)。
    AI_ACQUIRE_TIMEOUT_SECONDS: int = 600
    # AI 任务队列(方案2)worker 池线程数=并发上限。多余任务排队而非拒绝。claude 每任务 fork 子进程,勿过大。
    AI_WORKER_CONCURRENCY: int = 2
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

    # ---- 飞书通知（群自定义机器人 webhook 推卡片）----
    # 与上面的 OpenAPI 取文完全独立：取文是「读文档」（需 app 凭据），本通道是「发消息」
    # （只需群机器人 webhook URL）。未配 URL 即整条通道静默关闭，不影响任何业务流程。
    FEISHU_WEBHOOK_URL: str = ""
    # 群机器人若开了「签名校验」，把密钥填这里（留空表示机器人未开签名）。
    FEISHU_WEBHOOK_SECRET: str = ""
    # 卡片按钮回跳平台的基址（如 http://10.0.0.5:4173）。留空则卡片不带跳转按钮。
    PLATFORM_BASE_URL: str = ""
    # 各告警场景开关（通道总开关是 FEISHU_WEBHOOK_URL，这里做细粒度静音）。
    NOTIFY_EXEC_FAIL: bool = True       # 自动回归批次出现失败
    NOTIFY_TASK_ASSIGN: bool = False    # 任务指派到人（按产品要求：任务分配不走推推推送，默认关）
    NOTIFY_REPORT_MISSING: bool = True  # 日报缺交提醒
    NOTIFY_EVAL_PIPELINE: bool = True    # 测评任务一条龙(执行完自动判定+评价)分步通知
    NOTIFY_PLAN_RESULT: bool = True      # 测试计划执行完毕结果回执(手动+定时、成败都发)
    # 【已废弃/保留兼容】一条龙曾用此指定判定/评价引擎;现一条龙固定走平台默认引擎(claude),
    # 与手动批量判定/手动综合评价完全一致——避免自动与手动用不同引擎导致分数系统性出入。
    # 仍读入以兼容旧 .env(不再影响行为)。
    EVAL_PIPELINE_PROVIDER: str = ""
    # 推推(TuiTui)机器人通知：一条龙分步通知走这里。appid+secret URL 鉴权，发到 togroups。
    TUITUI_BOT_APPID: str = ""
    TUITUI_BOT_SECRET: str = ""   # 敏感：只填进 .env
    TUITUI_BOT_GROUP: str = ""    # 目标群 id
    TUITUI_BASE_URL: str = "https://alarm.im.qihoo.net"  # 外网发送改 https://im.live.360.cn:8282/robot
    # ---- Nami 静态部署(综合评价 HTML → 公网短链)----
    # 一条龙生成综合评价后,把 HTML 部署到 n.cn 网关换公网短链(zhaomi.cn),经推推推给人。
    # 依赖 nami cookie(与 skill nami-static-deploy 同源);两路径留空则用 skill 默认位置
    # (~/.openclaw/workspace/config/{.cookie.json,cloud_config.json})。cookie 缺失/过期时
    # 自动回落到平台自托管短链 /r/<code>(见 eval_pipeline),不阻断一条龙。
    NAMI_DEPLOY_ENABLED: bool = True
    NAMI_COOKIE_PATH: str = ""         # nami cookie json 路径;空=skill 默认
    NAMI_CLOUD_CONFIG_PATH: str = ""   # cloud_config.json(含 vm_id)路径;空=skill 默认
    # auto/ci 批次 business 失败自动生成 RemainingIssue 草稿（与飞书通道独立，false 关闭）。
    AUTO_ISSUE_ON_FAIL: bool = True
    # auto/ci 批次失败自动重试次数上限（0=关闭；1=失败补发一次，重试通过标 flaky）。
    EXEC_AUTO_RETRY: int = 1
    # 日报缺交提醒的每日触发时刻（24 小时制 HH:MM，Asia/Shanghai）。留空则不建该定时 job。
    REPORT_REMIND_AT: str = ""

    # multica(异常会话详细分析平台)对接。契约待细化,默认 off 不推。
    MULTICA_MODE: str = "off"          # off / http / cli
    MULTICA_URL: str = ""              # http 模式:创建分析任务的 endpoint
    MULTICA_TOKEN: str = ""            # http 模式:Bearer token(如需)
    MULTICA_CLI_TEMPLATE: str = ""     # cli 模式:命令模板,如 'multica push --link {share_link} --run {run_id}'

    # ---- 反馈测试模块（机器人推 md/zip 对接）----
    # 机器人无人值守，用独立长期 token 鉴权（与用户 JWT 分离，仿 RUNNER_TOKEN）。空则拒绝一切 ingest。
    FEEDBACK_BOT_TOKEN: str = ""    # 反馈用例归属的固定专用项目（startup 自动 ensure，机器人侧无需知道 project_id）。
    FEEDBACK_PROJECT_CODE: str = "__feedback__"
    FEEDBACK_PROJECT_NAME: str = "反馈测试"
    # 反馈用例补 script / 下发执行时借用的「被测产品」选择器来源项目 code（反馈项目自己不建选择器）。
    # 空则回退用反馈项目自身（通常无选择器）。生产应指向纳米Work功能测试项目的 code。
    FEEDBACK_SELECTOR_PROJECT_CODE: str = ""

    # ---- CI/CD 集成钩子（流水线触发测试计划 + 质量门禁查询）----
    # CI 无人值守，用独立长期 token 鉴权（仿 RUNNER_TOKEN/FEEDBACK_BOT_TOKEN 模式）。
    # 空则拒绝一切 /api/hooks/* 请求（通道默认关闭）。
    CI_HOOK_TOKEN: str = ""

    # ---- 极库云(geelib)缺陷上报（把遗留问题/回归失败推成极库云工作项「缺陷」）----
    # 鉴权走 qihoo-sso-cli 取 app_token（与 sso-geelib-project-skill 同源），无人值守纯 HTTP，
    # 不依赖 Node/skill。未开或缺 sub_id 映射即整条通道静默关闭，不影响任何业务流程。
    GEELIB_ENABLED: bool = False
    GEELIB_API_URL: str = "http://geelib.agent-auth.qihoo.net"
    GEELIB_SSO_BIN: str = ""            # 空则运行时 shutil.which("qihoo-sso-cli")
    GEELIB_SSO_APP: str = "geelib"      # qihoo-sso-cli -app 值
    GEELIB_SSO_TOOL: str = "sso-geelib-project-skill"  # qihoo-sso-cli -tool 值（授权登记用）
    GEELIB_DEFECT_TYPE: str = "缺陷"     # /openapi/Matter/add 的 type_id（工作项类型名）
    # 平台项目→极库云项目(sub_id)的映射，形如 "nw:419,other:512"。也可在 Project.geelib_sub_id 单配。
    GEELIB_SUB_MAP: str = ""
    # auto/ci 批次失败自动上报开关（默认关：先建本地草稿供人复核，人确认后再上报，避免误报污染极库云）。
    GEELIB_AUTO_REPORT: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def geelib_sub_map(self) -> dict[str, int]:
        """解析 GEELIB_SUB_MAP("code:sub_id,code2:sub_id2") → {code: sub_id}。非法项跳过。"""
        out: dict[str, int] = {}
        for pair in (self.GEELIB_SUB_MAP or "").split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            code, _, sid = pair.partition(":")
            code, sid = code.strip(), sid.strip()
            if code and sid.isdigit():
                out[code] = int(sid)
        return out

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
