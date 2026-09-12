"""Batch manifests and task-balanced repeated-execution statistics."""
import hashlib
import json
from collections import defaultdict
from types import SimpleNamespace

from app.models.ai_eval import EvalExperiment, EvalRun
from app.services.eval_snapshot import payload_of
from app.services.eval_metrics import outcome_metrics


def freeze(db, project_id, batch_id, queries, run_ids, trial_count):
    from app.api.eval_queue import _payload_of
    dataset = []
    for query in sorted(queries, key=lambda q: q.id):
        p = _payload_of(query)
        p.pop('dialog_options', None)
        dataset.append(p)
    digest = hashlib.sha256(json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    runs = db.query(EvalRun).filter(EvalRun.id.in_(run_ids)).order_by(EvalRun.id).all()
    manifest = {'schema_version': 1, 'rules_version': 'context-v2', 'trial_count': trial_count,
        'dataset_hash': digest, 'dataset': dataset, 'planned_runs': [
            {'run_id': r.id, 'target_engine': r.target_engine, 'runner': r.runner,
             'target_device': r.target_device, 'payload': payload_of(r)} for r in runs]}
    db.add(EvalExperiment(project_id=project_id, batch_id=batch_id, dataset_hash=digest,
                          manifest=json.dumps(manifest, ensure_ascii=False)))


def samples_by_batches(db, batch_ids, project_id):
    from sqlalchemy.orm import load_only
    by_batch = {bid: [] for bid in batch_ids}
    manifests = {bid: None for bid in batch_ids}
    if not batch_ids:
        return by_batch, manifests
    rows = db.query(EvalRun).options(load_only(EvalRun.id, EvalRun.batch_id, EvalRun.project_id,
        EvalRun.eval_query_id, EvalRun.target_engine, EvalRun.payload, EvalRun.status, EvalRun.verdict,
        EvalRun.score, EvalRun.reason, EvalRun.verdict_reason, EvalRun.bean_cost, EvalRun.duration_ms)).filter(
        EvalRun.batch_id.in_(batch_ids), EvalRun.project_id == project_id).all()
    for row in rows:
        by_batch[row.batch_id].append(row)
    experiments = db.query(EvalExperiment).filter(EvalExperiment.batch_id.in_(batch_ids),
                                                 EvalExperiment.project_id == project_id).all()
    for experiment in experiments:
        manifest = json.loads(experiment.manifest)
        bid = experiment.batch_id
        manifests[bid] = manifest
        found = {r.id for r in by_batch[bid]}
        for planned in manifest['planned_runs']:
            if planned['run_id'] not in found:
                by_batch[bid].append(SimpleNamespace(id=planned['run_id'], batch_id=bid,
                    target_engine=planned['target_engine'], payload=json.dumps(planned['payload']),
                    eval_query_id=planned['payload'].get('eval_query_id'), status='missing',
                    verdict=None, score=None, reason='计划执行记录缺失', bean_cost=None, duration_ms=None))
    return by_batch, manifests


def samples_with_missing(db, batch_id, project_id):
    batches, manifests = samples_by_batches(db, [batch_id], project_id)
    return batches[batch_id], manifests[batch_id]


def trial_metrics(rows):
    """One sample is an entire conversation trial; average trials within each task first."""
    tasks = defaultdict(lambda: defaultdict(list))
    for run in rows:
        p = payload_of(run)
        case = p.get('source_conversation_group') or p.get('conversation_group') or f"q:{p.get('eval_query_id') or run.eval_query_id or run.id}"
        key = (run.target_engine or 'unknown', p.get('compare_group') or '', case)
        tasks[key][p.get('trial_index', 1)].append(run)
    result = []
    for (engine, variant, case), trials in tasks.items():
        attempts = []
        for index, turns in sorted(trials.items()):
            m = outcome_metrics(turns)
            verdict = 'fail' if m['failed'] else ('pass' if m['passed'] == len(turns) else 'unknown')
            attempts.append({'trial_index': index, 'run_ids': [r.id for r in turns],
                'verdict': verdict, 'score': m['avg_score'] if m['judged'] == len(turns) else None,
                'coverage_rate': m['coverage_rate']})
        n = len(attempts)
        passed = sum(a['verdict'] == 'pass' for a in attempts)
        judged = sum(a['verdict'] != 'unknown' for a in attempts)
        scores = [a['score'] for a in attempts if a['score'] is not None]
        result.append({'engine': engine, 'variant': variant, 'case': case, 'attempts': attempts,
            'trial_count': n, 'success_rate': round(passed / n * 100, 1),
            'coverage_rate': round(judged / n * 100, 1),
            'mean_score': round(sum(scores) / len(scores), 2) if scores else None,
            'score_min': min(scores) if scores else None, 'score_max': max(scores) if scores else None})
    groups = defaultdict(list)
    for item in result:
        groups[(item['engine'], item['variant'])].append(item)
    return {'tasks': result, 'by_engine_variant': [
        {'engine': eng, 'variant': var, 'task_count': len(items),
         'success_rate': round(sum(i['success_rate'] for i in items) / len(items), 1),
         'coverage_rate': round(sum(i['coverage_rate'] for i in items) / len(items), 1),
         'mean_score': round(sum(i['mean_score'] for i in items if i['mean_score'] is not None) /
                             sum(i['mean_score'] is not None for i in items), 2)
                       if any(i['mean_score'] is not None for i in items) else None}
        for (eng, var), items in sorted(groups.items())]}
