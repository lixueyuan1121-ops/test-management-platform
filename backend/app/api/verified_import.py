"""Import externally verified results, reusing scenarios across project members."""
import json
import uuid
from datetime import timedelta, timezone
from types import SimpleNamespace
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import update
from sqlalchemy.orm import Session
from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import AiTask, ExecRun, Project, Requirement, TestCase, User
from app.schemas.common import ok
from app.schemas.verified_import import DEFAULT_IMPORT_TASK, VerifiedImport
from app.services.verified_import_tasks import resolve_import_task
from app.services.claude_runner import validate_script_for_edit
from app.services.selector_device import owned_device
from app.services.verified_dedup import digest, plans_for

router = APIRouter(prefix="/api/verified-imports", tags=["verified-imports"])


def _authorize_and_lock(body, db, user):
    assert_project_role(db, user, body.project_id, (ProjectRole.admin, ProjectRole.member))
    # A database write lock, not a process mutex: serializes workers/hosts and SQLite.
    # On MySQL every subsequent matching read is a locking/current read, not an old RR snapshot.
    count = db.execute(update(Project).where(Project.id == body.project_id).values(id=Project.id, updated_at=Project.updated_at)).rowcount
    if not count:
        raise HTTPException(404, "项目不存在")
    device = owned_device(db, user, "", body.runner_device_id)
    if device.platform != "web":
        raise HTTPException(422, "当前导入契约仅支持 web/PC 执行设备")
    return device


def _prepare(body, db):
    scripts = []
    for case in body.cases:
        script, error = validate_script_for_edit(case.exec_kind, case.script, body.project_id, db, body.sub_product)
        if error:
            raise HTTPException(422, f"{case.title}: {error}")
        scripts.append(script)
    rows = (db.query(TestCase).filter_by(project_id=body.project_id, sub_product=body.sub_product, platform="web")
            .with_for_update().populate_existing().all())
    try:
        plans = plans_for(body, scripts, rows)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return scripts, {r.id: r for r in rows}, plans


def _request_digest(body):
    data = body.model_dump(mode="json")
    # Preserve old receipts' hashes when new optional fields are absent/defaulted.
    if data["task_name"] == DEFAULT_IMPORT_TASK:
        del data["task_name"]
    if not data["sub_product"]:
        del data["sub_product"]
    for case in data["cases"]:
        if case["resolution"] is None:
            del case["resolution"]
    return digest(data)


