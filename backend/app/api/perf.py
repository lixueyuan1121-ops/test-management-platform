"""性能测试路由：双轨采集结果的下发/回传/查询/在线报告。

两条数据轨（source）：
- dispatch：POST /jobs 下发(pending) → runner GET /queue 拉取 → POST /queue/{id}/claim
  认领(running) → PATCH /queue/{id} 回传(completed)。用户 JWT 下发，runner token 执行。
- upload：本地交互采集后 POST /queue/upload 一次性建 run 并填结果(runner token)。

查询/报告（用户 JWT）：
- GET /runs         历史列表（摘要，不含大字段 samples/events）
- GET /runs/{id}    单条详情（全量 meta/samples/events）
- GET /report       报告页数据源：返回 [{meta,samples,events}]，直接喂前端 report-logic
- DELETE /runs/{id} 删除（本人或平台管理员）

沿用全项目约定：{code,msg,data} 信封、手写 _to_out、体外 assert_project_role、
runner 侧 require_runner_ctx（设备 token 优先、共享 token 兜底，归属校验防冒充）。
状态/场景一律 VARCHAR，规避 MySQL 原生 ENUM 越界静默空串的坑。
"""
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user, RunnerCtx, require_runner_ctx
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import PerfRun, PerfReportSet, User
from app.schemas.common import ok
from app.schemas.perf import PerfDispatchIn, PerfReportIn, PerfUploadIn, PerfReportSetIn, PerfThresholdsIn, PerfPromptIn

router = APIRouter(prefix="/api/perf", tags=["perf"])

_WRITE_ROLES = (ProjectRole.admin, ProjectRole.member)


def _meta_of(r: PerfRun) -> dict | None:
    try:
        return json.loads(r.meta_json) if r.meta_json else None
    except (json.JSONDecodeError, ValueError):
        return None


def _to_out(r: PerfRun, full: bool = False) -> dict:
    meta = _meta_of(r)
    m = meta or {}
    out = {
        "id": r.id,
        "run_id": r.id,   # 与 exec_queue 对齐：runner/agent 统一用 run_id 取值
        "project_id": r.project_id,
        "report_set_id": r.report_set_id,
        "runner": r.runner,
        "scenario": r.scenario,
        "variant": r.variant,
        "proc": r.proc,
        "duration": r.duration,
        "source": r.source,
        "status": r.status,
        "outcome": r.outcome,
        "duration_ms": r.duration_ms,
        "error": r.error,
        "prompt": r.prompt,
        "signal_seq": r.signal_seq,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "enqueued_by": r.enqueued_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        # 摘要（列表页/卡片用，避免拉全量 samples）
        "summary": m.get("summary"),
        "durations": m.get("durations"),
        "capabilities": m.get("capabilities"),
        "sample_count": m.get("sampleCount"),
    }
    if full:
        out["meta"] = meta
        out["samples"] = json.loads(r.samples_json) if r.samples_json else []
        out["events"] = json.loads(r.events_json) if r.events_json else []
    return out


def _apply_result(r: PerfRun, *, outcome=None, meta=None, samples=None, events=None, error=None) -> None:
    """把 runner 回传/直传的采集结果写入 run，并从 meta 提取冗余字段（时长/起止）。"""
    if outcome is not None:
        r.outcome = outcome
    if meta is not None:
        r.meta_json = json.dumps(meta, ensure_ascii=False)
    if samples is not None:
        r.samples_json = json.dumps(samples, ensure_ascii=False)
    if events is not None:
        r.events_json = json.dumps(events, ensure_ascii=False)
    if error:
        r.error = error
    if meta:
        d = meta.get("durations") or {}
        tot = d.get("totalMs")
        if isinstance(tot, (int, float)):
            r.duration_ms = int(tot)
        sa, ea = meta.get("startedAt"), meta.get("endedAt")
        if isinstance(sa, (int, float)):
            r.started_at = datetime.utcfromtimestamp(sa / 1000)
        if isinstance(ea, (int, float)):
            r.ended_at = datetime.utcfromtimestamp(ea / 1000)
    # 有 error 或采集 outcome=failed 记为 failed，其余（含 timeout/interrupted 但有数据）记 completed
    r.status = "failed" if (error or outcome == "failed") else "completed"


