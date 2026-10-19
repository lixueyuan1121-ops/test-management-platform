from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole, TaskStatus
from app.db.session import get_db
from app.models import Project, ProjectMember, Task, TaskAssignee, User
from app.schemas.common import ok
from app.schemas.task import TaskCreate, TaskLedgerFields, TaskOut, TaskUpdate
from app.services.task_dates import filter_task_dates

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _user_name(db: Session, uid: int | None) -> str:
    if uid is None:
        return ""
    u = db.get(User, uid)
    return u.name if u else ""


def _name_map(db: Session, uids) -> dict[int, str]:
    """批量取用户名 → {id: name},消除列表里逐行查库的 N+1。"""
    ids = {u for u in uids if u}
    if not ids:
        return {}
    return dict(db.query(User.id, User.name).filter(User.id.in_(ids)).all())


def _to_out(db: Session, t: Task, on_date: date | None = None, names: dict | None = None) -> dict:
    # names 提供时走批量映射(列表用);否则逐个查(单条端点用)
    def nm(uid):
        if names is not None:
            return names.get(uid, "")
        return _user_name(db, uid)
    return {
        "id": t.id,
        "project_id": t.project_id,
        "assigned_by": t.assigned_by,
        "assigned_by_name": nm(t.assigned_by),
        "assigned_to": t.assigned_to,
        "assigned_to_name": "、".join(nm(uid) for uid in t.assigned_to_ids),
        "assigned_to_ids": t.assigned_to_ids,
        "is_ingested": bool(t.ingest_key),
        "source_group_id": t.source_group_id,
        "source_message_id": t.source_message_id,
        "title": t.title,
        "description": t.description,
        "module": t.module,
        "requirement_url": t.requirement_url,
        "developer": t.developer,
        "priority": t.priority.value,
        "assigned_date": str(t.assigned_date),
        "status": t.status.value,
        "status_locked": bool(t.status_locked),
        # 顺延标记：派单日早于查询日即为顺延（仅 list 传 on_date 时有意义）
        "is_carried": on_date is not None and t.assigned_date < on_date,
        # 终态时间戳（详情显示上线时间/关闭时间）+ 关闭备注
        "online_at": t.online_at.isoformat() if t.online_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "close_note": t.close_note,
        **{field: getattr(t, field) for field in TaskLedgerFields.model_fields},
        "release_record_id": t.release_record_id,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _assignee_ids(db: Session, body):
    supplied = body.assigned_to_ids
    if supplied is None and body.assigned_to is None:
        return None
    ids = list(dict.fromkeys(supplied if supplied is not None else [body.assigned_to]))
    if any(type(uid) is not int or uid <= 0 for uid in ids):
        raise HTTPException(422, detail="指派用户无效")
    if supplied is not None and body.assigned_to is not None and (not ids or body.assigned_to != ids[0]):
        raise HTTPException(422, detail="单人指派字段与多人指派字段不一致")
    existing = {uid for (uid,) in db.query(User.id).filter(User.id.in_(ids)).all()}
    if set(ids) != existing:
        raise HTTPException(404, detail="指派用户不存在")
    return ids


def _set_assignees(task: Task, ids):
    task.assigned_to = ids[0] if ids else None
    existing = {a.user_id: a for a in task.assignees}
    task.assignees = [existing.get(uid) or TaskAssignee(user_id=uid) for uid in ids[1:]]


def _stamp_terminal(t: Task, new_status: TaskStatus) -> None:
    """状态「变为」终态时刷新对应时间戳为最新时刻（每次进入 online/closed 都刷新，非首次固定）。
    调用前提：new_status != t.status（确为一次状态变更）。"""
    if new_status == TaskStatus.online:
        t.online_at = datetime.utcnow()
    elif new_status == TaskStatus.closed:
        t.closed_at = datetime.utcnow()


@router.get("")
def list_tasks(
    project_id: int = Query(...),
    date: date | None = None,
    mine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    dates: Annotated[list[date] | None, Query()] = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    """查项目任务。start_date/end_date 严格过滤分配日期（含首尾）；兼容 date/dates。

    传 date 时，某天列表带出以下三类，使任务在「派单当天」与「实际完成当天」都可见：
    1. 当天派单的任务（assigned_date == date）；
    2. 更早派单但仍未完成（status ∉ {online, closed}）的顺延任务——每天带出直到做完；
    3. 在当天「变为完成」的任务：online_at 或 closed_at 落在 date 当天。
       故一个昨天派单、今天上线/关闭的顺延任务会出现在今天（完成当天），符合按完成日归集。
    """
    # Preserve legacy day queries; no date parameters means all dates.
    selected = sorted(set(dates or []) | ({date} if date else set()))
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(Task).filter(Task.project_id == project_id)
    q = filter_task_dates(q, selected, start_date, end_date)
    if mine and not user.is_platform_admin:
        q = q.filter(or_(Task.assigned_to == user.id, Task.assignees.any(TaskAssignee.user_id == user.id)))
    rows = q.order_by(Task.assigned_date.desc(), Task.id.desc()).all()
    names = _name_map(db, [uid for t in rows for uid in (t.assigned_by, *t.assigned_to_ids)])
    last_date = end_date or (max(selected) if selected else None)
    def in_selection(task):
        return start_date <= task.assigned_date <= end_date if start_date else task.assigned_date in selected
    return ok([_to_out(db, t, on_date=None if in_selection(t) else last_date, names=names) for t in rows])


@router.post("")
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 任务分配已放开到成员：admin/member 均可把任务指派给项目内任意人（不再限本人）。
    assert_project_role(db, user, body.project_id, (ProjectRole.admin, ProjectRole.member))
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    ids = _assignee_ids(db, body) or []
    t = Task(
        project_id=body.project_id,
        assigned_by=user.id,
        assigned_to=ids[0] if ids else None,
        title=body.title,
        description=body.description,
        module=body.module,
        requirement_url=body.requirement_url,
        developer=body.developer,
        priority=body.priority,
        assigned_date=body.assigned_date,
        status=TaskStatus.pending,
        **{field: getattr(body, field) for field in TaskLedgerFields.model_fields},
    )
    _set_assignees(t, ids)
    db.add(t)
    db.commit()
    db.refresh(t)
    # 任务指派通知：自己派给自己不发（常见自助加任务场景）
    for uid in t.assigned_to_ids:
        if uid == t.assigned_by:
            continue
        try:
            from app.services.notify import notify_task_assigned
            proj = db.get(Project, t.project_id)
            assignee = db.get(User, uid)
            assigner = db.get(User, t.assigned_by)
            notify_task_assigned(
                task_title=t.title,
                project_name=proj.name if proj else f"项目#{t.project_id}",
                assignee_name=assignee.name if assignee else f"用户#{uid}",
                assigner_name=assigner.name if assigner else f"用户#{t.assigned_by}",
                assigned_date=str(t.assigned_date),
                priority=t.priority,
            )
        except Exception:
            pass  # 通知失败不影响任务创建
    return ok(_to_out(db, t))


@router.patch("/{tid}")
def update_task(
    tid: int,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks = None,
):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, t.project_id, (ProjectRole.admin, ProjectRole.member))
    ids = _assignee_ids(db, body)
    # Existing forms may resend the primary owner without the additional owners.
    if t.ingest_key and 'assigned_to' in body.model_fields_set and body.assigned_to is None and body.assigned_to_ids is None:
        ids = []
    requested = ids is not None and (body.assigned_to_ids is not None or body.assigned_to != t.assigned_to)
    assignment_changed = requested and set(ids) != set(t.assigned_to_ids)
    if t.ingest_key and assignment_changed:
        assert_project_role(db, user, t.project_id, (ProjectRole.admin,))
        for uid in ids:
            owner = db.get(User, uid)
            member = db.query(ProjectMember).filter_by(project_id=t.project_id, user_id=uid).first()
            if not owner or owner.status.value != 'active' or not member or member.role == ProjectRole.guest:
                raise HTTPException(400, detail='测试负责人必须是当前项目的有效管理员或成员')
        previous_revision = t.assignment_revision
        changed = db.query(Task).filter(Task.id == tid, Task.assignment_revision == previous_revision).update(
            {Task.assignment_revision: previous_revision + 1}, synchronize_session=False)
        if not changed:
            db.rollback()
            raise HTTPException(409, detail='任务负责人已被其他管理员更新，请刷新后重试')
        t.assignment_revision = previous_revision + 1
    if "target_version" in body.model_fields_set and t.release_record_id:
        if (body.target_version or "").strip() != (t.target_version or "").strip():
            raise HTTPException(400, detail="任务已填写发版记录，不能修改归属版本")
    if body.assigned_to is not None and not db.get(User, body.assigned_to):
        raise HTTPException(404, detail="指派用户不存在")
    # 扩展字段显式传 null 表示清空；旧客户端不传时保留原值。
    for field in TaskLedgerFields.model_fields:
        if field in body.model_fields_set:
            value = getattr(body, field)
            setattr(t, field, value.strip() or None if isinstance(value, str) else value)
    # Old forms resend the unchanged scalar assignee while editing other fields.
    # Preserve co-assignees unless the assignment is actually changed.
    if requested:
        _set_assignees(t, ids)
    # 非 status 字段照常更新（派单同步与人工都可写）
    for f in ("title", "description", "module", "requirement_url", "developer",
              "priority", "assigned_date"):
        v = getattr(body, f, None)
        if v is not None:
            setattr(t, f, v)
    # 关闭备注：可与 status→closed 一起提交，也可单独更新（传了才写，空串按清空处理）。
    if body.close_note is not None:
        t.close_note = body.close_note.strip() or None
    new_status = getattr(body, "status", None)
    if t.ingest_key and assignment_changed:
        if t.assigned_to is not None and t.status == TaskStatus.unassigned and new_status in (None, TaskStatus.unassigned):
            new_status = TaskStatus.pending
        elif t.assigned_to is None:
            new_status = TaskStatus.unassigned
    # 人工端点：管理员可随时改状态（本端点仅前端 UI 调用，agent 走 /sync）。
    # 人工真正改变 status 即置锁，标记已被人工接管——此后 agent 的 /sync 不再覆盖，
    # 人工自己仍可继续经本端点修改。编辑任务带原状态值提交（未变）不置锁，避免误锁。
    if new_status is not None and new_status != t.status:
        _stamp_terminal(t, new_status)   # 变为 online/closed 刷新对应时间戳
        t.status = new_status
        t.status_locked = True  # 人工接管标记
    if t.ingest_key and assignment_changed:
        from app.services.task_assignment import queue_assignment
        queue_assignment(db, t, user)
    db.commit()
    queued = bool(t.ingest_key and assignment_changed and t.assigned_to is not None)
    if queued and background_tasks is not None:
        from app.services.task_assignment import dispatch_assignment_notices
        background_tasks.add_task(dispatch_assignment_notices, t.id)
    return ok({**_to_out(db, t), 'assignment_notification_queued': queued})


@router.patch("/{tid}/sync")
def sync_task_status(
    tid: int,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """派单 agent 专用状态同步端点。语义：自动同步，绝不覆盖人工接管过的状态。

    人机同账号，无法靠身份区分，故按端点区分：人工走 PATCH /api/tasks/{id}（会置锁），
    agent 走本端点。若 status_locked 为真（已被人工接管），忽略传入 status，保留现状；
    未锁时才写入 status，且不置锁（自动同步不算人工接管，人工随后仍可接管）。
    """
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, t.project_id, (ProjectRole.admin,))
    new_status = getattr(body, "status", None)
    if new_status is not None and not t.status_locked and new_status != t.status:
        _stamp_terminal(t, new_status)   # agent 自动同步到 online/closed 也刷新时间戳
        t.status = new_status
    db.commit()
    return ok(_to_out(db, t))


@router.delete("/{tid}")
def delete_task(
    tid: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, t.project_id, (ProjectRole.admin, ProjectRole.member))
    db.delete(t)
    db.commit()
    return ok({"deleted": tid})


@router.post("/copy")
def copy_yesterday(
    project_id: int = Query(...),
    target_date: date = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把昨天的任务复制到 target_date（同项目同指派人同标题）。"""
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member))
    yesterday = target_date - timedelta(days=1)
    src = db.query(Task).filter(
        Task.project_id == project_id, Task.assigned_date == yesterday, Task.assigned_to.isnot(None),
    ).all()
    created = 0
    for s in src:
        t = Task(
            project_id=s.project_id,
            assigned_by=user.id,
            assigned_to=s.assigned_to,
            title=s.title,
            description=s.description,
            module=s.module,
            requirement_url=s.requirement_url,
            developer=s.developer,
            priority=s.priority,
            assigned_date=target_date,
            status=TaskStatus.pending,
            **{field: getattr(s, field) for field in TaskLedgerFields.model_fields},
        )
        _set_assignees(t, s.assigned_to_ids)
        db.add(t)
        created += 1
    db.commit()
    return ok({"copied": created, "target_date": str(target_date)})
