"""从飞书快照预览并回填提测台账，默认只读。

cd backend
.venv/bin/python -m scripts.backfill_task_ledger --source ../artifacts/task-ledger/feishu-reference-2026-10-16.json --project-name '纳米Work PC端' --output /tmp/ledger-plan.json
.venv/bin/python -m scripts.backfill_task_ledger --apply-plan /tmp/ledger-plan.json --backup /tmp/ledger-before.json

不创建/删除任务，不改测试/开发负责人、原始日期、任务名和说明。
计划含逐字段旧值前置校验；执行时任务有变化则整批拒绝。歧义匹配不进入写入计划。
"""
import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.enums import TaskStatus
from app.db.session import engine
from app.models import Project, Task, User
from app.schemas.task import TaskUpdate

STATUS_MAP = {'待分配':'unassigned', '待跟进':'pending', '待测':'pending', '测试中':'testing',
              '阻塞':'blocked', '待开发修复':'waiting_fix', '回归中':'regressing',
              '测试完成':'ready_online', '有风险':'at_risk', '暂停':'paused', '已上线':'online'}
FIELD_MAP = {'风险等级':'risk_level','归属版本':'target_version','当前卡点/进度':'progress_note',
             '预计完成':'expected_completion','下一步动作':'next_action','动作负责人':'action_owner',
             '最晚反馈时间':'feedback_due_at','是否影响发版':'affects_release','结论/备注':'conclusion'}
ALLOWED = set(FIELD_MAP.values()) | {'priority','status','requirement_url'}


