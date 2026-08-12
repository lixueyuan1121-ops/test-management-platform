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


# 所有项目级角色的集合，便于权限校验
ALL_PROJECT_ROLES = {ProjectRole.admin, ProjectRole.member, ProjectRole.guest}
WRITE_ROLES = {ProjectRole.admin, ProjectRole.member}  # guest 不可写
