"""Create isolated retest tasks from frozen feedback inputs; never reset the baseline."""
import hashlib
import json
from collections import OrderedDict

from fastapi import HTTPException

from app.core.enums import EvalDeviceKind, EvalRunStatus, EvalTaskStatus
from app.models import EvalQuery, EvalRun, Project
from app.models.ai_eval import EvalTask, EvalExperiment
from app.services.eval_engines import DEFAULT_ENGINE, EVAL_ENGINES, validate_dialog_options
from app.services.eval_snapshot import payload_of
from app.services.multica import push_time


def _context_key(run):
    p = payload_of(run)
    group = p.get("conversation_group")
    return (run.batch_id, run.eval_task_id, run.target_engine, run.target_device, group,
            p.get("compare_group"), p.get("configuration_id"), p.get("trial_index", 1)) if group else ("single", run.id)


def create_retest(db, body, user_id):
    from app.api.eval_queue import _new_batch_id
    from app.api.eval_task import assign_groups_balanced
    from app.services.dispatcher import online_eval_runners

    selected_ids = sorted(set(body.run_ids))
    if not db.query(Project).filter(Project.id == body.project_id).with_for_update().first():
        raise HTTPException(404, detail="项目不存在")
    # Serialize submissions touching the same source, so concurrent clicks cannot enqueue twice.
    selected = db.query(EvalRun).filter(EvalRun.project_id == body.project_id,
        EvalRun.id.in_(selected_ids), EvalRun.pushed_multica.is_(True)).order_by(EvalRun.id).with_for_update().all()
    if len(selected) != len(selected_ids):
        raise HTTPException(400, detail="所选结果不存在、未推送 Multica 或不属于当前项目，请刷新后重试")
    engines = list(dict.fromkeys(body.target_engines)) if body.target_engines is not None else None
    if engines is not None and any(e not in EVAL_ENGINES for e in engines):
        raise HTTPException(400, detail="包含不支持的复测产品")
    groups = OrderedDict()
    # Include all turns of the original execution, not similarly named conversations in other batches/configurations.
    batches = {r.batch_id for r in selected if payload_of(r).get('conversation_group') and r.batch_id}
    peers = db.query(EvalRun).filter(EvalRun.project_id == body.project_id,
        EvalRun.batch_id.in_(batches)).order_by(EvalRun.id).all() if batches else []
    for row in selected:
        key = _context_key(row)
        if key not in groups:
            group = [r for r in peers if _context_key(r) == key] if key[0] != 'single' else [row]
            if not group:
                raise HTTPException(400, detail=f"RUN-{row.id} 缺少原始会话批次，无法完整复测")
            group.sort(key=lambda r: (payload_of(r).get('turn_index') or 0, r.id))
            if key[0] != 'single':
                turns = [payload_of(r).get('turn_index') or 0 for r in group]
                if turns != list(range(len(group))):
                    raise HTTPException(400, detail=f"RUN-{row.id} 原会话轮次缺失或重复，无法完整复测")
            groups[key] = group
    originals = {r.id: r for group in groups.values() for r in group}
    if any(r.status in (EvalRunStatus.pending, EvalRunStatus.running, EvalRunStatus.judging) for r in originals.values()):
        raise HTTPException(409, detail="原会话仍在执行或判定，请完成后再复测")
    active = db.query(EvalRun.id).filter(EvalRun.project_id == body.project_id,
        EvalRun.multica_retest_source_id.in_(originals),
        EvalRun.status.in_([EvalRunStatus.pending, EvalRunStatus.running, EvalRunStatus.judging])).with_for_update().first()
    if active:
        raise HTTPException(409, detail="所选反馈已有复测正在进行，请先查看复测记录")
    qids = {r.eval_query_id for r in originals.values() if r.eval_query_id}
    queries = {q.id: q for q in db.query(EvalQuery).filter(EvalQuery.project_id == body.project_id, EvalQuery.id.in_(qids)).all()}
    batch = _new_batch_id()
    plan = []
    for gi, group in enumerate(groups.values(), 1):
        targets = engines or [group[0].target_engine or DEFAULT_ENGINE]
        for eng in targets:
            if eng not in EVAL_ENGINES:
                raise HTTPException(400, detail=f"不支持复测产品 {eng}")
            # Different source configurations/trials remain separate even if the selected target product is identical.
            group_id = f"multica:{batch}:{gi}:{eng}"
            for source in group:
                p = dict(payload_of(source))
                if not p.get('prompt'):
                    raise HTTPException(400, detail=f"RUN-{source.id} 缺少原提问快照，无法按原输入复测，请先核对原始资料")
                opts = dict(p.get('dialog_options') or {})
                if body.model is not None:
                    opts.pop('model', None)
                    if body.model.strip():
                        opts['model'] = body.model.strip()
                # WorkBuddy/QWork do not support Nami's mode/depth controls.
                if eng != 'namiwork':
                    opts = {k: v for k, v in opts.items() if k == 'model'}
                validate_dialog_options(eng, opts)
                p['dialog_options'] = opts
                if engines is not None or body.model is not None:
                    for field in ('configuration_id', 'configuration_index', 'configuration_label', 'configuration_count'):
                        p.pop(field, None)  # Never display an old label for a newly selected model.
                p['conversation_group'] = group_id if payload_of(source).get('conversation_group') else None
                p['source_conversation_group'] = f"multica-source:{group[0].id}"
                p['trial_index'], p['trial_count'] = 1, 1
                p['multica_source_run_id'] = source.id
                # A pinned Nami VM is only valid on the original runner and product.
                pinned = source.target_device if eng == (source.target_engine or DEFAULT_ENGINE) else None
                plan.append((eng, group_id, source, p, pinned))
    if len(plan) > 1000:
        raise HTTPException(400, detail="含会话上下文的复测超过 1000 条，请减少勾选")
    eligible = {}
    assigned = {}
    for eng in dict.fromkeys(item[0] for item in plan):
        online = online_eval_runners(db, engine=eng)
        if not online:
            raise HTTPException(400, detail=f"{EVAL_ENGINES[eng]['label']} 当前没有在线测评设备，请先启动 run-eval")
        weights = OrderedDict()
        for e, key, source, p, pinned in plan:
            if e != eng:
                continue
            candidates = [source.runner] if pinned else online
            if any(r not in online for r in candidates):
                raise HTTPException(400, detail=f"RUN-{source.id} 绑定的原执行设备不在线，无法访问原目标设备")
            eligible[key] = candidates
            weights[key] = weights.get(key, 0) + 1
        assigned.update(assign_groups_balanced(list(weights.items()), online))
        for key in weights:
            if len(eligible[key]) == 1:
                assigned[key] = eligible[key][0]
    task = EvalTask(project_id=body.project_id, name=f"Multica 复测 {push_time():%Y-%m-%d %H:%M:%S}",
        description=f"来源反馈 RUN：{', '.join(map(str, selected_ids))}。本批使用下发快照；后续同基准复测请从 Multica 复测入口发起。",
        query_ids=json.dumps(sorted(queries)), target_engines=json.dumps(list(dict.fromkeys(x[0] for x in plan))),
        status=EvalTaskStatus.running, last_batch_id=batch, created_by=user_id, auto_pipeline=False)
    db.add(task)
    db.flush()
    created = []
    for eng, key, source, p, pinned in plan:
        run = EvalRun(project_id=body.project_id, eval_query_id=source.eval_query_id if source.eval_query_id in queries else None,
            eval_task_id=task.id, batch_id=batch, runner=assigned[key], eligible_runners=json.dumps(eligible[key]),
            target_engine=eng, target_device=pinned, device_kind=EvalDeviceKind.desktop, status=EvalRunStatus.pending,
            payload=json.dumps(p, ensure_ascii=False), multica_retest_source_id=source.id, enqueued_by=user_id)
        db.add(run)
        db.flush()
        created.append(run)
    # The manifest must also use frozen inputs, not the current (possibly edited/deleted) query library.
    dataset = [payload_of(r) for r in created]
    digest = hashlib.sha256(json.dumps(dataset, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    manifest = {'schema_version': 1, 'rules_version': 'context-v2', 'trial_count': max(p.get('trial_count') or 1 for p in dataset),
        'dataset_hash': digest, 'dataset': dataset, 'planned_runs': [
            {'run_id': r.id, 'target_engine': r.target_engine, 'runner': r.runner,
             'target_device': r.target_device, 'payload': payload_of(r)} for r in created]}
    db.add(EvalExperiment(project_id=body.project_id, batch_id=batch, dataset_hash=digest,
                          manifest=json.dumps(manifest, ensure_ascii=False)))
    db.commit()
    return {'task_id': task.id, 'batch_id': batch, 'run_ids': [r.id for r in created],
            'source_count': len(selected_ids), 'context_count': len(originals) - len(selected_ids)}
