"""对话测评结果导出:飞书表 + multica 推送(异常会话)。

飞书:导出到用户指定表(eval 平台生成、无飞书来源锚点,故导出非回填原表)。
multica:推 is_abnormal 且未 pushed 的 run,回写 pushed_multica/multica_ref 防重推。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import EvalRun, User
from app.schemas.common import ok
from app.schemas.eval_export import EvalExportFeishuIn, EvalPushMulticaIn
from app.services import feishu, multica

router = APIRouter(prefix="/api/eval-export", tags=["eval-export"])
_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)

# 导出飞书的默认列映射(字段→列)。沿用 CLI 五列 + 平台判定列。
_FEISHU_COL_MAP = {
    "share_link": "C", "artifact_share_link": "D", "reported_duration": "E",
    "bean_cost": "F", "answer": "H", "verdict": "J", "verdict_reason": "K", "is_abnormal": "L",
}


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
        rows.append({
            "share_link": r.share_link or "", "artifact_share_link": r.artifact_share_link or "",
            "reported_duration": r.reported_duration or "", "bean_cost": r.bean_cost or "",
            "answer": r.answer or "", "verdict": r.verdict or "",
            "verdict_reason": r.verdict_reason or "", "is_abnormal": "是" if r.is_abnormal else "否",
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
                                 EvalRun.is_abnormal == True,  # noqa: E712
                                 EvalRun.pushed_multica == False)  # noqa: E712
    if body.batch_id:
        q = q.filter(EvalRun.batch_id == body.batch_id)
    runs = q.order_by(EvalRun.id).all()
    pushed, results = 0, []
    for r in runs:
        try:
            ref = multica.push_abnormal_run(r)
            if ref is None:
                results.append({"run_id": r.id, "skipped": "multica 未配置(MULTICA_MODE=off)"})
                continue
            r.pushed_multica = True
            r.multica_ref = str(ref)[:512]
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
