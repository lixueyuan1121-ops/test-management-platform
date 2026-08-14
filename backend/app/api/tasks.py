from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole, TaskStatus
from app.db.session import get_db
from app.models import Project, ProjectMember, Task, User
from app.schemas.common import ok
from app.schemas.task import TaskCreate, TaskOut, TaskUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _user_name(db: Session, uid: int) -> str:
    u = db.get(User, uid)
    return u.name if u else ""


def _to_out(db: Session, t: Task, on_date: date | None = None) -> dict:
    return {
        "id": t.id,
        "project_id": t.project_id,
        "assigned_by": t.assigned_by,
        "assigned_by_name": _user_name(db, t.assigned_by),
        "assigned_to": t.assigned_to,
        "assigned_to_name": _user_name(db, t.assigned_to),
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
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("")
def list_tasks(
    project_id: int = Query(...),
    date: date | None = None,
    mine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查项目任务。mine=1 时只看指派给我的；date 过滤分配日期。

    顺延可见：传 date 时，除当天派单的任务外，还带出更早日期但仍未完成
    （status ∉ {online, closed}）的任务，使未做完的任务不会因翻页到次日而消失。
    """
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(Task).filter(Task.project_id == project_id)
    if date:
        q = q.filter(or_(
            Task.assigned_date == date,
            (Task.assigned_date < date) & Task.status.notin_(
                [TaskStatus.online, TaskStatus.closed]
            ),
        ))
    if mine and not user.is_platform_admin:
        q = q.filter(Task.assigned_to == user.id)
    rows = q.order_by(Task.assigned_date.desc(), Task.id.desc()).all()
    return ok([_to_out(db, t, on_date=date) for t in rows])


@router.post("")
def create_task(
    body: TaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 放开到成员:项目 admin/member 都可建任务(方便成员自助加任务)。
    member = assert_project_role(db, user, body.project_id, (ProjectRole.admin, ProjectRole.member))
    if not db.get(Project, body.project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="项目不存在")
    if not db.get(User, body.assigned_to):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="指派用户不存在")
    # 成员(非平台管理员、非项目 admin)只能把任务指派给**自己**,不能派给他人;admin 不限。
    is_admin = user.is_platform_admin or member.role == ProjectRole.admin
    if not is_admin and body.assigned_to != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="成员只能把任务指派给自己")
    t = Task(
        project_id=body.project_id,
        assigned_by=user.id,
        assigned_to=body.assigned_to,
        title=body.title,
        description=body.description,
        module=body.module,
        requirement_url=body.requirement_url,
        developer=body.developer,
        priority=body.priority,
        assigned_date=body.assigned_date,
        status=TaskStatus.pending,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return ok(_to_out(db, t))


@router.patch("/{tid}")
def update_task(
    tid: int,
    body: TaskUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = db.get(Task, tid)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务不存在")
    assert_project_role(db, user, t.project_id, (ProjectRole.admin,))
    # 非 status 字段照常更新（派单同步与人工都可写）
    for f in ("title", "description", "module", "requirement_url", "developer",
              "priority", "assigned_to", "assigned_date"):
        v = getattr(body, f, None)
        if v is not None:
            setattr(t, f, v)
    new_status = getattr(body, "status", None)
    # 人工端点：管理员可随时改状态（本端点仅前端 UI 调用，agent 走 /sync）。
    # 人工真正改变 status 即置锁，标记已被人工接管——此后 agent 的 /sync 不再覆盖，
    # 人工自己仍可继续经本端点修改。编辑任务带原状态值提交（未变）不置锁，避免误锁。
    if new_status is not None and new_status != t.status:
        t.status = new_status
        t.status_locked = True  # 人工接管标记
    db.commit()
    return ok(_to_out(db, t))


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
    if new_status is not None and not t.status_locked:
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
    assert_project_role(db, user, t.project_id, (ProjectRole.admin,))
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
    assert_project_role(db, user, project_id, (ProjectRole.admin,))
    yesterday = target_date - timedelta(days=1)
    src = db.query(Task).filter(
        Task.project_id == project_id, Task.assigned_date == yesterday
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
        )
        db.add(t)
        created += 1
    db.commit()
    return ok({"copied": created, "target_date": str(target_date)})
