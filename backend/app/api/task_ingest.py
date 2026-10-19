"""提测转发机器人的自动录入入口。"""
import hashlib
import json
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.tasks import _to_out
from app.core.config import settings
from app.core.deps import assert_project_role
from app.core.enums import ProjectRole, TaskStatus
from app.core.security import decode_token
from app.db.session import get_db
from app.models import Project, Task, User
from app.schemas.common import ok
from app.schemas.task import TaskIngest

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


def require_ingest_user(
    x_task_ingest_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    uid = None
    if x_task_ingest_token is not None:
        expected = settings.TASK_INGEST_TOKEN
        if not expected or not secrets.compare_digest(x_task_ingest_token.encode(), expected.encode()):
            raise HTTPException(401, detail='提测录入令牌无效或未配置')
        uid = settings.TASK_INGEST_USER_ID
    elif authorization and authorization.lower().startswith('bearer '):
        payload = decode_token(authorization[7:].strip())
        if payload and payload.get('type') == 'access':
            try:
                uid = int(payload.get('sub', ''))
            except (ValueError, TypeError):
                pass
    user = db.get(User, uid) if uid else None
    if not user or user.status.value != 'active':
        raise HTTPException(401, detail='需要有效的提测录入令牌或平台账号令牌')
    return user


@router.post('/ingest')
def ingest_task(body: TaskIngest, db: Session = Depends(get_db), user: User = Depends(require_ingest_user)):
    assert_project_role(db, user, body.project_id, (ProjectRole.admin,))
    project = db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, detail='项目不存在')
    if project.status.value != 'active':
        raise HTTPException(400, detail='项目已停用')
    # project 参与键值，避免同一消息明确提交到不同项目时发生跨项目关联。
    key = hashlib.sha256(json.dumps([body.project_id, body.source_group_id, body.source_message_id],
                                   ensure_ascii=False).encode()).hexdigest()
    existing = db.query(Task).filter_by(ingest_key=key).first()
    if existing:
        return ok({'created': False, 'task': _to_out(db, existing)})
    values = body.model_dump(exclude={'assigned_date'})
    task = Task(**values, ingest_key=key, assigned_by=user.id, assigned_to=None,
                assigned_date=body.assigned_date or datetime.now(ZoneInfo('Asia/Shanghai')).date(),
                status=TaskStatus.unassigned)
    db.add(task)
    try:
        db.commit()
    except IntegrityError:
        # 并发重复推送由唯一索引兜底，重试不覆盖人工编辑/派单结果。
        db.rollback()
        existing = db.query(Task).filter_by(ingest_key=key).first()
        if not existing:
            raise
        return ok({'created': False, 'task': _to_out(db, existing)})
    db.refresh(task)
    return ok({'created': True, 'task': _to_out(db, task)})
