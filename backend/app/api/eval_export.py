"""对话测评结果导出:飞书表 + multica 推送(异常会话)。

飞书:导出到用户指定表(eval 平台生成、无飞书来源锚点,故导出非回填原表)。
multica:按 run_ids 推送正常或异常结果;旧调用仍只推异常。回写推送状态防重推。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session, load_only

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import EvalRun, EvalQuery, User
from app.core.config import settings
from app.schemas.common import ok
from app.schemas.eval_export import EvalExportFeishuIn, EvalPushMulticaIn, EvalMulticaRetestIn
from app.services import feishu, multica

router = APIRouter(prefix="/api/eval-export", tags=["eval-export"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)

# 导出飞书的默认列映射(字段→列)。沿用 CLI 五列 + 平台判定列。
# 2026-08 补列:score 评分 / review 复核标注 / group A-B 组 / dimension 维度(后两列取自 payload 快照)。
_FEISHU_COL_MAP = {
    "share_link": "C", "artifact_share_link": "D", "reported_duration": "E",
    "bean_cost": "F", "answer": "H", "verdict": "J", "verdict_reason": "K", "is_abnormal": "L",
    "score": "M", "review": "N", "group": "O", "dimension": "P",
}
_REVIEW_LABEL = {"confirmed": "认可判定", "false_positive": "误报(实际通过)",
                 "false_negative": "漏报(实际有问题)"}


def _query_runs(db, project_id, batch_id=None, abnormal_only=False):
    q = db.query(EvalRun).filter(EvalRun.project_id == project_id)
    if batch_id:
        q = q.filter(EvalRun.batch_id == batch_id)
    if abnormal_only:
        q = q.filter(EvalRun.is_abnormal == True)  # noqa: E712
    return q.order_by(EvalRun.id).all()


@router.post("/feishu")
def export_feishu(body: EvalExportFeishuIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if not feishu.is_configured():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="未配置飞书应用凭据(FEISHU_APP_ID/FEISHU_APP_SECRET)")
    runs = _query_runs(db, body.project_id, body.batch_id, body.abnormal_only)
    rows = []
    for r in runs:
        try:
            payload = json.loads(r.payload) if r.payload else {}
        except (ValueError, TypeError):
            payload = {}
        rows.append({
            "share_link": r.share_link or "", "artifact_share_link": r.artifact_share_link or "",
            "reported_duration": r.reported_duration or "", "bean_cost": r.bean_cost or "",
            "answer": r.answer or "", "verdict": r.verdict or "",
            "verdict_reason": r.verdict_reason or "", "is_abnormal": "是" if r.is_abnormal else "否",
            "score": str(r.score) if r.score is not None else "",
            "review": _REVIEW_LABEL.get(r.review_mark) or "",
            "group": (payload.get("compare_group") or "") if payload else "",
            "dimension": (payload.get("dimension") or "") if payload else "",
        })
    try:
        n = feishu.write_sheet_rows(body.sheet_url, rows, _FEISHU_COL_MAP, body.start_row)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return ok({"exported": n, "sheet_url": body.sheet_url})


@router.post("/multica")
def push_multica(body: EvalPushMulticaIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id,
                                 EvalRun.pushed_multica == False)  # noqa: E712
    if body.run_ids is not None:
        q = q.filter(EvalRun.id.in_(body.run_ids))
    else:
        # 兼容旧调用:未指定勾选项仍只推异常,不能意外全量推送。
        q = q.filter(EvalRun.is_abnormal == True)  # noqa: E712
    if body.batch_id:
        q = q.filter(EvalRun.batch_id == body.batch_id)
    runs = q.order_by(EvalRun.id).all()
    if runs and settings.MULTICA_MODE.lower() == "skill":
        try:
            multica.check_skill_ready()
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    pushed, results = 0, []
    for r in runs:
        try:
            query = db.get(EvalQuery, r.eval_query_id) if r.eval_query_id else None
            ref = multica.push_abnormal_run(r, query=query)
            if ref is None:
                results.append({"run_id": r.id, "skipped": "multica 未配置(MULTICA_MODE=off)"})
                continue
            r.pushed_multica = True
            r.multica_ref = str(ref)[:512]
            r.multica_pushed_at = multica.push_time()
            db.commit()
            pushed += 1
            results.append({"run_id": r.id, "ref": ref})
        except Exception as e:  # noqa: BLE001 单条失败不断批
            db.rollback()
            results.append({"run_id": r.id, "error": str(e)})
    return ok({"pushed": pushed, "candidates": len(runs), "results": results})


@router.get("/multica-pending")
def multica_pending(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    n = db.query(EvalRun).filter(EvalRun.project_id == project_id,
                                 EvalRun.is_abnormal == True,  # noqa: E712
                                 EvalRun.pushed_multica == False).count()  # noqa: E712
    return ok({"pending": n})


_MULTICA_FIELDS = ("id", "eval_query_id", "eval_task_id", "batch_id", "target_engine", "status", "payload",
    "verdict", "score", "verdict_reason", "reason", "multica_ref", "multica_pushed_at", "created_at",
    "multica_retest_source_id", "share_link", "reported_duration", "bean_cost")


def _multica_item(run):
    from app.services.eval_snapshot import payload_of
    return {"run_id": run.id, **{k: getattr(run, k) for k in _MULTICA_FIELDS if k not in ("id", "status", "payload")},
            "status": getattr(run.status, "value", run.status), "payload": payload_of(run)}


@router.get("/multica-results")
def multica_results(project_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    search: str = Query("", max_length=200), target_engine: str | None = None,
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    q = db.query(EvalRun).filter(EvalRun.project_id == project_id, EvalRun.pushed_multica.is_(True))
    if search.strip():
        term = search.strip()
        q = q.filter(or_(EvalRun.payload.contains(term, autoescape=True),
                         EvalRun.multica_ref.contains(term, autoescape=True),
                         EvalRun.id == (int(term) if term.isdecimal() and len(term) < 12 else -1)))
    if target_engine:
        q = q.filter(EvalRun.target_engine == target_engine if target_engine != "namiwork" else
                     or_(EvalRun.target_engine == "namiwork", EvalRun.target_engine.is_(None)))
    total = q.count()
    rows = q.options(load_only(*(getattr(EvalRun, f) for f in _MULTICA_FIELDS))).order_by(
        EvalRun.multica_pushed_at.desc(), EvalRun.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    ids = [r.id for r in rows]
    stats = db.query(EvalRun.multica_retest_source_id, func.max(EvalRun.id), func.count(EvalRun.id),
        func.sum(case((EvalRun.status.in_(['pending', 'running', 'judging']), 1), else_=0))).filter(
        EvalRun.project_id == project_id, EvalRun.multica_retest_source_id.in_(ids)).group_by(EvalRun.multica_retest_source_id).all() if ids else []
    latest = {r.id: r for r in db.query(EvalRun).options(load_only(*(getattr(EvalRun, f) for f in _MULTICA_FIELDS))).filter(
        EvalRun.id.in_([x[1] for x in stats]), EvalRun.project_id == project_id).all()} if stats else {}
    by_source = {sid: {"latest_retest": _multica_item(latest[rid]), "retest_count": count, "active_retests": active}
                 for sid, rid, count, active in stats}
    return ok({"items": [{**_multica_item(r), **by_source.get(r.id, {"latest_retest": None, "retest_count": 0, "active_retests": 0})} for r in rows],
               "total": total, "page": page, "page_size": page_size})


@router.get("/multica-results/{run_id}/retests")
def multica_retest_history(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.api.eval_queue import _to_out
    row = db.get(EvalRun, run_id)
    if not row:
        raise HTTPException(404, detail="测评结果不存在")
    assert_project_role(db, user, row.project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    if not row.pushed_multica:
        raise HTTPException(400, detail="该结果尚未推送 Multica")
    retests = db.query(EvalRun).options(load_only(*(getattr(EvalRun, f) for f in _MULTICA_FIELDS))).filter(
        EvalRun.project_id == row.project_id, EvalRun.multica_retest_source_id == row.id).order_by(EvalRun.id.desc()).all()
    return ok({"source": _to_out(row), "retests": [_multica_item(r) for r in retests]})


@router.post("/multica-retest")
def multica_retest(body: EvalMulticaRetestIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    from app.services.eval_multica_retest import create_retest
    try:
        return ok(create_retest(db, body, user.id))
    except Exception:
        db.rollback()
        raise
