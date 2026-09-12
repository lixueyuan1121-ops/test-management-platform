"""Versioned judgment evidence; EvalRun remains a cache for existing consumers."""
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func

from app.models.ai_eval import EvalJudgment, EvalJudgmentReview, EvalRun, EvalRunHistory

RESULT_FIELDS = ("verdict", "score", "verdict_dims", "verdict_reason", "judged_by", "is_abnormal")


def result_of(run):
    return {name: getattr(run, name, None) for name in RESULT_FIELDS}


def ensure_legacy(db, run):
    if run.judgment_id or not run.verdict:
        return
    row = EvalJudgment(eval_run_id=run.id, attempt=attempt_of(db, run), provider=run.judged_by,
        rules_version="legacy", result=json.dumps(result_of(run), ensure_ascii=False),
        completed_at=datetime.now(timezone.utc).replace(tzinfo=None))
    db.add(row)
    db.flush()
    run.judgment_id = row.id
    if run.review_mark or run.review_note:
        db.add(EvalJudgmentReview(judgment_id=row.id, mark=run.review_mark, note=run.review_note))


def attempt_of(db, run):
    return (db.query(func.max(EvalRunHistory.attempt)).filter_by(eval_run_id=run.id).scalar() or 0) + 1


def begin(db, run, provider):
    ensure_legacy(db, run)
    row = EvalJudgment(eval_run_id=run.id, attempt=attempt_of(db, run), provider=provider)
    db.add(row)
    db.flush()
    run.judgment_id = row.id
    run.review_mark = run.review_note = None
    for field in RESULT_FIELDS:
        setattr(run, field, False if field == "is_abnormal" else None)
    return row


def set_input(db, run, prompt, system_prompt, metadata):
    if not isinstance(run, EvalRun) or not run.judgment_id:
        return
    row = db.get(EvalJudgment, run.judgment_id)
    data = json.dumps({"prompt": prompt, "system_prompt": system_prompt, **metadata}, ensure_ascii=False, sort_keys=True)
    row.input_json = data
    row.input_hash = hashlib.sha256(data.encode()).hexdigest()


def complete(db, run, ballots=None):
    if isinstance(run, EvalRun) and run.judgment_id:
        row = db.get(EvalJudgment, run.judgment_id)
        if row and not row.completed_at:
            row.result = json.dumps(result_of(run), ensure_ascii=False)
            row.ballots = json.dumps(ballots or [], ensure_ascii=False)
            row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
