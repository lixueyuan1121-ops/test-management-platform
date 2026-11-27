from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.md_run import MdRun, MdRunRetest


def add_history(db, run_id, output):
    origin = db.get(MdRunRetest, run_id)
    if origin:
        output["retest"] = {"source_run_id": origin.source_run_id, "external_ref": origin.external_ref}
        return
    refs = {f.get("external_ref") for f in output["findings"] if isinstance(f, dict) and f.get("external_ref")}
    if not refs:
        return
    rows = (db.query(MdRunRetest.external_ref, MdRun.id, MdRun.status, MdRun.client_version)
            .join(MdRun, MdRun.id == MdRunRetest.run_id)
            .filter(MdRunRetest.source_run_id == run_id, MdRunRetest.external_ref.in_(refs))
            .order_by(MdRun.id.desc()))
    history = {ref: [] for ref in refs}
    for ref, rid, status, version in rows:
        history[ref].append({"id": rid, "status": status, "client_version": version})
    for finding in output["findings"]:
        if isinstance(finding, dict) and finding.get("external_ref") in history:
            finding["retests"] = history[finding["external_ref"]]


def dispatch(db, source, findings, body, user_id):
    ref = body.external_ref
    linked = [f for _, f in findings if isinstance(f, dict) and f.get("external_ref") == ref]
    if not linked:
        raise HTTPException(409, "该任务没有已关联此工单的缺陷")

    history = (db.query(MdRun).join(MdRunRetest, MdRunRetest.run_id == MdRun.id)
               .filter(MdRunRetest.source_run_id == source.id, MdRunRetest.external_ref == ref)
               .order_by(MdRun.id.desc()))
    latest = history.with_for_update().first()
    if latest and (latest.status in ("pending", "running") or latest.id != body.previous_run_id):
        return {"run_id": latest.id, "created": False}
    if not latest and body.previous_run_id:
        raise HTTPException(409, "复验记录已变更，请刷新任务详情")

    runner = body.runner or source.runner
    env_group = body.env_group or source.env_group
    version = source.client_version if body.client_version is None else body.client_version.strip() or None
    cases = "\n\n".join(f"### {f.get('title') or ref}\n{f.get('detail') or f.get('description') or ''}" for f in linked)
    reference = "\n".join("> " + line for line in source.content.splitlines())
    document = (f"# 修复验证 · {ref}\n\n来源：提测任务 #{source.id} · {source.title}\n"
                f"本次执行机：{runner}\n本次环境分组：{env_group}\n"
                f"目标客户端版本：{version or '当前安装版本'}\n\n"
                "## 本次验证范围\n仅执行下面缺陷的复现步骤、关联用例及必要前置条件。"
                "如指定了版本，请先核实客户端版本，版本不符时记录 blocked。"
                "记录本次预期和实际结果，按原用例预期判定 pass、fail 或 blocked。\n\n"
                f"{cases}\n\n## 原提测文档（供查阅前置条件、步骤和预期）\n{reference}\n")
    row = MdRun(project_id=source.project_id, task_id=source.task_id, req_no=source.req_no,
                title=f"修复验证 {ref} · {linked[0].get('title') or source.title}"[:255],
                runner=runner, env_group=env_group, client_version=version,
                source="retest", content=document, status="pending", enqueued_by=user_id,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(row)
    db.flush()
    run_id = row.id
    db.add(MdRunRetest(run_id=run_id, source_run_id=source.id, external_ref=ref,
                       previous_run_id=body.previous_run_id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # A concurrent request already dispatched from the same history entry.
        winner = history.first()
        if not winner:
            raise
        return {"run_id": winner.id, "created": False}
    return {"run_id": run_id, "created": True}
