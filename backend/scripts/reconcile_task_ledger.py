"""Reconcile a reviewed Feishu mapping. Prepare is read-only; apply requires a backup.

Mappings explicitly select one primary task per source row, duplicate tasks to close,
and platform user IDs for new tasks (an empty list leaves the owner unassigned).
"""
import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.enums import TaskStatus
from app.db.session import engine
from app.models import Project, Task, TaskAssignee, User
from app.schemas.task import TaskCreate, TaskUpdate
from scripts.backfill_task_ledger import make_plan, normalized, scalar, FIELD_MAP, STATUS_MAP


def snapshot(task):
    return {**{c.name: scalar(getattr(task, c.name)) for c in Task.__table__.columns},
            'assigned_to_ids': task.assigned_to_ids}


def prepare(db, source, mapping, project_name, year, actor_id):
    project = db.query(Project).filter_by(name=project_name).one()
    actor = db.get(User, actor_id)
    if not actor or not actor.is_platform_admin:
        raise ValueError('指定操作人必须是平台管理员')
    tasks = {t.id: t for t in db.query(Task).filter_by(project_id=project.id).all()}
    rows = {str(r['source_row']): r for r in source['rows']}
    primary = mapping['primary']
    owners = mapping['new_owners']
    if set(primary) & set(owners) or set(primary) | set(owners) != set(rows):
        raise ValueError('每一来源行必须且只能指定主任务或新增负责人')
    if len(set(primary.values())) != len(primary):
        raise ValueError('不同来源行不能共用主任务')
    overrides = {row: {'task_id': tid, 'title': tasks[tid].title} for row, tid in primary.items()}
    selected = {**source, 'rows': [rows[r] for r in primary]}
    base = make_plan(db, project, selected, overrides)
    if base['unresolved']:
        raise ValueError('主任务匹配未通过')
    updates = []
    for change in base['changes']:
        task = tasks[change['task_id']]
        values = {**change['values'], 'title': rows[str(change['source_row'])]['功能/需求']}
        TaskUpdate.model_validate(values)
        updates.append({'task_id': task.id, 'source_row': change['source_row'],
                        'before': snapshot(task), 'values': values, 'warnings': change['warnings']})
    creates = []
    for row_key, ids in owners.items():
        row = rows[row_key]
        if len(set(ids)) != len(ids) or any(not db.get(User, uid) for uid in ids):
            raise ValueError('新增任务负责人不是平台有效用户')
        if any(normalized(t.title) == normalized(row['功能/需求']) for t in tasks.values()):
            raise ValueError('待新增任务已有同名记录，请重新匹配')
        raw_date = str(row['日期']).zfill(4)
        assigned_date = date(year, int(raw_date[:2]), int(raw_date[2:])).isoformat()
        description = '\n'.join([f"飞书来源：{source['url']}（第 {row_key} 行）"] +
                                [f'{k}：{v}' for k, v in row.items() if k != 'source_row' and v])
        body = {'project_id': project.id, 'title': row['功能/需求'], 'description': description,
                'assigned_to_ids': ids, 'assigned_date': assigned_date,
                'priority': row.get('优先级', 'P2').lower(),
                'developer': mapping.get('new_developers', {}).get(row_key, row.get('开发负责人') or None)}
        for label, field in FIELD_MAP.items():
            value = row.get(label)
            if value in (None, ''):
                continue
            if field == 'risk_level': value = {'绿': 'green', '黄': 'yellow', '红': 'red'}[value]
            if field == 'affects_release': value = {'是': True, '否': False}[value]
            if field in ('feedback_due_at', 'expected_completion', 'action_owner'):
                raise ValueError('新增任务的时间/动作负责人需显式核对')
            body[field] = value
        if row.get('需求文档', '').startswith(('https://', 'http://')):
            body['requirement_url'] = row['需求文档']
        body = TaskCreate.model_validate(body).model_dump(mode='json')
        creates.append({'source_row': int(row_key), 'body': body,
                        'status': STATUS_MAP.get(row.get('状态'), 'pending')})
    closes = []
    for tid_string, canonical_ids in mapping['duplicates'].items():
        tid = int(tid_string)
        if tid in primary.values() or not canonical_ids or not set(canonical_ids) <= set(primary.values()):
            raise ValueError('重复任务必须指向本次核实的主任务')
        task = tasks[tid]
        if task.release_record_id:
            raise ValueError('重复任务已有发版关联，需先人工处理')
        note = '飞书台账对齐：与主任务 ' + '、'.join(f'#{i}' for i in canonical_ids) + ' 重复，按要求关闭。原记录和验收数据保留。'
        closes.append({'task_id': tid, 'before': snapshot(task), 'primary_ids': canonical_ids,
                       'close_note': '\n'.join(filter(None, [task.close_note, note]))})
    return {'project_id': project.id, 'project_name': project.name, 'actor_id': actor_id,
            'source': source['url'], 'source_count': len(rows), 'prepared_at': datetime.now().isoformat(),
            'updates': updates, 'creates': creates, 'closes': closes}


