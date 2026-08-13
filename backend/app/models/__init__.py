"""导入所有模型，便于 Base.metadata.create_all 一次性建全表。"""
from app.models.user import User
from app.models.project import Project, Team, ProjectMember
from app.models.task import Task
from app.models.report import DailyReport
from app.models.issue import RemainingIssue
from app.models.integration import Integration, ApiToken, IntegrationEvent
from app.models.tool import ToolCategory, TestTool
from app.models.ai import AiTask, TestCase
from app.models.checklist import ChecklistItem
from app.models.exec_queue import ExecRun

__all__ = [
    "User",
    "Project",
    "Team",
    "ProjectMember",
    "Task",
    "DailyReport",
    "RemainingIssue",
    "Integration",
    "ApiToken",
    "IntegrationEvent",
    "ToolCategory",
    "TestTool",
    "AiTask",
    "TestCase",
    "ChecklistItem",
    "ExecRun",
]
