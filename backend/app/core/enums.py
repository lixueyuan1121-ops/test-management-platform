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


# 所有项目级角色的集合，便于权限校验
ALL_PROJECT_ROLES = {ProjectRole.admin, ProjectRole.member, ProjectRole.guest}
WRITE_ROLES = {ProjectRole.admin, ProjectRole.member}  # guest 不可写
