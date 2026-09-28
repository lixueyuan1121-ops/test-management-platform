"""Daily reports remain one shared progress report per task/date."""


def reported_assignee_ids(tasks, reports):
    reports = list(reports)
    submitted = {r.user_id for r in reports if r.user_id}
    reported_tasks = {r.task_id for r in reports}
    for task in tasks:
        ids = task.assigned_to_ids
        if task.id in reported_tasks and len(ids) > 1:
            submitted.update(ids)
    return submitted
