import base64
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import (AiJob, AiTask, Project, RequirementAnalysis, RequirementBaseline,
                        RequirementSource, Task, User)
from app.schemas.common import ok
from app.schemas.requirement_analysis import (RequirementAnalyzeIn, RequirementConfirmIn,
                                              RequirementDraft, RequirementDraftIn)
from app.services import ai_jobs, generators
from app.services.requirement_analysis import (confirmation_errors, coverage, encode, source_hash,
                                               validate_evidence)
from app.services.requirement_sources import public_materials

router = APIRouter(prefix="/api/ai/requirements", tags=["requirement-analysis"])
READ = (ProjectRole.admin, ProjectRole.member, ProjectRole.guest)
WRITE = (ProjectRole.admin, ProjectRole.member)


def get_analysis(db, user, aid, write=False):
    row = db.get(RequirementAnalysis, aid)
    if row is None:
        raise HTTPException(404, "需求分析不存在")
    assert_project_role(db, user, row.project_id, WRITE if write else READ)
    return row


def authorize_source(db, user, source, project_id=None):
    if source is None:
        raise HTTPException(404, "原始资料不存在，请重新导入")
    if source.created_by == user.id or user.is_platform_admin:
        return
    analyses = db.query(RequirementAnalysis).filter_by(source_id=source.id)
    if project_id:
        analyses = analyses.filter_by(project_id=project_id)
    for analysis in analyses.all():
        try:
            assert_project_role(db, user, analysis.project_id, READ)
            return
        except HTTPException:
            continue
    raise HTTPException(403, "无权读取该需求资料")


def to_out(db, row, detail=True):
    job = db.get(AiJob, row.job_id) if row.job_id else None
    baseline = db.query(RequirementBaseline).filter_by(analysis_id=row.id, revision=row.revision).first()
    result = {"id": row.id, "project_id": row.project_id, "task_id": row.task_id, "revision": row.revision,
              "source_id": row.source_id, "source_hash": row.source_hash, "source_url": row.source_url,
              "source_title": row.source_title, "provider": row.provider, "job_id": row.job_id,
              "status": "done" if row.draft else job.status if job else "failed",
              "error": job.error if job and not row.draft else None,
              "baseline_id": baseline.id if baseline else None,
              "confirmed_by": baseline.confirmed_by if baseline else None,
              "confirmation_note": baseline.confirmation_note if baseline else "",
              "confirmed_at": baseline.created_at.isoformat() if baseline else None,
              "created_at": row.created_at.isoformat() if row.created_at else None}
    if detail:
        result.update(source_text=row.source_text, source_info=json.loads(row.source_info),
                      visual_readings=json.loads(row.visual_readings or "[]"), draft=json.loads(row.draft) if row.draft else None)
        source = db.get(RequirementSource, row.source_id) if row.source_id else None
        result["materials"] = public_materials(json.loads(source.materials)) if source else []
    return result


@router.post("/analyze")
def analyze(body: RequirementAnalyzeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, WRITE)
    task = db.get(Task, body.task_id)
    if not db.get(Project, body.project_id) or not task or task.project_id != body.project_id:
        raise HTTPException(400, "请选择当前项目的关联任务")
    provider = generators.normalize_provider(body.provider)
    if not generators.get_provider(provider).is_available():
        raise HTTPException(503, "所选分析引擎不可用")
    warnings = [str(w)[:1000] for w in body.source_warnings]
    source = db.get(RequirementSource, body.source_id) if body.source_id else None
    if body.source_id:
        authorize_source(db, user, source, body.project_id)
        warnings = json.loads(source.warnings)
    row = RequirementAnalysis(project_id=body.project_id, task_id=body.task_id, created_by=user.id,
                              source_text=body.requirement, source_hash=source_hash(body.requirement), source_id=body.source_id,
                              source_url=source.url if source else body.source_url,
                              source_title=source.title if source else body.source_title,
                              source_info=encode({"input_type": body.input_type, "warnings": warnings}), provider=provider)
    db.add(row)
    db.flush()
    job = ai_jobs.enqueue(db, "requirement_analysis", provider=provider, project_id=body.project_id,
                         user_id=user.id, input={"analysis_id": row.id}, ref_kind="requirement_analysis", ref_id=row.id)
    row.job_id = job.id
    db.commit()
    return ok({"analysis_id": row.id, "job_id": job.id})


