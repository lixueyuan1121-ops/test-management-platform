"""Import external reports without impersonating a queue worker."""
import hashlib
import json
import uuid
from datetime import timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.deps import assert_project_role, get_current_user
from app.core.enums import ProjectRole
from app.db.session import get_db
from app.models import AiTask, ExecRun, Project, Requirement, TestCase, User
from app.schemas.common import ok
from app.schemas.verified_import import VerifiedImport
from app.services.claude_runner import validate_script_for_edit
from app.services.selector_device import owned_device

router = APIRouter(prefix="/api/verified-imports", tags=["verified-imports"])

@router.post("")
def import_verified(body: VerifiedImport, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    assert_project_role(db, user, body.project_id, (ProjectRole.admin, ProjectRole.member))
    # Serialize imports within this project on the production MySQL database.
    db.query(Project).filter_by(id=body.project_id).with_for_update().one()
    marker = f"verified:{user.id}:{body.external_id}"
    digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), ensure_ascii=False,
                                      sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    previous = db.query(AiTask).filter_by(project_id=body.project_id, kind="verified_import", input_ref=marker).with_for_update().first()
    if previous:
        saved = json.loads(previous.output_raw)
        if saved["sha256"] != digest:
            raise HTTPException(409, "相同 external_id 已导入不同内容")
        return ok({**saved["receipt"], "reused": True})
    device = owned_device(db, user, "", body.runner_device_id)
    if device.platform != "web":
        raise HTTPException(422, "当前导入契约仅支持 web/PC 执行设备")
    normalized = []
    for case in body.cases:
        script, error = validate_script_for_edit(case.exec_kind, case.script, body.project_id, db, "")
        if error:
            raise HTTPException(422, f"{case.title}: {error}")
        normalized.append(script)
    req = Requirement(project_id=body.project_id, title=body.requirement, created_by=user.id)
    db.add(req)
    db.flush()
    task = AiTask(project_id=body.project_id, user_id=user.id, kind="verified_import", provider="codex",
                  input_type="text", input_ref=marker, status="done", case_count=len(body.cases))
    db.add(task)
    db.flush()
    batch = uuid.uuid4().hex
    records = []
    from app.api.exec_queue import _dispatch_payload
    for case, script in zip(body.cases, normalized):
        tc = TestCase(ai_task_id=task.id, project_id=body.project_id, requirement_id=req.id,
                      provider="codex", sub_product="", title=case.title, category=case.category,
                      priority=case.priority, page=case.page, exec_kind=case.exec_kind, platform="web",
                      steps=case.steps, expected=case.expected, precondition=case.precondition or None,
                      script=json.dumps(script, ensure_ascii=False), is_regression=True,
                      review_status="pending", adopted=False,
                      kind_reason="外部实测导入；执行器、环境与验证范围见执行记录")
        db.add(tc)
        db.flush()
        end = case.finished_at.astimezone(timezone.utc).replace(tzinfo=None)
        run = ExecRun(project_id=body.project_id, test_case_id=tc.id, kind=case.exec_kind,
                      runner=device.runner_id, runner_device_id=device.id, status="passed", verdict="pass",
                      enqueued_by=user.id, batch_id=batch,
                      payload=json.dumps(_dispatch_payload(tc, db, False), ensure_ascii=False),
                      report=json.dumps(case.report, ensure_ascii=False), duration_ms=case.duration_ms,
                      started_at=end-timedelta(milliseconds=case.duration_ms), finished_at=end,
                      reason=f"[外部实测导入] {case.executor}；环境：{case.environment}；范围：{case.scope}。不是线上队列执行记录。")
        db.add(run)
        db.flush()
        records.append({"case_id": tc.id, "run_id": run.id, "title": tc.title})
    receipt = {"project_id": body.project_id, "requirement_id": req.id,
               "batch_id": batch, "records": records}
    task.output_raw = json.dumps({"sha256": digest, "receipt": receipt}, ensure_ascii=False)
    db.commit()
    return ok({**receipt, "reused": False})