def apply(db, plan, backup_path, result_path):
    if backup_path.exists() or result_path.exists():
        raise ValueError('备份/结果已存在，拒绝重复执行')
    project = db.get(Project, plan['project_id'])
    actor = db.get(User, plan['actor_id'])
    if not project or project.name != plan['project_name'] or not actor or not actor.is_platform_admin:
        raise ValueError('项目或操作人身份发生变化')
    ids = [r['task_id'] for r in plan['updates'] + plan['closes']]
    if len(ids) != len(set(ids)):
        raise ValueError('同一任务不能重复修改')
    tasks = {t.id: t for t in db.query(Task).filter(Task.id.in_(ids)).with_for_update().all()}
    for r in plan['updates'] + plan['closes']:
        task = tasks.get(r['task_id'])
        if not task or task.project_id != project.id or snapshot(task) != r['before']:
            raise ValueError(f"任务 #{r['task_id']} 已变化，请重新预览")
    parsed_updates = []
    allowed = set(FIELD_MAP.values()) | {'priority', 'status', 'requirement_url', 'title'}
    for r in plan['updates']:
        if not set(r['values']) <= allowed:
            raise ValueError('不允许修改现有任务负责人或关联关系')
        values = TaskUpdate.model_validate(r['values']).model_dump(exclude_unset=True)
        task = tasks[r['task_id']]
        if task.status in (TaskStatus.online, TaskStatus.closed) and values.get('status', task.status) != task.status:
            raise ValueError('不能回退已终结主任务')
        if task.release_record_id and values.get('target_version', task.target_version) != task.target_version:
            raise ValueError('不能修改已归档版本')
        parsed_updates.append((task, values))
    parsed_creates = []
    titles = {normalized(t.title) for t in db.query(Task).filter_by(project_id=project.id).all()}
    for r in plan['creates']:
        body = TaskCreate.model_validate(r['body'])
        if body.project_id != project.id or normalized(body.title) in titles:
            raise ValueError('新增任务项目不符或已经存在')
        titles.add(normalized(body.title))
        owner_ids = body.assigned_to_ids or []
        if any(not db.get(User, uid) for uid in owner_ids):
            raise ValueError('新增任务负责人不存在')
        parsed_creates.append((r, body, TaskStatus(r['status'])))
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with backup_path.open('x', encoding='utf-8') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    for task, values in parsed_updates:
        if 'status' in values and values['status'] != task.status: task.status_locked = True
        for field, value in values.items(): setattr(task, field, value)
    for r in plan['closes']:
        task = tasks[r['task_id']]
        task.status = TaskStatus.closed
        task.status_locked = True
        task.closed_at = datetime.utcnow()
        task.close_note = r['close_note']
    created = []
    for r, body, status in parsed_creates:
        values = body.model_dump(exclude={'assigned_to_ids', 'assigned_to'})
        owner_ids = body.assigned_to_ids or []
        task = Task(**values, assigned_by=actor.id, assigned_to=owner_ids[0] if owner_ids else None,
                    status=status, status_locked=True)
        task.assignees = [TaskAssignee(user_id=uid) for uid in owner_ids[1:]]
        db.add(task); db.flush()
        created.append({'source_row': r['source_row'], 'task_id': task.id, 'title': task.title})
    db.flush()
    # Read stored precision (e.g. MySQL DATETIME truncates microseconds) before comparing.
    db.expire_all()
    expected = {tid: snapshot(task) for tid, task in tasks.items()}
    expected.update({r['task_id']: snapshot(db.get(Task, r['task_id'])) for r in created})
    db.commit()
    db.expire_all()
    for tid, value in expected.items():
        if snapshot(db.get(Task, tid)) != value: raise RuntimeError(f'任务 #{tid} 回读不一致')
    result = {'updated': len(plan['updates']), 'created': created, 'closed': [r['task_id'] for r in plan['closes']],
              'verified': True, 'backup': str(backup_path)}
    with result_path.open('x', encoding='utf-8') as f: json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'mapping', 'output', 'apply-plan', 'backup', 'result'): p.add_argument('--' + name, type=Path)
    p.add_argument('--project-name'); p.add_argument('--year', type=int); p.add_argument('--actor-id', type=int)
    a = p.parse_args()
    with Session(engine) as db:
        if a.apply_plan:
            if not a.backup or not a.result: p.error('执行必须指定 --backup 和 --result')
            result = apply(db, json.loads(a.apply_plan.read_text()), a.backup, a.result)
            print(json.dumps({**result, 'created': len(result['created'])}, ensure_ascii=False))
        else:
            if not all([a.source, a.mapping, a.output, a.project_name, a.year, a.actor_id]): p.error('缺少预览参数')
            plan = prepare(db, json.loads(a.source.read_text()), json.loads(a.mapping.read_text()), a.project_name, a.year, a.actor_id)
            a.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
            print(json.dumps({k: len(plan[k]) for k in ('updates', 'creates', 'closes')}))


if __name__ == '__main__': main()
