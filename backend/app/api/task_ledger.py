"""提测台账的日报快照与发版归档。原逐任务日报和验收关系不变。"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, require_platform_admin
from app.core.enums import ProjectRole, TaskStatus
from app.db.session import get_db
from app.models import DailyReport, Project, ReleaseRecord, Task, TaskDigest, User
from app.schemas.common import ok
from app.schemas.task_ledger import DigestGenerate, DigestSave, TaskReleaseCreate

router = APIRouter(prefix="/api/task-ledger", tags=["task-ledger"])
READ_ROLES = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)
STATUS = {"pending": "待跟进", "testing": "测试中", "ready_online": "测试完成",
          "unassigned": "待分配", "waiting_fix": "待开发修复", "regressing": "回归中", "at_risk": "有风险", "paused": "暂停",
          "blocked": "阻塞", "online": "已上线", "closed": "已关闭"}


def selected_tasks(db, project_id, task_ids, lock=False):
    ids = list(dict.fromkeys(task_ids))
    q = db.query(Task).filter(Task.project_id == project_id, Task.id.in_(ids)).order_by(Task.id)
    if lock:
        q = q.with_for_update()
    rows = q.all()
    if len(rows) != len(ids):
        raise HTTPException(400, detail="所选任务已删除或不属于当前项目，请刷新后重试")
    by_id = {t.id: t for t in rows}
    return [by_id[tid] for tid in ids]


def digest_out(row):
    return {"id": row.id, "project_id": row.project_id, "report_date": str(row.report_date),
            "title": row.title, "content": row.content, "task_ids": json.loads(row.task_ids),
            "filter_summary": row.filter_summary, "updated_at": row.updated_at}


@router.post("/digests/generate")
def generate_digest(body: DigestGenerate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, WRITE_ROLES)
    rows = selected_tasks(db, body.project_id, body.task_ids)
    project = db.get(Project, body.project_id)
    names = dict(db.query(User.id, User.name).filter(User.id.in_([uid for t in rows for uid in t.assigned_to_ids])).all())
    reports = {r.task_id: r for r in db.query(DailyReport).filter(
        DailyReport.task_id.in_(body.task_ids), DailyReport.report_date == body.report_date).all()}
    done = [t for t in rows if t.status in (TaskStatus.ready_online, TaskStatus.online)]
    risky = [t for t in rows if t.risk_level in ("red", "yellow") or t.status in (TaskStatus.blocked, TaskStatus.at_risk) or t.affects_release]
    title = f"{project.name} · {body.report_date} 测试日报"
    lines = [f"# {title}", "", f"筛选范围：{body.filter_summary or '当前任务列表'}", "", "## 今日提测概览",
             f"共 {len(rows)} 个功能：测试完成/已上线 {len(done)} 个，测试中 {sum(t.status == TaskStatus.testing for t in rows)} 个，"
             f"阻塞 {sum(t.status == TaskStatus.blocked for t in rows)} 个，有风险 {len(risky)} 个。"]

    def detail(t):
        owner = "、".join([names.get(uid, "") for uid in t.assigned_to_ids]) or "未指定"
        parts = [f"{t.title}：{STATUS[t.status.value]}", f"测试负责人：{owner}"]
        if t.target_version:
            parts.append(f"归属版本：{t.target_version}")
        report = reports.get(t.id)
        for label, value in [("当前卡点/进度", t.progress_note), ("日报反馈", report.summary if report else None),
                             ("结论/备注", t.conclusion)]:
            if value:
                parts.append(f"{label}：{value}")
        if t.expected_completion:
            parts.append(f"预计完成：{t.expected_completion}")
        if t.risk_level:
            parts.append(f"风险：{ {'green': '绿', 'yellow': '黄', 'red': '红'}[t.risk_level] }")
        if t.affects_release is not None:
            parts.append(f"影响发版：{'是' if t.affects_release else '否'}")
        if t.next_action:
            parts.append(f"下一步：{t.next_action}")
        if t.action_owner:
            parts.append(f"动作负责人：{t.action_owner}")
        if t.feedback_due_at:
            parts.append(f"最晚反馈：{t.feedback_due_at:%Y-%m-%d %H:%M}")
        return "；".join(parts).replace("\n", "\n   ")

    sections = [("已完成", done), ("进行中/待跟进", [t for t in rows if t not in done and t.status != TaskStatus.closed]),
                ("风险/阻塞", risky), ("需要协助/决策", [t for t in rows if t.next_action]),
                ("已关闭", [t for t in rows if t.status == TaskStatus.closed])]
    for heading, group in sections:
        lines.extend(["", f"## {heading}"])
        lines.extend([f"{i}. {detail(t)}" for i, t in enumerate(group, 1)] or ["暂无。"])
    return ok({"title": title, "content": "\n".join(lines), "task_ids": [t.id for t in rows]})


@router.get("/digests")
def list_digests(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, WRITE_ROLES)
    rows = db.query(TaskDigest).filter_by(project_id=project_id, author_id=user.id).order_by(TaskDigest.id.desc()).all()
    return ok([digest_out(r) for r in rows])


@router.post("/digests")
def save_digest(body: DigestSave, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, WRITE_ROLES)
    row = db.get(TaskDigest, body.id) if body.id else None
    if body.id and (not row or row.project_id != body.project_id or row.author_id != user.id):
        raise HTTPException(404, detail="日报不存在或无编辑权限")
    if row is None:
        selected_tasks(db, body.project_id, body.task_ids)
        row = TaskDigest(project_id=body.project_id, author_id=user.id,
                         task_ids=json.dumps(list(dict.fromkeys(body.task_ids))), filter_summary=body.filter_summary)
        db.add(row)
    # 保存已有快照不重新读取任务，任务后续删除不影响历史日报。
    row.report_date, row.title, row.content = body.report_date, body.title, body.content
    db.commit()
    db.refresh(row)
    return ok(digest_out(row))


@router.post("/releases")
def fill_release(body: TaskReleaseCreate, db: Session = Depends(get_db), user: User = Depends(require_platform_admin)):
    from app.api.release import _norm_channel, _norm_sub_product, _to_out
    project = db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, detail="项目不存在")
    # 同项目归档串行；同时锁任务和目标记录，重复点击不重复累加。
    db.query(Project).filter_by(id=body.project_id).with_for_update().one()
    tasks = selected_tasks(db, body.project_id, body.task_ids, lock=True)
    version = body.version.strip()
    if not version or any(t.status != TaskStatus.online or (t.target_version or '').strip() != version for t in tasks):
        raise HTTPException(400, detail="仅可填写已上线且归属版本一致、非空的任务")
    if not body.content.strip():
        raise HTTPException(400, detail="上线内容不能为空")
    rel = None
    if body.release_id:
        rel = db.query(ReleaseRecord).filter_by(id=body.release_id).with_for_update().first()
        if not rel or rel.project_id != body.project_id or rel.version != version:
            raise HTTPException(400, detail="发版记录必须属于同一项目和归属版本")
    linked_ids = {t.release_record_id for t in tasks if t.release_record_id}
    if linked_ids:
        if len(linked_ids) == 1 and all(t.release_record_id for t in tasks) and (not rel or rel.id in linked_ids):
            existing = db.get(ReleaseRecord, next(iter(linked_ids)))
            if existing:
                return ok({"release": _to_out(db, existing), "added": 0})
        # 不允许混入已归档任务，否则手工编写的内容可能重复。
        raise HTTPException(409, detail="所选任务含已填写发版记录的内容，请刷新后仅选择未填写任务")
    if not rel:
        sub = _norm_sub_product(body.sub_product, project.platform_type, db=db, project_id=project.id)
        duplicate = db.query(ReleaseRecord).filter_by(project_id=body.project_id, version=version,
            sub_product=sub, release_date=body.release_date).first()
        if duplicate:
            raise HTTPException(409, detail="该版本和日期已有发版记录，请选择追加到已有记录")
        rel = ReleaseRecord(project_id=body.project_id, version=version, release_date=body.release_date,
            sub_product=sub, channel=_norm_channel(body.channel, project.platform_type),
            req_count=0, created_by=user.id, memo=body.memo)
        db.add(rel)
        db.flush()
    content = "\n\n".join(filter(None, [rel.content, body.content.strip()]))
    if len(content.encode('utf-8')) > 65000:
        raise HTTPException(400, detail="追加后上线内容超出容量，请精简本次内容后重试")
    rel.content = content
    rel.req_count = (rel.req_count or 0) + len(tasks)
    for task in tasks:
        task.release_record_id = rel.id
    db.commit()
    return ok({"release": _to_out(db, rel), "added": len(tasks)})
