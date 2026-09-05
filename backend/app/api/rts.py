import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import RtsRecommendation, User
from app.schemas.common import ok
from app.services import ai_jobs, rts as rtssvc

router = APIRouter(prefix="/api/rts", tags=["rts"])


class AnalyzeBody(BaseModel):
    project_id: int
    release_id: int


@router.get("/candidates")
def candidates(release_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """该版本候选回归用例(风险分降序)。风险分现算不落表。"""
    ranked = rtssvc.rank_candidates(db, release_id)
    _, in_rel = rtssvc.cases_for_release(db, release_id)
    return ok({"items": ranked, "candidate_count": len(ranked), "in_release_count": len(in_rel)})


@router.post("/analyze")
def analyze(body: AnalyzeBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """入队 AI 风险叙事。pending 去重：同 release 已有未完成 job 则复用。"""
    assert_project_role(db, user, body.project_id, (ProjectRole.admin,))
    from app.models import AiJob
    existing = (db.query(AiJob)
                .filter(AiJob.kind == "rts", AiJob.ref_kind == "release",
                        AiJob.ref_id == body.release_id, AiJob.status.in_(["pending", "running"]))
                .first())
    if existing:
        return ok({"job_id": existing.id, "reused": True})
    job = ai_jobs.enqueue(db, "rts", project_id=body.project_id, user_id=user.id,
                          input={"release_id": body.release_id},
                          ref_kind="release", ref_id=body.release_id)
    return ok({"job_id": job.id})


@router.get("/recommendation")
def recommendation(release_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """读该版本最新 AI 风险叙事；无则 {exists:false}。"""
    row = (db.query(RtsRecommendation).filter(RtsRecommendation.release_id == release_id)
           .order_by(RtsRecommendation.id.desc()).first())
    if not row:
        return ok({"exists": False})
    return ok({"exists": True, "overall_risk": row.overall_risk, "summary": row.summary,
               "rationale": row.rationale, "focus_points": json.loads(row.focus_points or "[]"),
               "candidate_count": row.candidate_count, "recommended_count": row.recommended_count,
               "provider": row.provider, "generated_at": str(row.generated_at)})
