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


# ⚠️ /batch 必须注册在 /{run_id} 之前:FastAPI 按注册顺序匹配,若 /{run_id} 在前,
# POST /eval-judge/batch 会被它捕获、把 "batch" 当 run_id 转 int → 422 参数校验失败
# (此前顺序反了,批量判定端点上线以来从未被真正命中过)。
@router.post("/batch")
def judge_batch(body: JudgeBatchIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id)
    if body.run_ids:
        # 空列表等同于未指定，避免 in_([]) 在部分数据库下报错
        q = q.filter(EvalRun.id.in_(body.run_ids))
    else:
        from app.core.enums import EvalRunStatus
        q = q.filter(EvalRun.status == EvalRunStatus.done)
    rows = q.all()
    if not rows:
        return ok({"judged": 0, "results": []})
    results = []
    for r in rows:
        st = getattr(r.status, "value", r.status)
        if st in ("pending", "running"):
            # 未执行完/未回填的不判(前端只传 done,这里防手工调用或状态漂移时误判),明确回执原因
            results.append({"run_id": r.id, "skipped": True, "reason": f"状态 {st},尚未执行完成"})
            continue
        try:
            res = eval_judge.judge_run(db, r, provider=body.provider)
            results.append({"run_id": r.id, **res})
        except Exception as e:  # noqa: BLE001 单条失败不断批
            results.append({"run_id": r.id, "error": str(e)})
    return ok({"judged": len(results), "results": results})


@router.post("/{run_id}")
def judge_one(run_id: int, body: JudgeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    eval_judge.judge_run(db, r, provider=body.provider)
    db.refresh(r)
    return ok(_run_out(r))


@router.get("/abnormal")
def list_abnormal(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalRun)
            .filter(EvalRun.project_id == project_id, EvalRun.is_abnormal == True)  # noqa: E712
            .order_by(EvalRun.id.desc()).all())
    return ok([_run_out(r) for r in rows])


@router.get("/dimension-stats")
def eval_dimension_stats(
    project_id: int = Query(...),
    days: int = 30,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对话测评维度通过率聚合:以 EvalQuery.dimension 为轴,统计 verdict=pass/fail 的通过率。

    time window: [today-days+1, today];  error/NULL verdict 不计。
    dimension 为空归入"未标注"。dims 按 total 降序。overall_rate 为加权均值。
    """
    from datetime import date, timedelta
    from app.models.ai_eval import EvalQuery, EvalRun
    from sqlalchemy import func

    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    if days <= 0 or days > 365:
        days = 30
    today = date.today()
    d_from = today - timedelta(days=days - 1)

    rows = (
        db.query(EvalQuery.dimension, EvalRun.verdict, func.count(EvalRun.id))
        .join(EvalQuery, EvalQuery.id == EvalRun.eval_query_id)
        .filter(
            EvalRun.project_id == project_id,
            EvalRun.verdict.in_(["pass", "fail"]),
            func.date(EvalRun.created_at) >= d_from,
            func.date(EvalRun.created_at) <= today,
        )
        .group_by(EvalQuery.dimension, EvalRun.verdict)
        .all()
    )

    agg: dict[str, dict] = {}
    for dim, verdict, cnt in rows:
        key = dim or "未标注"
        bucket = agg.setdefault(key, {"total": 0, "passed": 0})
        bucket["total"] += cnt
        if verdict == "pass":
            bucket["passed"] += cnt

    dims = sorted(
        [
            {
                "dimension": k,
                "total": v["total"],
                "passed": v["passed"],
                "pass_rate": round(v["passed"] / v["total"] * 100, 1) if v["total"] else 0.0,
            }
            for k, v in agg.items()
        ],
        key=lambda x: (-x["total"], x["dimension"]),
    )

    judged_total = sum(d["total"] for d in dims)
    total_passed = sum(d["passed"] for d in dims)
    overall_rate = round(total_passed / judged_total * 100, 1) if judged_total else 0.0

    return ok({"days": days, "dims": dims, "judged_total": judged_total, "overall_rate": overall_rate})