@router.get("/analyses")
def analyses(project_id: int, task_id: int | None = None, limit: int = Query(20, ge=1, le=100),
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, READ)
    query = db.query(RequirementAnalysis).filter_by(project_id=project_id)
    if task_id is not None:
        query = query.filter_by(task_id=task_id)
    return ok([to_out(db, row, False) for row in query.order_by(RequirementAnalysis.id.desc()).limit(limit).all()])


@router.get("/analyses/{aid}")
def analysis_detail(aid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return ok(to_out(db, get_analysis(db, user, aid)))


@router.patch("/analyses/{aid}")
def save_draft(aid: int, body: RequirementDraftIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = get_analysis(db, user, aid, True)
    if not row.draft or row.revision != body.revision:
        raise HTTPException(409, "分析尚未完成或内容已被其他人修改，请重新加载")
    old = RequirementDraft.model_validate_json(row.draft)
    if {r.id for r in old.rules} - {r.id for r in body.draft.rules}:
        raise HTTPException(422, "规则不能直接删除，请标记本期排除并记录原因")
    if {q.id for q in old.questions} - {q.id for q in body.draft.questions}:
        raise HTTPException(422, "澄清问题不能直接删除，请填写处理结论")
    try:
        draft = validate_evidence(body.draft, row.source_text, json.loads(row.visual_readings))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    serialized = encode(draft.model_dump())
    if json.loads(serialized) != json.loads(row.draft):
        result = db.execute(update(RequirementAnalysis).where(RequirementAnalysis.id == aid,
                            RequirementAnalysis.revision == body.revision).values(draft=serialized, revision=body.revision + 1))
        if result.rowcount != 1:
            raise HTTPException(409, "内容已更新，请重新加载后再保存")
        db.commit()
        db.refresh(row)
    return ok(to_out(db, row))


@router.post("/analyses/{aid}/confirm")
def confirm(aid: int, body: RequirementConfirmIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = get_analysis(db, user, aid, True)
    # Serialize confirmation with draft edits; the conditional revision update is
    # also a write lock on SQLite and MySQL, unlike a read-only version check.
    locked = db.execute(update(RequirementAnalysis).where(RequirementAnalysis.id == aid,
                        RequirementAnalysis.revision == body.revision).values(revision=body.revision))
    if locked.rowcount != 1 or not row.draft or row.source_hash != body.source_hash:
        raise HTTPException(409, "需求内容或规则版本已变化，请重新加载确认")
    db.refresh(row)
    if not body.scope_reviewed:
        raise HTTPException(422, "请核对本期范围、资料完整性和规则后再确认")
    visuals = json.loads(row.visual_readings)
    warnings = json.loads(row.source_info).get("warnings", [])
    if (warnings or any(v["status"] != "read" for v in visuals)) and not body.confirmation_note:
        raise HTTPException(422, "存在未读取或不确定的资料，请说明如何补充或排除其影响")
    draft = RequirementDraft.model_validate_json(row.draft)
    errors = confirmation_errors(draft, visuals)
    if errors:
        raise HTTPException(422, "；".join(errors))
    baseline = db.query(RequirementBaseline).filter_by(analysis_id=aid, revision=row.revision).first()
    if not baseline:
        baseline = RequirementBaseline(analysis_id=aid, revision=row.revision, confirmed_by=user.id,
                                       payload=encode(draft.model_dump()), confirmation_note=body.confirmation_note)
        db.add(baseline)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            baseline = db.query(RequirementBaseline).filter_by(analysis_id=aid, revision=body.revision).one()
    else:
        db.commit()
    return ok({"baseline_id": baseline.id, "revision": baseline.revision, "confirmed_by": baseline.confirmed_by})


@router.get("/coverage/{ai_task_id}")
def generation_coverage(ai_task_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(AiTask, ai_task_id)
    if not task:
        raise HTTPException(404, "生成任务不存在")
    assert_project_role(db, user, task.project_id, READ)
    return ok(coverage(db, ai_task_id))


@router.get("/sources/{sid}/images/{mid}")
def source_image(sid: int, mid: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    source = db.get(RequirementSource, sid)
    authorize_source(db, user, source)
    item = next((m for m in json.loads(source.materials) if m["id"] == mid), None)
    if not item or not item.get("data"):
        raise HTTPException(404, "该图片未成功获取")
    return Response(base64.b64decode(item["data"]), media_type=item["mime_type"],
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