# ==================== 用户侧（JWT）====================

@router.post("/jobs")
def dispatch_job(body: PerfDispatchIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """下发一个性能采集任务到执行机（source=dispatch, pending）。

    绑定 project_id 时校验项目 member/admin；不绑则仅需登录（perf 偏个人/全局工具）。
    """
    if body.project_id is not None:
        assert_project_role(db, user, body.project_id, _WRITE_ROLES)
    if body.report_set_id is not None and not db.get(PerfReportSet, body.report_set_id):
        raise HTTPException(404, "报告集不存在")
    r = PerfRun(
        project_id=body.project_id,
        report_set_id=body.report_set_id,
        runner=body.runner,
        scenario=body.scenario,
        variant=body.variant,
        proc=body.proc,
        duration=body.duration,
        source="dispatch",
        status="pending",
        enqueued_by=user.id,
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


@router.get("/runs")
def list_runs(
    project_id: int | None = None,
    report_set_id: int | None = None,
    scenario: str | None = None,
    variant: str | None = None,
    status_: str | None = Query(None, alias="status"),
    runner: str | None = None,
    source: str | None = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(PerfRun)
    if project_id is not None:
        q = q.filter(PerfRun.project_id == project_id)
    if report_set_id is not None:
        q = q.filter(PerfRun.report_set_id == report_set_id)
    if scenario:
        q = q.filter(PerfRun.scenario == scenario)
    if variant:
        q = q.filter(PerfRun.variant == variant)
    if status_:
        q = q.filter(PerfRun.status == status_)
    if runner:
        q = q.filter(PerfRun.runner == runner)
    if source:
        q = q.filter(PerfRun.source == source)
    rows = q.order_by(PerfRun.id.desc()).limit(limit).all()
    return ok([_to_out(r) for r in rows])


@router.get("/report")
def report_payload(
    scenario: str | None = None,
    variant: str | None = None,
    ids: str | None = None,
    report_set_id: int | None = None,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """报告页数据源：返回 [{run_id, meta, samples, events}]，直接喂 report-logic。

    只取已完成(completed)的 run；meta 兜底补 scenario/variant（分组要用）。
    report_set_id 传入时只取该报告集内的采集（报告按集独立展示）。
    """
    q = db.query(PerfRun).filter(PerfRun.status == "completed")
    if report_set_id is not None:
        q = q.filter(PerfRun.report_set_id == report_set_id)
    if ids:
        id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        if id_list:
            q = q.filter(PerfRun.id.in_(id_list))
    if scenario:
        q = q.filter(PerfRun.scenario == scenario)
    if variant:
        q = q.filter(PerfRun.variant == variant)
    rows = q.order_by(PerfRun.id.desc()).limit(limit).all()

    payload = []
    for r in rows:
        meta = _meta_of(r)
        if not meta:
            continue
        meta.setdefault("scenario", r.scenario)
        meta.setdefault("variant", r.variant)
        payload.append({
            "run_id": r.id,
            "meta": meta,
            "samples": json.loads(r.samples_json) if r.samples_json else [],
            "events": json.loads(r.events_json) if r.events_json else [],
        })
    return ok(payload)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "记录不存在")
    return ok(_to_out(r, full=True))


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "记录不存在")
    if not user.is_platform_admin and r.enqueued_by != user.id:
        raise HTTPException(403, "只能删除自己下发的记录")
    db.delete(r)
    db.commit()
    return ok({"deleted": run_id})


# ==================== 交互采集控制（用户 JWT）====================

@router.get("/runs/{run_id}/prompt")
def get_prompt(run_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """采集控制页轮询：返回当前提示、状态、signal_seq（判断 agent 是否已消费上一次点击）。"""
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "记录不存在")
    return ok({
        "run_id": r.id,
        "status": r.status,
        "scenario": r.scenario,
        "variant": r.variant,
        "prompt": r.prompt,
        "signal_seq": r.signal_seq,
        "error": r.error,
    })


@router.post("/runs/{run_id}/signal")
def send_signal(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """平台点「继续」：signal_seq +1，并清空当前 prompt（表示这条待办已推进，等 agent 报下一条）。"""
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "记录不存在")
    if not user.is_platform_admin and r.enqueued_by != user.id:
        raise HTTPException(403, "只能推进自己下发的采集")
    if r.status != "running":
        raise HTTPException(409, "该采集不在进行中，无法推进")
    r.signal_seq = (r.signal_seq or 0) + 1
    r.prompt = None
    db.commit()
    db.refresh(r)
    return ok({"signal_seq": r.signal_seq})


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """取消采集：标记 canceled，agent 轮询到后 kill 掉 perfdog。仅本人/管理员。"""
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "记录不存在")
    if not user.is_platform_admin and r.enqueued_by != user.id:
        raise HTTPException(403, "只能取消自己下发的采集")
    if r.status not in ("pending", "running"):
        raise HTTPException(409, "该采集已结束，无法取消")
    r.status = "canceled"
    r.prompt = None
    r.error = "用户取消采集"
    db.commit()
    return ok({"canceled": run_id})


# ==================== runner 侧（runner token）====================

@router.get("/queue")
def queue_pending(
    runner: str = Query("win-01"),
    limit: int = Query(5, le=20),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """runner 拉取待执行的 dispatch 任务。设备 token 锁定 runner_id，防冒充。"""
    if ctx.device is not None:
        runner = ctx.device.runner_id
        ctx.device.last_seen_at = datetime.utcnow()
        db.commit()
    rows = (
        db.query(PerfRun)
        .filter(PerfRun.status == "pending", PerfRun.source == "dispatch", PerfRun.runner == runner)
        .order_by(PerfRun.id)
        .limit(limit)
        .all()
    )
    return ok([_to_out(r) for r in rows])


@router.post("/queue/{run_id}/claim")
def queue_claim(
    run_id: int,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(PerfRun, run_id)
    if not r or r.status != "pending":
        raise HTTPException(409, "该任务不可认领")
    if r.runner != runner:
        raise HTTPException(403, "该任务未派给此执行机")
    r.status = "running"
    db.commit()
    db.refresh(r)
    return ok(_to_out(r))


@router.patch("/queue/{run_id}")
def queue_report(
    run_id: int,
    body: PerfReportIn,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.runner != runner:
        raise HTTPException(403, "该任务未派给此执行机")
    _apply_result(r, outcome=body.outcome, meta=body.meta, samples=body.samples, events=body.events, error=body.error)
    db.commit()
    db.refresh(r)
    # 性能红线检查(旁路):completed 且报告集设了阈值 → 超线推飞书;失败静默
    try:
        from app.services.perf_guard import guard_perf_run
        guard_perf_run(db, r)
    except Exception:
        pass
    return ok(_to_out(r))


@router.post("/queue/upload")
def queue_upload(
    body: PerfUploadIn,
    runner: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """本地交互采集结果一次性直传（source=upload）。设备 token 优先决定归属 runner_id。"""
    rid = ctx.device.runner_id if ctx.device is not None else (runner or body.runner)
    if ctx.device is not None:
        ctx.device.last_seen_at = datetime.utcnow()
    if body.report_set_id is not None and not db.get(PerfReportSet, body.report_set_id):
        raise HTTPException(404, "报告集不存在")
    r = PerfRun(
        project_id=body.project_id,
        report_set_id=body.report_set_id,
        runner=rid,
        scenario=body.scenario,
        variant=body.variant,
        proc=body.proc,
        duration=body.duration,
        source="upload",
        status="pending",
        enqueued_by=None,
    )
    _apply_result(r, outcome=body.outcome, meta=body.meta, samples=body.samples, events=body.events, error=None)
    db.add(r)
    db.commit()
    db.refresh(r)
    # 性能红线检查(旁路,与 queue_report 同款)
    try:
        from app.services.perf_guard import guard_perf_run
        guard_perf_run(db, r)
    except Exception:
        pass
    return ok(_to_out(r))


@router.patch("/queue/{run_id}/prompt")
def report_prompt(
    run_id: int,
    body: PerfPromptIn,
    runner: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RunnerCtx = Depends(require_runner_ctx),
):
    """agent 上报 perfdog 当前提示行；返回当前 signal_seq + status，让 agent 同一次调用即拿到
    平台是否点了「继续」(seq 变大→写回车) 和是否被 canceled(→kill perfdog)。"""
    if ctx.device is not None:
        runner = ctx.device.runner_id
    r = db.get(PerfRun, run_id)
    if not r:
        raise HTTPException(404, "任务不存在")
    if r.runner != runner:
        raise HTTPException(403, "该任务未派给此执行机")
    # canceled 时不覆盖 prompt（保留取消态）；否则更新提示
    if r.status != "canceled":
        r.prompt = body.prompt
        db.commit()
        db.refresh(r)
    return ok({"signal_seq": r.signal_seq, "status": r.status})

def _set_out(db: Session, s: PerfReportSet) -> dict:
    cnt = db.query(PerfRun).filter(PerfRun.report_set_id == s.id).count()
    done = db.query(PerfRun).filter(PerfRun.report_set_id == s.id, PerfRun.status == "completed").count()
    from app.services.perf_guard import parse_thresholds
    return {
        "id": s.id,
        "name": s.name,
        "created_by": s.created_by,
        "run_count": cnt,
        "completed_count": done,
        "thresholds": parse_thresholds(s.thresholds_json),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@router.get("/report-sets")
def list_report_sets(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    rows = db.query(PerfReportSet).order_by(PerfReportSet.id.desc()).all()
    return ok([_set_out(db, s) for s in rows])


@router.post("/report-sets")
def create_report_set(body: PerfReportSetIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = PerfReportSet(name=body.name.strip(), created_by=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return ok(_set_out(db, s))


@router.patch("/report-sets/{set_id}")
def rename_report_set(set_id: int, body: PerfReportSetIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.get(PerfReportSet, set_id)
    if not s:
        raise HTTPException(404, "报告集不存在")
    if not user.is_platform_admin and s.created_by != user.id:
        raise HTTPException(403, "只能修改自己创建的报告集")
    s.name = body.name.strip()
    db.commit()
    db.refresh(s)
    return ok(_set_out(db, s))


@router.patch("/report-sets/{set_id}/thresholds")
def set_thresholds(set_id: int, body: PerfThresholdsIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """设置报告集性能红线。{metricKey: {max?: n, min?: n}};传 {} 清空(关闭告警)。

    metricKey 须在 METRIC_DEFS 白名单内;数值须为数字。超线时采集完成即推飞书告警。
    """
    from app.services.perf_guard import METRIC_DEFS

    s = db.get(PerfReportSet, set_id)
    if not s:
        raise HTTPException(404, "报告集不存在")
    if not user.is_platform_admin and s.created_by != user.id:
        raise HTTPException(403, "只能修改自己创建的报告集")
    cleaned = {}
    for k, v in (body.thresholds or {}).items():
        if k not in METRIC_DEFS:
            raise HTTPException(400, f"未知指标 {k}(可用:{','.join(METRIC_DEFS)})")
        if not isinstance(v, dict):
            raise HTTPException(400, f"指标 {k} 的阈值须为对象 {{max?/min?}}")
        rule = {}
        for bound in ("max", "min"):
            if v.get(bound) is not None:
                if not isinstance(v[bound], (int, float)):
                    raise HTTPException(400, f"指标 {k} 的 {bound} 须为数字")
                rule[bound] = v[bound]
        if rule:
            cleaned[k] = rule
    s.thresholds_json = json.dumps(cleaned, ensure_ascii=False) if cleaned else None
    db.commit()
    db.refresh(s)
    return ok(_set_out(db, s))


@router.delete("/report-sets/{set_id}")
def delete_report_set(set_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """删除报告集。其下 run 的 report_set_id 由外键 SET NULL（run 记录保留，脱离该集）。"""
    s = db.get(PerfReportSet, set_id)
    if not s:
        raise HTTPException(404, "报告集不存在")
    if not user.is_platform_admin and s.created_by != user.id:
        raise HTTPException(403, "只能删除自己创建的报告集")
    db.delete(s)
    db.commit()
    return ok({"deleted": set_id})