def scalar(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return getattr(value, 'value', value)


def normalized(value):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', (value or '').lower())


def make_plan(db: Session, project: Project, source: dict, matches_override=None):
    tasks = db.query(Task).filter_by(project_id=project.id).all()
    names = {name for (name,) in db.query(User.name).all()}
    changes, unresolved = [], []
    consumed = set()
    for row in source['rows']:
        title = normalized(row.get('功能/需求'))
        if not title:
            continue
        matches = [t for t in tasks if normalized(t.title) == title]
        if not matches and len(title) >= 8:
            matches = [t for t in tasks if title in normalized(t.title) or title in normalized(t.description)]
        override = (matches_override or {}).get(str(row['source_row']))
        if override:
            matches = [t for t in tasks if t.id == override['task_id'] and t.title == override['title']]
        if len(matches) != 1 or matches[0].id in consumed:
            unresolved.append({'source_row':row['source_row'],'title':row['功能/需求'],
                'reason':'未匹配到唯一任务' if len(matches) != 1 else '同一任务匹配多个来源行',
                'candidates':[{'id':t.id,'title':t.title,'assigned_date':str(t.assigned_date)} for t in matches]})
            continue
        task = matches[0]
        consumed.add(task.id)
        values, warnings = {}, []
        for label, field in FIELD_MAP.items():
            value = row.get(label)
            if value is None or value == '':
                continue  # 来源空白不是删除指令。
            if field == 'risk_level':
                value = {'绿':'green','黄':'yellow','红':'red'}.get(value)
            elif field == 'affects_release':
                value = {'是':True,'否':False}.get(value)
            elif field == 'action_owner' and value not in names:
                warnings.append(f'动作负责人“{value}”不是平台姓名，保留平台原值'); continue
            elif field in ('feedback_due_at','expected_completion'):
                try:
                    value = (datetime.fromisoformat(value) if field == 'feedback_due_at' else date.fromisoformat(value)).isoformat()
                except ValueError:
                    warnings.append(f'{label}“{value}”缺少绝对日期，待人工确认'); continue
            if field == 'target_version' and task.release_record_id and task.target_version != value:
                warnings.append('已关联发版记录，归属版本冲突，保留平台版本'); continue
            if value is not None and scalar(getattr(task,field)) != value:
                values[field] = value
        priority = row.get('优先级','').lower()
        if priority in ('p0','p1','p2','p3') and scalar(task.priority) != priority:
            values['priority'] = priority
        status = STATUS_MAP.get(row.get('状态'))
        if status and scalar(task.status) != status:
            if task.status in (TaskStatus.online,TaskStatus.closed):
                warnings.append(f'平台已终结（{task.status.value}），不自动回退到飞书状态（{status}）')
            else:
                values['status'] = status
        url = row.get('需求文档','')
        if url.startswith(('https://','http://')) and not task.requirement_url:
            values['requirement_url'] = url
        elif row.get('需求文档引用') and not task.requirement_url:
            warnings.append('来源需求文档为富文本引用，未核实原始链接，保留引用供核对')
        TaskUpdate.model_validate(values)
        changes.append({'task_id':task.id,'title':task.title,'source_row':row['source_row'],
                        'before':{field:scalar(getattr(task,field)) for field in values}, 'values':values,
                        'assigned_to':task.assigned_to,'assigned_to_ids':task.assigned_to_ids,'developer':task.developer,
                        'warnings':warnings})
    return {'project_id':project.id,'project_name':project.name,'source':source.get('url'),
            'source_revision':source.get('revision'),'source_count':len(source['rows']),
            'changes':changes,'unresolved':unresolved}


def apply_plan(db: Session, plan: dict, backup_path: Path):
    if backup_path.exists():
        raise ValueError('备份文件已存在，请指定新的备份文件，避免覆盖')
    project = db.get(Project,plan['project_id'])
    if not project or project.name != plan['project_name']:
        raise ValueError('项目身份发生变化')
    updates, backup, seen = [], [], set()
    for change in plan['changes']:
        values = change['values']
        if not values:
            continue
        if not set(values) <= ALLOWED or set(values) != set(change['before']):
            raise ValueError('写入计划包含非允许字段或缺少旧值')
        if change['task_id'] in seen:
            raise ValueError('写入计划包含重复任务')
        seen.add(change['task_id'])
        task = db.query(Task).filter_by(id=change['task_id'],project_id=project.id).with_for_update().first()
        if not task or task.title != change['title'] or task.assigned_to != change['assigned_to'] or task.assigned_to_ids != change['assigned_to_ids'] or task.developer != change['developer']:
            raise ValueError(f"任务身份/负责人变化：{change['task_id']}")
        if any(scalar(getattr(task,f)) != before for f,before in change['before'].items()):
            raise ValueError(f'任务 #{task.id} 自预览后已变化，请重新生成计划')
        parsed = TaskUpdate.model_validate(values).model_dump(exclude_unset=True)
        if 'target_version' in parsed and task.release_record_id and task.target_version != parsed['target_version']:
            raise ValueError(f'任务 #{task.id} 已关联发版记录，不能改归属版本')
        if 'status' in parsed and task.status in (TaskStatus.online,TaskStatus.closed) and task.status != parsed['status']:
            raise ValueError(f'任务 #{task.id} 已终结，不能自动回退状态')
        updates.append((task,parsed))
        backup.append({'task_id':task.id,'before':change['before'],'after':values,
                       'title':task.title,'assigned_to_ids':task.assigned_to_ids,'developer':task.developer,
                       'status_locked':task.status_locked,'online_at':scalar(task.online_at),'closed_at':scalar(task.closed_at)})
    backup_path.parent.mkdir(parents=True,exist_ok=True)
    # 写前备份；独占创建，防覆盖。事务失败仍保留备份供核对。
    with backup_path.open('x',encoding='utf-8') as f:
        json.dump({'project_id':project.id,'project_name':project.name,'source':plan.get('source'),'created_at':datetime.now().isoformat(),'changes':backup},f,ensure_ascii=False,indent=2)
    for task,values in updates:
        for field,value in values.items():
            if field == 'status' and task.status != value:
                # 飞书历史状态没有上线时刻，保留既有时间，不能编造为“现在上线”。
                task.status_locked = True
            setattr(task,field,value)
    db.commit()
    db.expire_all()
    for change in backup:
        task = db.get(Task,change['task_id'])
        if any(scalar(getattr(task,f)) != value for f,value in change['after'].items()):
            raise RuntimeError(f"回读校验失败：任务 #{task.id}")
    return len(updates)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--matches',type=Path,help='已人工核实的来源行→平台任务 ID/标题映射')
    parser.add_argument('--project-name')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--apply-plan',type=Path)
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args()
    with Session(engine) as db:
        if args.apply_plan:
            if not args.backup:
                parser.error('--apply-plan 必须提供 --backup')
            count=apply_plan(db,json.loads(args.apply_plan.read_text()),args.backup)
            print(json.dumps({'updated':count,'verified':True,'backup':str(args.backup)},ensure_ascii=False))
        else:
            if not all([args.source,args.project_name,args.output]):
                parser.error('预览需要 --source、--project-name、--output')
            projects=db.query(Project).filter_by(name=args.project_name).all()
            if len(projects)!=1:
                raise ValueError('项目名称必须唯一匹配')
            plan=make_plan(db,projects[0],json.loads(args.source.read_text()),json.loads(args.matches.read_text()) if args.matches else None)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
            print(json.dumps({'matched':len(plan['changes']),'unresolved':len(plan['unresolved']),'plan':str(args.output)},ensure_ascii=False))


if __name__=='__main__':
    main()
