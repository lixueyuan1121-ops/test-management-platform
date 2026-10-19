"""人工派单通知：事务内入队、后台发送、定时重试。"""
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Project, Task, TaskAssignmentNotice, User
from app.services.notify import _link, send_tuitui_message

logger = logging.getLogger('test_platform')


def queue_assignment(db: Session, task: Task, actor: User) -> None:
    now = datetime.utcnow()
    # 负责人变更后，尚未发出的旧指派已过时。
    db.query(TaskAssignmentNotice).filter(
        TaskAssignmentNotice.task_id == task.id, TaskAssignmentNotice.sent_at.is_(None),
        TaskAssignmentNotice.cancelled_at.is_(None),
    ).update({'cancelled_at': now}, synchronize_session=False)
    if task.assigned_to is None:
        return
    project = db.get(Project, task.project_id)
    owner_names = "、".join(db.get(User, uid).name for uid in task.assigned_to_ids)
    lines = ['【测试任务派单】', f'任务：{task.title}', f'任务编号：#{task.id}',
             f'项目：{project.name}', f'测试负责人：{owner_names}', f'派单人：{actor.name}',
             f'测试日期：{task.assigned_date}', f'优先级：{task.priority.value.upper()}']
    for label, value in [('开发负责人', task.developer), ('模块', task.module),
                         ('归属版本', task.target_version), ('预计完成', task.expected_completion),
                         ('需求文档', task.requirement_url), ('提测内容', task.description)]:
        if value:
            lines.append(f'{label}：{value}')
    link = _link(f'/tasks?project_id={task.project_id}&task_id={task.id}')
    if link:
        lines.append(f'查看任务：{link}')
    db.add(TaskAssignmentNotice(task_id=task.id, revision=task.assignment_revision,
                               group_id=settings.TUITUI_TASK_ASSIGN_GROUP,
                               content='\n'.join(lines), next_attempt_at=now))


def dispatch_assignment_notices(task_id: int | None = None) -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        query = db.query(TaskAssignmentNotice.id).filter(
            TaskAssignmentNotice.sent_at.is_(None), TaskAssignmentNotice.cancelled_at.is_(None),
            TaskAssignmentNotice.next_attempt_at <= now,
        )
        if task_id is not None:
            query = query.filter(TaskAssignmentNotice.task_id == task_id)
        ids = [row.id for row in query.order_by(TaskAssignmentNotice.id).limit(20).all()]
        for notice_id in ids:
            now = datetime.utcnow()
            # 条件更新认领，避免后台请求与定时任务同时发同一条；崩溃后租约自动过期。
            claimed = db.query(TaskAssignmentNotice).filter(
                TaskAssignmentNotice.id == notice_id, TaskAssignmentNotice.sent_at.is_(None),
                TaskAssignmentNotice.cancelled_at.is_(None), TaskAssignmentNotice.next_attempt_at <= now,
            ).update({TaskAssignmentNotice.next_attempt_at: now + timedelta(minutes=2),
                      TaskAssignmentNotice.attempts: TaskAssignmentNotice.attempts + 1}, synchronize_session=False)
            db.commit()
            if not claimed:
                continue
            notice = db.get(TaskAssignmentNotice, notice_id)
            task = db.get(Task, notice.task_id)
            if not task or task.assignment_revision != notice.revision or task.assigned_to is None:
                notice.cancelled_at = now
            elif send_tuitui_message(notice.content, notice.group_id):
                notice.sent_at = datetime.utcnow()
                notice.last_error = None
            else:
                notice.last_error = '推推未确认发送成功，请检查机器人配置、群权限及网络；系统将自动重试'
                notice.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(900, 30 * 2 ** min(notice.attempts, 5)))
            db.commit()
    except Exception:
        db.rollback()
        logger.exception('任务派单通知处理失败')
    finally:
        db.close()
