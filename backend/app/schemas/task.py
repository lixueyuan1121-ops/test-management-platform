from datetime import date, datetime
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.enums import TaskPriority, TaskStatus


MAX_FEATURE_HTML = 12 * 1024 * 1024


class _DocumentCheck(HTMLParser):
    found = False

    def handle_starttag(self, tag, attrs):
        if tag in ('html', 'body', 'main', 'article'):
            self.found = True


class FeatureHtmlUpload(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=MAX_FEATURE_HTML)

    @field_validator('name')
    @classmethod
    def html_filename(cls, value):
        if (not value.lower().endswith(('.html', '.htm'))
                or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in value)):
            raise ValueError('请选择本地 .html 或 .htm 文件')
        return value

    @field_validator('content')
    @classmethod
    def html_document(cls, value):
        if len(value.encode('utf-8')) > MAX_FEATURE_HTML or '\0' in value:
            raise ValueError('功能说明须为 12 MiB 以内的 UTF-8 HTML 文件')
        parser = _DocumentCheck()
        try:
            parser.feed(value)
            parser.close()
        except (AssertionError, ValueError):
            raise ValueError('HTML 文件格式无效') from None
        if not parser.found:
            raise ValueError('文件缺少 HTML 文档正文')
        return value


class TaskLedgerFields(BaseModel):
    feature_description: str | None = Field(default=None, max_length=10000)
    risk_level: Literal['green', 'yellow', 'red'] | None = None
    target_version: str | None = Field(default=None, max_length=64)
    progress_note: str | None = Field(default=None, max_length=10000)
    expected_completion: date | None = None
    next_action: str | None = Field(default=None, max_length=10000)
    action_owner: str | None = Field(default=None, max_length=128)
    feedback_due_at: datetime | None = None
    affects_release: bool | None = None
    conclusion: str | None = Field(default=None, max_length=10000)

    @field_validator('feature_description', 'target_version', 'progress_note', 'next_action', 'action_owner', 'conclusion', mode='before')
    @classmethod
    def trim_optional_text(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class TaskCreate(TaskLedgerFields):
    feature_html: FeatureHtmlUpload | None = None
    project_id: int
    title: str = Field(min_length=1, max_length=20000)
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: TaskPriority = TaskPriority.p2
    assigned_to: int | None = Field(default=None, gt=0)
    assigned_to_ids: list[int] | None = Field(default=None, max_length=100)
    assigned_date: date


class TaskUpdate(TaskLedgerFields):
    feature_html: FeatureHtmlUpload | None = None
    title: str | None = Field(default=None, min_length=1, max_length=20000)
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: TaskPriority | None = None
    assigned_to: int | None = Field(default=None, gt=0)
    assigned_to_ids: list[int] | None = Field(default=None, max_length=100)
    assigned_date: date | None = None
    status: TaskStatus | None = None
    expected_status: TaskStatus | None = None  # 用户打开编辑器/状态菜单时看到的状态
    close_note: str | None = None   # 关闭任务时的备注（通常与 status='closed' 一起提交）


class TaskOut(TaskLedgerFields):
    feature_html_name: str | None = None
    id: int
    project_id: int
    assigned_by: int
    assigned_by_name: str
    assigned_to: int | None
    assigned_to_name: str
    assigned_to_ids: list[int] = Field(default_factory=list)
    title: str
    description: str | None = None
    module: str | None = None
    requirement_url: str | None = None
    developer: str | None = None
    priority: str
    assigned_date: date
    status: str
    online_at: datetime | None = None
    closed_at: datetime | None = None
    close_note: str | None = None
    release_record_id: int | None = None
    created_at: datetime

    source_group_id: str | None = None
    source_message_id: str | None = None
    is_ingested: bool = False

    class Config:
        from_attributes = True


class TaskIngest(BaseModel):
    """转发机器人提交已收集的提测信息；不接受负责人或工作流状态。"""
    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    project_id: int = Field(gt=0)
    source_group_id: Literal['7652669649127481'] = '7652669649127481'
    source_message_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=20000)
    description: str = Field(min_length=1, max_length=20000)
    module: str | None = Field(default=None, max_length=128)
    requirement_url: str | None = Field(default=None, max_length=512)
    developer: str | None = Field(default=None, max_length=64)
    priority: TaskPriority = TaskPriority.p2
    assigned_date: date | None = None
    target_version: str | None = Field(default=None, max_length=64)
    expected_completion: date | None = None

    @field_validator('requirement_url')
    @classmethod
    def validate_requirement_url(cls, value):
        from urllib.parse import urlsplit
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('需求地址必须为 http/https 链接')
        return value
