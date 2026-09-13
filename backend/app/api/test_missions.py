from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import update
from app.core.deps import get_current_user, assert_project_role
from app.core.enums import ALL_PROJECT_ROLES, WRITE_ROLES, ReviewStatus
from app.db.session import get_db
from app.models import (TestMission, MissionEvent, MissionRun, RequirementAnalysis, RequirementBaseline,
                        RequirementCaseLink, TestCase, ExecRun, AiJob, AiTask, User, ProjectMember, RunnerDevice)
from app.schemas.common import ok
from app.schemas.test_mission import MissionCreate, MissionDecision
from app.schemas.exec_queue import EnqueueCasesIn
from app.services import ai_jobs
from app.services import test_missions as svc
from app.services.requirement_analysis import encode, case_hash, approved_criteria

router = APIRouter(prefix="/api/test-missions", tags=["test-missions"])


def get_mission(db, user, mid, write=False):
    row = db.get(TestMission, mid)
    if not row:
        raise HTTPException(404, "测试目标不存在")
    assert_project_role(db, user, row.project_id, tuple(WRITE_ROLES if write else ALL_PROJECT_ROLES))
    return row


def output(db, m, detail=False):
    result = {k: getattr(m, k) for k in ("id", "project_id", "task_id", "goal", "provider", "analysis_id", "baseline_id",
        "ai_task_id", "phase", "paused", "revision", "active_job_id", "error", "interventions", "authorized_by")}
    for k in ("created_at", "updated_at", "completed_at", "authorized_at"):
        result[k] = getattr(m, k).isoformat() if getattr(m, k) else None
    result["policy"] = svc.unpack(m.policy)
    result["stale"] = bool(m.baseline_id and not svc.baseline_current(db, m))
    if detail:
        result["plan"] = svc.unpack(m.plan)
        result["report"] = svc.evidence_report(db, m) if m.baseline_id else None
        result["final_report"] = svc.unpack(m.report)
        result["events"] = [{"id": e.id, "kind": e.kind, "message": e.message,
            "data": svc.unpack(e.data), "actor_id": e.actor_id, "created_at": e.created_at.isoformat()}
            for e in db.query(MissionEvent).filter_by(mission_id=m.id).order_by(MissionEvent.id).all()]
    return result


@router.post("")
def create(body: MissionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, tuple(WRITE_ROLES))
    if body.analysis_id:
        from app.api.requirement_analysis import get_analysis
        analysis = get_analysis(db, user, body.analysis_id, True)
        if analysis.project_id != body.project_id or not analysis.task_id:
            raise HTTPException(400, "请选择当前项目且有关联任务的需求分析")
    else:
        from app.api.requirement_analysis import create_analysis
        data = create_analysis(db, user, body.requirement, commit=False)
        analysis = db.get(RequirementAnalysis, data["analysis_id"])
    m = TestMission(project_id=body.project_id, task_id=analysis.task_id, goal=body.goal.strip(),
        provider=analysis.provider, created_by=user.id, analysis_id=analysis.id,
        active_job_id=analysis.job_id, phase="analyzing", revision=1,
        policy=encode({"max_cases": body.max_cases}), interventions=0)
    db.add(m); db.flush()
    if body.requirement and analysis.job_id:
        db.get(AiJob, analysis.job_id).input = encode({"analysis_id": analysis.id, "mission_id": m.id})
    svc.event(db, m, "created", "测试目标已保存，后台将持续推进；业务验收与执行授权由人确认", actor=user.id)
    db.commit()
    ai_jobs.notify_new_job()
    return ok(output(db, m, True))


