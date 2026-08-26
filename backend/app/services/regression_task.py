"""「线上回归」常驻任务：每个项目一个、status=testing 的自动维护任务。

用例被标记回归（is_regression=true）时，自动通过 checklist_item 挂到本项目的线上回归任务下，
作为集中备份（关联指向、不复制；取消回归即摘除；原用例删除则 CASCADE 自动清理）。

被 main.py（startup ensure 全部项目）、projects.py（新建项目时 ensure）、
ai.py（标记回归钩子取任务）共用。
"""
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import TaskStatus
from app.models import Project, Task, User

logger = logging.getLogger("test_platform")


def _seed_admin_id(db: Session) -> int | None:
    """线上回归任务的 assigned_by/to 用种子管理员（平台任务，非某个人）。"""
    admin = db.query(User).filter_by(username=settings.SEED_ADMIN_USERNAME).first()
    return admin.id if admin else None


def get_regression_task(db: Session, project_id: int) -> Task | None:
    """取某项目的线上回归任务（is_regression_task=True）；无则 None。"""
    return (db.query(Task)
            .filter(Task.project_id == project_id, Task.is_regression_task.is_(True))
            .first())


def ensure_regression_task(db: Session, project_id: int, admin_id: int | None = None) -> Task | None:
    """幂等 ensure 某项目的线上回归任务。已存在直接返回；无管理员则跳过（返回 None）。"""
    existing = get_regression_task(db, project_id)
    if existing:
        return existing
    admin_id = admin_id or _seed_admin_id(db)
    if not admin_id:
        return None
    from datetime import date
    task = Task(
        project_id=project_id,
        assigned_by=admin_id,
        assigned_to=admin_id,
        title=settings.REGRESSION_TASK_TITLE,
        description="平台自动维护的线上回归任务：标记为回归的用例自动汇集于此。",
        status=TaskStatus.testing,
        status_locked=True,          # 防派单同步覆盖其状态
        is_regression_task=True,
        assigned_date=date.today(),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("已创建线上回归任务: project=%s task=%s", project_id, task.id)
    return task


def ensure_all_regression_tasks(db: Session) -> None:
    """startup 调用：给所有非专用项目 ensure 线上回归任务。反馈专用项目走独立回归机制，跳过。"""
    admin_id = _seed_admin_id(db)
    if not admin_id:
        return
    projects = db.query(Project).filter(Project.code != settings.FEEDBACK_PROJECT_CODE).all()
    for p in projects:
        ensure_regression_task(db, p.id, admin_id)


def sync_regression_link(db: Session, case_ids: list[int], is_regression: bool) -> None:
    """用例标记/取消回归后，同步 checklist_item 里「线上回归任务 ↔ 用例」的关联。

    - is_regression=True：把每个用例挂到其项目的线上回归任务下（upsert，已挂则跳过）。
    - is_regression=False：删除该用例与线上回归任务的关联（不影响用例与其它任务的关联）。

    关联指向、不复制。原用例删除时 checklist_item 走 CASCADE 自动清理。调用方已 commit 过
    is_regression 变更，本函数自行 commit 关联变更。
    """
    from app.models import ChecklistItem, TestCase

    if not case_ids:
        return
    cases = db.query(TestCase).filter(TestCase.id.in_(case_ids)).all()
    for tc in cases:
        rt = get_regression_task(db, tc.project_id)
        if not rt:
            continue
        exists = (db.query(ChecklistItem)
                  .filter(ChecklistItem.task_id == rt.id, ChecklistItem.test_case_id == tc.id)
                  .first())
        if is_regression and not exists:
            db.add(ChecklistItem(task_id=rt.id, test_case_id=tc.id, project_id=tc.project_id))
        elif not is_regression and exists:
            db.delete(exists)
    db.commit()

