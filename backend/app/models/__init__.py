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
from app.models.runner_device import RunnerDevice
from app.models.release import ReleaseRecord
from app.models.selector import SelectorKey, SelectorScope, ProbeRequest, SelectorLearned
from app.models.api_env import ApiEnv
from app.models.perf import PerfRun
from app.models.perf_report_set import PerfReportSet
from app.models.ai_eval import EvalQuery, EvalRun, EvalClientDevice, EvalTask
from app.models.feedback import (
    FeedbackImport, FeedbackCase, FeedbackRegressionSet, FeedbackSetCase, FeedbackRun,
)
from app.models.release_checklist import ReleaseChecklistItem
from app.models.test_plan import TestPlan, TestPlanCase, TestPlanRun
from app.models.requirement import Requirement

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
    "RunnerDevice",
    "ReleaseRecord",
    "SelectorKey",
    "SelectorScope",
    "ProbeRequest",
    "SelectorLearned",
    "ApiEnv",
    "PerfRun",
    "PerfReportSet",
    "EvalQuery",
    "EvalRun",
    "EvalClientDevice",
    "EvalTask",
    "FeedbackImport",
    "FeedbackCase",
    "FeedbackRegressionSet",
    "FeedbackSetCase",
    "FeedbackRun",
    "ReleaseChecklistItem",
    "TestPlan",
    "TestPlanCase",
    "TestPlanRun",
    "Requirement",
]
