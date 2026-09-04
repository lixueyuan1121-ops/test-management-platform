import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ExecStatus, ProjectRole
from app.db.session import get_db
from app.models import ExecRun, FailCluster, Project, Requirement, TestCase, User
from app.models.issue import RemainingIssue
from app.schemas.common import ok
from app.services import ai_jobs, fail_cluster as fcsvc

router = APIRouter(prefix="/api/fail-clusters", tags=["fail-clusters"])


class AnalyzeBody(BaseModel):
    project_id: int
    release_id: int
    requirement_ids: list[int] | None = None
    task_ids: list[int] | None = None


def _to_out(c: FailCluster) -> dict:
    return {
        "id": c.id, "release_id": c.release_id, "root_cause_title": c.root_cause_title,
        "summary": c.summary, "triage_kind": c.triage_kind, "member_count": c.member_count,
        "severity": c.severity, "confidence": c.confidence, "issue_id": c.issue_id,
        "run_ids": json.loads(c.run_ids or "[]"),
        "requirement_ids": json.loads(c.requirement_ids or "[]"),
        "batch_key": c.batch_key,
    }


@router.get("/scope")
def scope(release_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """列出该版本下的需求（含失败执行数），供勾选纳入聚类。"""
    reqs = db.query(Requirement).filter(Requirement.release_id == release_id).all()
    fail_status = [ExecStatus.failed, ExecStatus.blocked]
    out = []
    for rq in reqs:
        cnt = (db.query(ExecRun.id)
               .join(TestCase, ExecRun.test_case_id == TestCase.id)
               .filter(TestCase.requirement_id == rq.id, ExecRun.status.in_(fail_status)).count())
        out.append({"id": rq.id, "title": rq.title, "fail_count": cnt})
    return ok({"requirements": out})


@router.post("/analyze")
def analyze(body: AnalyzeBody, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """入队一次聚类。返回 job_id，前端走 pollAiJob。"""
    assert_project_role(db, user, body.project_id, (ProjectRole.admin,))
    batch_key = f"rel{body.release_id}"
    job = ai_jobs.enqueue(db, "fail_cluster", project_id=body.project_id, user_id=user.id,
                          input={"release_id": body.release_id,
                                 "requirement_ids": body.requirement_ids,
                                 "task_ids": body.task_ids, "batch_key": batch_key},
                          ref_kind="release", ref_id=body.release_id)
    return ok({"job_id": job.id})


@router.get("")
def list_clusters(release_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """读该版本最新聚类结果（按 member_count 降序）。"""
    rows = (db.query(FailCluster).filter(FailCluster.release_id == release_id)
            .order_by(FailCluster.member_count.desc(), FailCluster.id.desc()).all())
    fail_total = sum(r.member_count for r in rows)
    return ok({"items": [_to_out(r) for r in rows],
               "cluster_count": len(rows), "fail_count": fail_total})


@router.post("/{cid}/create-issue")
def create_issue(cid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """把一个根因簇建成 1 张遗留问题草稿（幂等：已建则返回原 issue_id）。"""
    c = db.get(FailCluster, cid)
    if not c:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="聚类不存在")
    assert_project_role(db, user, c.project_id, (ProjectRole.admin,))
    if c.issue_id:
        return ok({"issue_id": c.issue_id, "already": True})
    run_ids = json.loads(c.run_ids or "[]")
    desc = (f"AI 聚类根因：{c.summary or c.root_cause_title}\n"
            f"归因类别：{c.triage_kind or '未知'}\n"
            f"影响失败执行：{c.member_count} 条（run: {run_ids[:20]}）\n"
            f"涉及需求：{json.loads(c.requirement_ids or '[]')}")
    issue = RemainingIssue(
        project_id=c.project_id, title=c.root_cause_title[:255], description=desc,
        severity=_severity_enum(c.severity), exec_run_id=(run_ids[0] if run_ids else None))
    db.add(issue); db.flush()
    c.issue_id = issue.id
    db.commit()
    return ok({"issue_id": issue.id, "already": False})


def _severity_enum(sev: str | None):
    from app.core.enums import IssueSeverity
    try:
        return IssueSeverity(sev) if sev else IssueSeverity.major
    except ValueError:
        return IssueSeverity.major