@router.post("/preview")
def preview_verified(body: VerifiedImport, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _authorize_and_lock(body, db, user)
    _, _, plans = _prepare(body, db)
    db.rollback()
    return ok({"project_id": body.project_id, "ready": all(p["ready"] for p in plans), "cases": plans})


@router.post("")
def import_verified(body: VerifiedImport, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return perform_import(body, db, user)


def perform_import(body, db, user, *, commit=True):
    """Shared transaction: queued imports commit the receipt and item outcome together."""
    device = _authorize_and_lock(body, db, user)
    marker = f"verified:{user.id}:{body.external_id}"
    sha = _request_digest(body)
    previous = (db.query(AiTask).filter_by(project_id=body.project_id, kind="verified_import", input_ref=marker)
                .with_for_update().first())
    if previous:
        saved = json.loads(previous.output_raw)
        if saved["sha256"] != sha:
            raise HTTPException(409, "相同 external_id 已导入不同内容")
        return ok({**saved["receipt"], "reused": True})
    scripts, existing, plans = _prepare(body, db)
    if not all(p["ready"] for p in plans):
        if commit:
            db.rollback()
        return JSONResponse(status_code=409, content={"code": 409, "msg": "存在疑似重复或候选已变化，请确认后导入",
                            "data": {"reason": "duplicate_confirmation_required", "cases": plans}})
    linked_task = resolve_import_task(db, body.project_id, user.id, body.task_name)
    req = None
    if any(p["case_id"] is None for p in plans):
        # One shared import requirement per project. Current read under the project
        # lock also prevents duplicate groups when different workers create cases.
        req = (db.query(Requirement).filter_by(project_id=body.project_id, title="codex导入用例")
               .order_by(Requirement.id).with_for_update().first())
        if req is None:
            req = Requirement(project_id=body.project_id, title="codex导入用例", created_by=user.id)
            db.add(req)
            db.flush()
    task = AiTask(project_id=body.project_id, task_id=linked_task.id, user_id=user.id, kind="verified_import", provider="codex",
                  input_type="text", input_ref=marker, status="done", case_count=len(body.cases))
    db.add(task)
    db.flush()
    batch, records = uuid.uuid4().hex, []
    from app.api.exec_queue import _dispatch_payload
    for case, script, plan in zip(body.cases, scripts, plans):
        tc = existing.get(plan["case_id"])
        reused = tc is not None
        if tc is None:
            tc = TestCase(ai_task_id=task.id, project_id=body.project_id, task_id=linked_task.id, requirement_id=req.id,
                          provider="codex", sub_product=body.sub_product, title=case.title, category=case.category,
                          priority=case.priority, page=case.page, exec_kind=case.exec_kind, platform="web",
                          steps=case.steps, expected=case.expected, precondition=case.precondition or None,
                          script=json.dumps(script, ensure_ascii=False), is_regression=False,
                          review_status="pending", adopted=False,
                          kind_reason="外部实测导入；执行器、环境与验证范围见执行记录")
            db.add(tc)
            db.flush()
        if tc.task_id is None:
            tc.task_id = linked_task.id
        # Snapshot the submitted execution, NEVER overwrite the canonical case or pair its old script with a new report.
        snapshot = SimpleNamespace(**{**case.model_dump(exclude={"resolution"}), "id": tc.id,
                                   "project_id": body.project_id, "sub_product": body.sub_product,
                                   "script": json.dumps(script, ensure_ascii=False)})
        payload = _dispatch_payload(snapshot, db, False)
        payload["verified_import"] = {"external_id": body.external_id, "requirement": body.requirement,
                                      "task_id": linked_task.id, "task_name": linked_task.title,
                                      "executor": case.executor, "environment": case.environment, "scope": case.scope,
                                      "resolution": case.resolution.model_dump() if case.resolution else None}
        end = case.finished_at.astimezone(timezone.utc).replace(tzinfo=None)
        run = ExecRun(project_id=body.project_id, test_case_id=tc.id, task_id=linked_task.id, kind=case.exec_kind,
                      runner=device.runner_id, runner_device_id=device.id, status="passed", verdict="pass",
                      enqueued_by=user.id, batch_id=batch, payload=json.dumps(payload, ensure_ascii=False),
                      report=json.dumps(case.report, ensure_ascii=False), duration_ms=case.duration_ms,
                      started_at=end-timedelta(milliseconds=case.duration_ms), finished_at=end,
                      reason=f"[外部实测导入] {case.executor}；环境：{case.environment}；范围：{case.scope}。不是线上队列执行记录。")
        db.add(run)
        db.flush()
        records.append({"case_id": tc.id, "run_id": run.id, "title": case.title,
                        "disposition": "reused" if reused else "created",
                        "task_id": linked_task.id, "task_name": linked_task.title, "case_task_id": tc.task_id})
    receipt = {"project_id": body.project_id, "requirement_id": req.id if req else None,
               "task_id": linked_task.id, "task_name": linked_task.title,
               "batch_id": batch, "records": records,
               "created_cases": sum(r["disposition"] == "created" for r in records),
               "reused_cases": sum(r["disposition"] == "reused" for r in records)}
    task.output_raw = json.dumps({"sha256": sha, "receipt": receipt}, ensure_ascii=False)
    if commit:
        db.commit()
    return ok({**receipt, "reused": False})
