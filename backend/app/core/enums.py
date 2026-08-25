"""共享枚举：角色与状态。

放这里而不是 models 里，是为了让 api/deps 层能直接引用，避免循环导入。
"""
import enum


class ProjectRole(str, enum.Enum):
    """项目级角色"""
    admin = "admin"      # 项目管理员：分配任务、看本项目统计、管本项目成员
    member = "member"    # 成员：接收任务、提交日报
    guest = "guest"      # 嘉宾：纯只读


class UserStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"


class ProjectStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class TaskStatus(str, enum.Enum):
    pending = "pending"    # 待测：已派单，尚未开始
    testing = "testing"    # 测试中：正在执行
    blocked = "blocked"    # 阻塞：卡住（环境/缺陷/依赖）
    online = "online"      # 已上线：测完通过、已上线
    closed = "closed"      # 已关闭：不再跟进/取消/合并


class TaskPriority(str, enum.Enum):
    p0 = "p0"
    p1 = "p1"
    p2 = "p2"
    p3 = "p3"


class IssueSeverity(str, enum.Enum):
    blocker = "blocker"
    major = "major"
    minor = "minor"


class IssueStatus(str, enum.Enum):
    open = "open"
    resolved = "resolved"


class IntegrationEventStatus(str, enum.Enum):
    received = "received"
    processed = "processed"
    failed = "failed"


class ToolStatus(str, enum.Enum):
    online = "online"
    offline = "offline"


class AiTaskStatus(str, enum.Enum):
    """一次 AI 生成任务的生命周期。"""
    running = "running"    # 生成中（subprocess 执行 claude）
    done = "done"          # 完成并落库
    failed = "failed"      # 失败（超时/CLI 错误/无输出）


class AiInputType(str, enum.Enum):
    """AI 生成的输入来源。"""
    text = "text"    # 手动粘贴需求文本
    url = "url"      # 任务需求地址
    file = "file"    # 上传需求文档


class ReviewStatus(str, enum.Enum):
    """AI 测试点的三态评审状态（供「AI 战绩墙」统计采纳率）。"""
    pending = "pending"    # 未评审
    adopted = "adopted"    # 已采纳
    rejected = "rejected"  # 已否决


class ChecklistStatus(str, enum.Enum):
    """验收清单项的执行状态（成员逐条勾选）。"""
    pending = "pending"    # 待执行
    passed = "passed"      # 通过
    failed = "failed"      # 失败
    blocked = "blocked"    # 阻塞


class ExecKind(str, enum.Enum):
    """自动化执行类型（下发给 runner 时决定 Claude Code 怎么跑）。"""
    gui = "gui"        # GUI 用例：gui-mcp 操作被测客户端 DOM
    api = "api"        # 接口用例：curl / fetch 验证接口与响应
    cli = "cli"        # 命令行用例：起进程校验退出码 / 输出
    e2e = "e2e"        # 端到端：多步 + 等待策略（gui 工具为主，比单点 gui 长/慢）
    manual = "manual"  # 不可自动化：纯人工/探索性/主观体验；平台不派发到执行机


class ExecStatus(str, enum.Enum):
    """执行队列项（exec_run）的生命周期。

    runner 回写用 pass/fail（见 runner.mjs 契约），平台侧映射到 passed/failed；
    fail_kind=selector（选择器/环境阻塞）→ 映射到 blocked（不计入功能失败率，见 L2）。
    """
    pending = "pending"    # 待执行（已入队，等 runner 拉取）
    running = "running"    # 执行中（runner 已 claim）
    passed = "passed"      # 通过（verdict=pass）
    failed = "failed"      # 失败（verdict=fail 且 fail_kind≠selector：真功能 bug）
    blocked = "blocked"    # 阻塞（选择器/环境问题：定位失败/复位失败/掉登录，非功能失败）


class EvalRunStatus(str, enum.Enum):
    """一次对话测评执行 + 判定的生命周期。"""
    pending = "pending"    # 已下发，等执行机拉取
    running = "running"    # 执行机已认领、对话进行中
    done = "done"          # 对话+轨迹抓取完成（尚未判定）
    judging = "judging"    # 轨迹已回传，大模型判定中
    judged = "judged"      # 判定完成（终态）
    failed = "failed"      # 执行失败（对话没跑起来/抓取失败；区别于“判定不通过”）


class EvalDeviceKind(str, enum.Enum):
    """执行载体（对齐 ai-eval-cli 的三种运行形态）。"""
    web = "web"            # Web 多账号（ContextPool，注入 storageState 登录态）
    desktop = "desktop"    # 桌面客户端（CDP 连 Electron 单客户端多对话）
    cli = "cli"            # 命令行执行（具体形态见子项 2；先占位）


class EvalVerdict(str, enum.Enum):
    """大模型对一次会话的总判定。"""
    passed = "pass"        # 三维皆过（passed 规避 Python 保留字 pass，值仍为 "pass"）
    failed = "fail"        # 有维度不过
    error = "error"        # 判定本身出错（轨迹缺失/判定引擎异常）


class FeedbackImportStatus(str, enum.Enum):
    """一次机器人推送批次(md/zip)的解析生命周期。"""
    parsing = "parsing"    # 解析中(含后台补 script)
    done = "done"          # 解析完成并落库
    failed = "failed"      # 整批失败(如非法文件)


class FeedbackCaseStatus(str, enum.Enum):
    """反馈用例的就绪状态。"""
    draft = "draft"        # 刚解析(尚未补 script / manual 未定稿)
    ready = "ready"        # script 就绪或 manual 定稿,可下发


# 所有项目级角色的集合，便于权限校验
ALL_PROJECT_ROLES = {ProjectRole.admin, ProjectRole.member, ProjectRole.guest}
WRITE_ROLES = {ProjectRole.admin, ProjectRole.member}  # guest 不可写
