"""对话测评判定路由:触发判定(单条/批量)、异常会话列表。

判定是平台侧动作(读 trace + 调引擎),用户 JWT 鉴权(区别于 runner)。
判定逻辑在 services/eval_judge.judge_run。异常会话(is_abnormal)供子项4 推 multica。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import EvalRun, User
from app.schemas.common import ok
from app.services import eval_judge

router = APIRouter(prefix="/api/eval-judge", tags=["eval-judge"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


class JudgeIn(BaseModel):
    provider: str | None = None


class JudgeBatchIn(BaseModel):
    project_id: int
    run_ids: list[int] | None = None   # 指定;为空则判该项目所有 done 的 run
    provider: str | None = None


def _run_out(r: EvalRun) -> dict:
    import json
    return {
        "run_id": r.id, "eval_query_id": r.eval_query_id, "project_id": r.project_id,
        "status": getattr(r.status, "value", r.status), "verdict": r.verdict,
        "verdict_dims": json.loads(r.verdict_dims) if r.verdict_dims else None,
        "verdict_reason": r.verdict_reason, "judged_by": r.judged_by,
        "is_abnormal": bool(r.is_abnormal), "share_link": r.share_link, "answer": r.answer,
    }


@router.post("/{run_id}")
def judge_one(run_id: int, body: JudgeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    eval_judge.judge_run(db, r, provider=body.provider)
    db.refresh(r)
    return ok(_run_out(r))


@router.post("/batch")
def judge_batch(body: JudgeBatchIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id)
    if body.run_ids:
        q = q.filter(EvalRun.id.in_(body.run_ids))
    else:
        from app.core.enums import EvalRunStatus
        q = q.filter(EvalRun.status == EvalRunStatus.done)
    rows = q.all()
    results = []
    for r in rows:
        try:
            res = eval_judge.judge_run(db, r, provider=body.provider)
            results.append({"run_id": r.id, **res})
        except Exception as e:  # noqa: BLE001 单条失败不断批
            results.append({"run_id": r.id, "error": str(e)})
    return ok({"judged": len(results), "results": results})


@router.get("/abnormal")
def list_abnormal(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalRun)
            .filter(EvalRun.project_id == project_id, EvalRun.is_abnormal == True)  # noqa: E712
            .order_by(EvalRun.id.desc()).all())
    return ok([_run_out(r) for r in rows])
