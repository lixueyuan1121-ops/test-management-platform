"""Task grouping for verified imports. Caller must hold the project write lock."""
from datetime import date
from app.models import Task


def resolve_import_task(db, project_id, user_id, task_name):
    task = (db.query(Task).filter_by(project_id=project_id, title=task_name)
            .order_by(Task.id).with_for_update().populate_existing().first())
    if task is None:
        task = Task(project_id=project_id, title=task_name, assigned_by=user_id,
                    assigned_to=user_id, assigned_date=date.today(),
                    description="Codex 实测用例导入关联任务")
        db.add(task)
        db.flush()
    return task
