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
    votes: int = 1   # 1=单票(默认);3/5=稳健多数决(N 倍引擎耗时)


class JudgeBatchIn(BaseModel):
    project_id: int
    run_ids: list[int] | None = None   # 指定;为空则判该项目所有 done 的 run
    provider: str | None = None
    votes: int = 1


def _run_out(r: EvalRun) -> dict:
    import json
    return {
        "run_id": r.id, "eval_query_id": r.eval_query_id, "project_id": r.project_id,
        "status": getattr(r.status, "value", r.status), "verdict": r.verdict,
        "score": r.score,
        "verdict_dims": json.loads(r.verdict_dims) if r.verdict_dims else None,
        "verdict_reason": r.verdict_reason, "judged_by": r.judged_by,
        "review_mark": r.review_mark, "review_note": r.review_note,
        "is_abnormal": bool(r.is_abnormal), "share_link": r.share_link, "answer": r.answer,
    }


# ⚠️ /batch 必须注册在 /{run_id} 之前:FastAPI 按注册顺序匹配,若 /{run_id} 在前,
# POST /eval-judge/batch 会被它捕获、把 "batch" 当 run_id 转 int → 422 参数校验失败
# (此前顺序反了,批量判定端点上线以来从未被真正命中过)。
@router.post("/batch")
def judge_batch(body: JudgeBatchIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """批量判定改入队(方案2 P2):可判的每条 run 各建一个 eval_judge job,worker 池并发消费,
    返回 {job_ids, count, skipped}。前端轮询这批 job 汇总。pending/running/cancelled 不可判→skipped。"""
    from app.core.enums import EvalRunStatus
    from app.services import ai_jobs

    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    q = db.query(EvalRun).filter(EvalRun.project_id == body.project_id)
    if body.run_ids:
        q = q.filter(EvalRun.id.in_(body.run_ids))
    else:
        q = q.filter(EvalRun.status == EvalRunStatus.done)
    rows = q.all()
    job_ids, skipped = [], []
    for r in rows:
        st = getattr(r.status, "value", r.status)
        if st not in ("done", "judged"):
            skipped.append({"run_id": r.id, "reason": f"状态 {st},不判定"})
            continue
        job = ai_jobs.enqueue(db, "eval_judge", provider=body.provider, project_id=body.project_id,
                              user_id=user.id, input={"run_id": r.id, "provider": body.provider,
                                                      "votes": body.votes},
                              ref_kind="eval_run", ref_id=r.id)
        job_ids.append(job.id)
    return ok({"job_ids": job_ids, "count": len(job_ids), "skipped": skipped})


class NotifyBatchDoneIn(BaseModel):
    project_id: int
    task_id: int | None = None   # 单任务重判传它 → 通知带该任务在线报告链接;跨任务/项目级不传
    judged: int = 0              # 本批实际判定条数(前端轮询汇总)
    failed: int = 0              # 判定失败/跳过条数


@router.post("/notify-batch-done")
def notify_batch_done(body: NotifyBatchDoneIn, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """手动批量判定「整批完成」后由前端补推的推推通知(带在线报告链接)。

    批量判定是 N 个异步 job、后端无「整批完成」钩子,故完成时机由前端 pollAiJobs 汇总后回调此端点。
    有 task_id → 拼该任务在线报告 /r/<code>(实时渲染,含最新判定明细);无则不带链接。
    通知失败/无开关都不报错(与 notify_eval_pipeline 一致:NOTIFY_EVAL_PIPELINE 关则静默)。
    """
    from app.models import EvalTask
    from app.services import notify
    from app.core.config import settings

    assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    task_name = "手动批量判定"
    share_url = None
    if body.task_id:
        task = db.get(EvalTask, body.task_id)
        if task and task.project_id == body.project_id:
            task_name = task.name
            base = (settings.PLATFORM_BASE_URL or "").rstrip("/")
            if base and task.summary_share_code:
                share_url = f"{base}/r/{task.summary_share_code}"
    lines = [f"已判定 {body.judged} 条" + (f",其中 {body.failed} 条失败/跳过" if body.failed else "")]
    if share_url:
        lines.append(f"在线报告: {share_url}")
    lines.append("如需按新标准更新报告,请在平台重新生成综合评价。")
    notify.notify_eval_pipeline(task_name, body.project_id, "✅ 已完成批量判定(手动)", lines, "blue")
    return ok({"notified": True, "share_url": share_url})


def _batch_judge_results(db: Session, project_id: int, run_ids: list[int] | None = None,
                         batch_id: str | None = None, provider: str | None = None,
                         votes: int = 1) -> list[dict]:
    """批量判定核心(端点与一条龙编排共用):判定范围内每条 done/judged 的 run,单条失败不断批。

    范围:run_ids 指定 → 这些;否则 batch_id 指定 → 该批;都无 → 该项目所有 done。
    返回逐条结果列表(含 skipped/error 回执)。调用方负责鉴权与 commit(judge_run 内部落库)。
    """
    from app.core.enums import EvalRunStatus

    q = db.query(EvalRun).filter(EvalRun.project_id == project_id)
    if run_ids:
        q = q.filter(EvalRun.id.in_(run_ids))
    elif batch_id:
        q = q.filter(EvalRun.batch_id == batch_id)
    else:
        q = q.filter(EvalRun.status == EvalRunStatus.done)
    rows = q.all()
    if not rows:
        return []
    results = []
    for r in rows:
        st = getattr(r.status, "value", r.status)
        if st not in ("done", "judged"):
            results.append({"run_id": r.id, "skipped": True, "reason": f"状态 {st},不判定"})
            continue
        try:
            res = eval_judge.judge_run(db, r, provider=provider, votes=votes)
            results.append({"run_id": r.id, **res})
        except Exception as e:  # noqa: BLE001 单条失败不断批
            results.append({"run_id": r.id, "error": str(e)})
    return results


def _run_batch_judge(db: Session, project_id: int, batch_id: str, provider: str | None = None) -> int:
    """一条龙用:判定某批次全部可判 run,返回实际判定(非 skipped/error)条数。"""
    results = _batch_judge_results(db, project_id, batch_id=batch_id, provider=provider)
    return sum(1 for r in results if not r.get("skipped") and not r.get("error"))


@router.post("/{run_id}")
def judge_one(run_id: int, body: JudgeIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单条判定改入队(方案2 P2):建 eval_judge job 立即返回 {job_id},前端轮询取判定结果。"""
    from app.services import ai_jobs

    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    job = ai_jobs.enqueue(db, "eval_judge", provider=body.provider, project_id=r.project_id,
                          user_id=user.id, input={"run_id": run_id, "provider": body.provider,
                                                  "votes": body.votes},
                          ref_kind="eval_run", ref_id=run_id)
    return ok({"job_id": job.id})


class ReviewMarkIn(BaseModel):
    # confirmed=认可判定 / false_positive=误报(判fail实际OK) / false_negative=漏报(判pass实际有问题) / None=清除
    mark: str | None = None
    note: str | None = None


@router.post("/{run_id}/review")
def review_run(run_id: int, body: ReviewMarkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """人工复核标注(失败收敛):对 AI 判定标 误报/漏报/认可,沉淀 judge 盲区、驱动题目期望迭代。

    误报(false_positive)顺带摘掉 is_abnormal(不再推 multica/不计异常);
    漏报(false_negative)反向置真。verdict 本身不改——保留 AI 原判供对照统计。
    """
    r = db.get(EvalRun, run_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="执行项不存在")
    assert_project_role(db, user, r.project_id, _WRITE_ROLES)
    if not r.verdict:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="该执行项还没有 AI 判定,先判定再复核")
    if body.mark is not None and body.mark not in ("confirmed", "false_positive", "false_negative"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="mark 须为 confirmed/false_positive/false_negative 或 null")
    r.review_mark = body.mark
    r.review_note = (body.note or "").strip() or None
    if body.mark == "false_positive":
        r.is_abnormal = False
    elif body.mark == "false_negative":
        r.is_abnormal = True
    db.commit(); db.refresh(r)
    return ok(_run_out(r))


@router.get("/abnormal")
def list_abnormal(project_id: int = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))
    rows = (db.query(EvalRun)
            .filter(EvalRun.project_id == project_id, EvalRun.is_abnormal == True)  # noqa: E712
            .order_by(EvalRun.id.desc()).all())
    return ok([_run_out(r) for r in rows])


@router.get("/judge-quality")
def judge_quality(project_id: int = Query(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """判定质量统计(evaluator alignment):用人工复核标注反推 AI 判定准不准、哪个引擎更靠谱。

    对齐 LangSmith 的 evaluator alignment——judge 本身也需要被评测。口径:
    - 复核覆盖率 = 已复核 / 已判定(样本太少时准确率不可信,前端据此提示)
    - 判定准确率 = confirmed / 已复核(误报+漏报都算判错)
    - 误报率/漏报率 = false_positive|false_negative / 已复核
    按 judged_by 分组做引擎横评(稳健多票的 providerxN 单独成组,正好看多票是否更准)。
    """
    from sqlalchemy import case, func

    assert_project_role(db, user, project_id, (ProjectRole.admin, ProjectRole.member, ProjectRole.guest))

    def _agg(*extra_filters):
        q = db.query(
            func.count(EvalRun.id).label("judged"),
            func.sum(case((EvalRun.review_mark.isnot(None), 1), else_=0)).label("reviewed"),
            func.sum(case((EvalRun.review_mark == "confirmed", 1), else_=0)).label("confirmed"),
            func.sum(case((EvalRun.review_mark == "false_positive", 1), else_=0)).label("fp"),
            func.sum(case((EvalRun.review_mark == "false_negative", 1), else_=0)).label("fn"),
        ).filter(EvalRun.project_id == project_id, EvalRun.verdict.isnot(None))
        for f in extra_filters:
            q = q.filter(f)
        return q.one()

    def _shape(row, name=None):
        judged, reviewed = int(row.judged or 0), int(row.reviewed or 0)
        confirmed, fp, fn = int(row.confirmed or 0), int(row.fp or 0), int(row.fn or 0)
        pct = lambda n: round(n / reviewed * 100, 1) if reviewed else None  # noqa: E731
        out = {
            "judged": judged, "reviewed": reviewed,
            "review_rate": round(reviewed / judged * 100, 1) if judged else None,
            "confirmed": confirmed, "false_positive": fp, "false_negative": fn,
            "accuracy": pct(confirmed), "fp_rate": pct(fp), "fn_rate": pct(fn),
        }
        if name is not None:
            out["engine"] = name
        return out

    overall = _shape(_agg())
    # 引擎横评:只列有复核样本的引擎(没样本算不出准确率,列出来是噪音)
    engines = [n for (n,) in db.query(EvalRun.judged_by)
               .filter(EvalRun.project_id == project_id, EvalRun.verdict.isnot(None),
                       EvalRun.judged_by.isnot(None), EvalRun.review_mark.isnot(None))
               .distinct().all()]
    by_engine = sorted(
        (_shape(_agg(EvalRun.judged_by == n), n) for n in engines),
        key=lambda e: (-(e["reviewed"] or 0), e["engine"] or ""),
    )
    return ok({"overall": overall, "by_engine": by_engine})


@router.get("/dimension-stats")
def eval_dimension_stats(
    project_id: int = Query(...),
    days: int = 30,
    by_engine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """对话测评维度通过率聚合:以 EvalQuery.dimension 为轴,统计 verdict=pass/fail 的通过率。

    time window: [today-days+1, today];  error/NULL verdict 不计。
    dimension 为空归入"未标注"。dims 按 total 降序。overall_rate 为加权均值。
    by_engine=True 时额外按 target_engine 分组(多产品横评),each 引擎一份分维通过率。
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

    result = {"days": days, "dims": dims, "judged_total": judged_total, "overall_rate": overall_rate}

    # by_engine:按 target_engine 分组各算一份分维通过率(多产品横评)。仿 judge-quality 的 by_engine。
    if by_engine:
        from app.services.eval_engines import EVAL_ENGINES
        eng_rows = (
            db.query(EvalRun.target_engine, EvalQuery.dimension, EvalRun.verdict, func.count(EvalRun.id))
            .join(EvalQuery, EvalQuery.id == EvalRun.eval_query_id)
            .filter(
                EvalRun.project_id == project_id,
                EvalRun.verdict.in_(["pass", "fail"]),
                EvalRun.target_engine.isnot(None),
                func.date(EvalRun.created_at) >= d_from,
                func.date(EvalRun.created_at) <= today,
            )
            .group_by(EvalRun.target_engine, EvalQuery.dimension, EvalRun.verdict)
            .all()
        )
        eng_agg: dict[str, dict] = {}   # engine -> {dim: {total, passed}}
        for eng, dim, verdict, cnt in eng_rows:
            bucket = eng_agg.setdefault(eng, {}).setdefault(dim or "未标注", {"total": 0, "passed": 0})
            bucket["total"] += cnt
            if verdict == "pass":
                bucket["passed"] += cnt
        by_engine_out = []
        for eng, dim_map in eng_agg.items():
            e_dims = sorted(
                [{"dimension": k, "total": v["total"], "passed": v["passed"],
                  "pass_rate": round(v["passed"] / v["total"] * 100, 1) if v["total"] else 0.0}
                 for k, v in dim_map.items()],
                key=lambda x: (-x["total"], x["dimension"]),
            )
            e_total = sum(v["total"] for v in dim_map.values())
            e_pass = sum(v["passed"] for v in dim_map.values())
            by_engine_out.append({
                "engine": eng, "label": EVAL_ENGINES.get(eng, {}).get("label", eng),
                "dims": e_dims, "judged_total": e_total,
                "overall_rate": round(e_pass / e_total * 100, 1) if e_total else 0.0,
            })
        by_engine_out.sort(key=lambda e: (-e["judged_total"], e["engine"]))
        result["by_engine"] = by_engine_out

    return ok(result)
