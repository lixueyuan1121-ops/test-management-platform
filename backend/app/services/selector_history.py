import hashlib
import json
from app.models.selector_revision import SelectorRevision


def snapshot(row):
    return {"platform": row.platform, "frame": row.frame, "page": row.page, "desc": row.desc,
            "candidates": json.loads(row.candidates or "[]")}


def revision(row):
    return hashlib.sha256(json.dumps(snapshot(row), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def remember(db, row, user_id):
    db.add(SelectorRevision(key_id=row.id, revision=revision(row),
                           snapshot=json.dumps(snapshot(row), ensure_ascii=False), changed_by=user_id))