@router.get("")
def listing(project_id: int, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assert_project_role(db, user, project_id, tuple(ALL_PROJECT_ROLES))
    return ok([output(db, m) for m in db.query(TestMission).filter_by(project_id=project_id).order_by(TestMission.id.desc()).limit(limit).all()])


@router.get("/metrics")
def metrics(project_id: int | None = None, days: int = Query(30, ge=1, le=365),
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(TestMission).filter(TestMission.created_at >= datetime.now() - timedelta(days=days))
    if project_id:
        assert_project_role(db, user, project_id, tuple(ALL_PROJECT_ROLES))
        query = query.filter(TestMission.project_id == project_id)
    elif not user.is_platform_admin:
        query = query.filter(TestMission.project_id.in_(db.query(ProjectMember.project_id).filter_by(user_id=user.id)))
    missions = query.all()
    completed = [m for m in missions if m.completed_at]
    reports = [svc.evidence_report(db, m) for m in completed]
    durations = [(m.completed_at - m.created_at).total_seconds() for m in completed]
    covered = sum(r["verified"] for r in reports)
    total = sum(r["total"] for r in reports)
    costs = []
    for m in missions:
        at = db.get(AiTask, m.ai_task_id) if m.ai_task_id else None
        if at and at.cost_usd is not None:
            costs.append(float(at.cost_usd))
    return ok({"days": days, "total": len(missions), "completed": len(completed),
        "ready_for_review": sum(r["verdict"] == "ready_for_review" for r in reports),
        "attention": sum(m.phase in {"clarifying", "awaiting_approval", "attention"} for m in missions),
        "avg_interventions": round(sum(m.interventions for m in completed) / len(completed), 1) if completed else None,
        "avg_duration_minutes": round(sum(durations) / len(durations) / 60, 1) if durations else None,
        "verified_criteria": covered, "total_criteria": total,
        "recorded_generation_cost_usd": round(sum(costs), 4) if costs else None,
        "cost_samples": len(costs), "cost_note": "仅统计有实际费用记录的用例生成；分析、规划及执行费用未完整采集，不估算总成本。",
        "metric_note": "按目标创建时间统计；完成表示流程收口，具备发布评审条件另计。有效覆盖按当前验收版本、用例核对与执行证据重算；平均耗时包含人工等待。"})


@router.get("/{mid}")
def detail(mid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return ok(output(db, get_mission(db, user, mid), True))


@router.get("/{mid}/runs/{run_id}")
def run_evidence(mid: int, run_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    m = get_mission(db, user, mid)
    link = db.query(MissionRun).filter_by(mission_id=mid, run_id=run_id).first()
    run = db.get(ExecRun, run_id) if link else None
    if not run or run.project_id != m.project_id:
        raise HTTPException(404, "执行记录不属于该目标")
    # Never expose the execution payload: it contains API environment credentials.
    return ok({"id": run.id, "status": run.status.value, "reason": run.reason,
               "report": svc.unpack(run.report, []), "evidence_url": run.evidence_url})


@router.post("/{mid}/decisions")
def decision(mid: int, body: MissionDecision, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_mission(db, user, mid, True)
    m = svc.lock(db, mid, body.revision)
    if body.action == "pause":
        if m.phase == "completed" or m.paused:
            raise HTTPException(409, "当前目标无需暂停")
        m.paused = True
        svc.event(db, m, "paused", "已暂停后续推进；已下发到执行机的工作仍可能继续", actor=user.id)
    elif body.action == "resume":
        if not m.paused and m.phase != "attention":
            raise HTTPException(409, "当前目标无需恢复")
        if m.baseline_id and not svc.baseline_current(db, m):
            raise HTTPException(409, "验收版本已变化，请基于新版本开始新一轮目标")
        m.paused = False
        if m.phase == "attention":
            m.phase = m.resume_phase or "analyzing"
            job = db.get(AiJob, m.active_job_id) if m.active_job_id else None
            if job and job.status in {"failed", "cancelled"}:
                job.status = "pending"; job.error = None; job.result = None; job.claimed_at = None
            # Explicit takeover establishes the principal for further writes.
            m.created_by = user.id
            if m.authorized_by:
                m.authorized_by = user.id
        m.error = None
        svc.event(db, m, "resumed", "按已保存的输入和预算继续推进", actor=user.id)
    elif body.action == "replan":
        if m.phase not in {"awaiting_approval", "attention"} or db.query(MissionRun.id).filter_by(mission_id=m.id).first():
            raise HTTPException(409, "已经执行的目标请开始新一轮，保留原始证据")
        a = db.get(RequirementAnalysis, m.analysis_id)
        b = db.query(RequirementBaseline).filter_by(analysis_id=a.id, revision=a.revision).first() if a else None
        if not b:
            raise HTTPException(409, "请先确认当前验收版本")
        m.baseline_id = b.id; m.plan = "{}"; m.error = None; m.paused = False
        svc.event(db, m, "replan", "根据当前验收版本和用例内容重新准备方案", actor=user.id)
        svc.start_generation(db, m)
    elif body.action == "approve":
        if m.phase != "awaiting_approval" or m.paused or not svc.baseline_current(db, m):
            raise HTTPException(409, "方案未就绪、已暂停或验收版本已变化，请刷新后重新规划")
        if not body.reviewed or not body.runner.strip():
            raise HTTPException(422, "请核对所选用例及验收关联，并选择授权执行设备")
        device = db.query(RunnerDevice).filter_by(owner_id=user.id, runner_id=body.runner.strip()).first()
        if not device:
            raise HTTPException(422, "请选择自己已登记的执行设备")
        baseline = db.get(RequirementBaseline, m.baseline_id)
        locked = db.execute(update(RequirementAnalysis).where(RequirementAnalysis.id == m.analysis_id,
            RequirementAnalysis.revision == baseline.revision).values(revision=baseline.revision))
        if locked.rowcount != 1:
            raise HTTPException(409, "验收版本已变化，请重新规划")
        plan = svc.unpack(m.plan)
        by_id = {c["id"]: c for c in plan.get("cases", [])}
        ids = list(dict.fromkeys(body.case_ids))
        if not ids or len(ids) > svc.unpack(m.policy)["max_cases"] or any(i not in by_id for i in ids):
            raise HTTPException(422, "请选择方案内且不超过预算的自动化用例")
        criteria = approved_criteria(svc.unpack(db.get(RequirementBaseline, m.baseline_id).payload))
        for cid in ids:
            db.execute(update(TestCase).where(TestCase.id == cid).values(id=cid))
            tc = db.get(TestCase, cid)
            if tc:
                db.refresh(tc)
            item = by_id[cid]
            if (not tc or tc.project_id != m.project_id or svc.execution_hash(tc) != item["hash"]
                    or tc.review_status == ReviewStatus.rejected):
                raise HTTPException(409, f"用例 {cid} 已变化或被否决，请重新规划后核对")
            tc.adopted = True; tc.review_status = ReviewStatus.adopted; tc.reviewed_at = datetime.now()
            for criterion_id in item["criterion_ids"]:
                criterion = criteria[criterion_id]
                link = db.query(RequirementCaseLink).filter_by(test_case_id=cid, baseline_id=m.baseline_id, criterion_id=criterion_id).first()
                if link:
                    link.reviewed_hash = case_hash(tc)
        from app.api.exec_queue import enqueue_case_runs
        result = enqueue_case_runs(db, user, EnqueueCasesIn(project_id=m.project_id, runner=body.runner.strip(), test_case_ids=ids), commit=False)
        for run_id in result["run_ids"]:
            run = db.get(ExecRun, run_id)
            item = by_id[run.test_case_id]
            db.add(MissionRun(mission_id=m.id, run_id=run_id, case_hash=item["hash"],
                             criterion_ids=encode(item["criterion_ids"]), reviewed_by=user.id))
        m.policy = encode({**svc.unpack(m.policy), "runner": body.runner.strip(), "max_retries": body.max_retries,
            "time_budget_minutes": body.time_budget_minutes, "case_ids": ids})
        m.authorized_by = user.id; m.authorized_at = datetime.now(); m.phase = "executing"; m.active_job_id = None
        svc.event(db, m, "authorized", "已核对所选用例及验收关联，授权在指定设备与预算内执行", {**result,
            "max_retries": body.max_retries, "time_budget_minutes": body.time_budget_minutes, "note": body.note}, user.id)
    elif body.action == "finish":
        if m.phase == "completed":
            raise HTTPException(409, "目标已完成")
        # Finishing before dispatch is useful for manual-only or insufficient plans.
        if db.query(MissionRun.id).filter_by(mission_id=m.id).first():
            raise HTTPException(409, "已下发的执行由后台按预算收口，可暂停后续推进")
        if not body.note.strip():
            raise HTTPException(422, "请说明收口原因，未验证项会保留为缺口")
        svc.finish(db, m, f"人工收口：{body.note}")
    m.interventions += 1
    db.commit()
    ai_jobs.notify_new_job()
    return ok(output(db, m, True))
