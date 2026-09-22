"""Conservative, deterministic matching; fuzzy candidates always require confirmation."""
import hashlib
import json
import re
import unicodedata
from collections import Counter


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def script_identity(value):
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, list) or not value:
            return None
        return [{k: v for k, v in {**step, "target": step.get("target") or {},
                                 "args": step.get("args") or {}}.items() if k != "desc"}
                for step in value]
    except (ValueError, TypeError, AttributeError):
        return None


def identity(case):
    script = script_identity(case.script)
    if script is None:
        return None
    # Never drop arguments, selectors, assertion values, preconditions or expected result.
    return digest([case.exec_kind, text(case.precondition), text(case.expected), script])


def revision(case):
    return digest([case.id, case.title, case.steps, case.expected, case.precondition,
                   case.script, case.exec_kind, case.page, case.review_status,
                   case.is_regression, case.adopted])


def grams(value):
    value = re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value or "").lower())
    return {value[i:i+2] for i in range(len(value)-1)} or ({value} if value else set())


def similarity(a, b):
    a, b = grams(a), grams(b)
    return len(a & b) / max(1, len(a | b))


def related(a, b):
    title = similarity(a.title, b.title)
    expected = similarity(a.expected, b.expected)
    steps = similarity(a.steps, b.steps)
    if title >= .55 or (expected >= .65 and steps >= .45):
        return True
    # Shared concrete actions help detect paraphrased descriptions. Generic connect is ignored.
    left, right = script_identity(a.script), script_identity(b.script)
    if left and right:
        def operations(items):
            return Counter(digest(s) for s in items if s.get("target") and s.get("action") != "connect")
        x, y = operations(left), operations(right)
        overlap = sum((x & y).values()) / max(1, sum((x | y).values()))
        return overlap >= .65
    return False


def plans_for(body, scripts, rows):
    from types import SimpleNamespace
    incoming = [SimpleNamespace(**{**c.model_dump(), "script": s}) for c, s in zip(body.cases, scripts)]
    # Resolve intra-batch duplication before writing anything; do not silently discard evidence.
    for i, item in enumerate(incoming):
        for j in range(i):
            if identity(item) == identity(incoming[j]):
                raise ValueError(f"同批第 {j+1}、{i+1} 条为相同场景，请拆成独立验证批次追加结果")
    row_identities = {r.id: identity(r) for r in rows}
    plans = []
    for i, (item, original) in enumerate(zip(incoming, body.cases)):
        fingerprint = identity(item)
        exact = [r for r in rows if fingerprint == row_identities[r.id]]
        candidates = exact or [r for r in rows if related(item, r)]
        candidates.sort(key=lambda r: r.id)
        kind = "exact" if len(exact) == 1 else ("ambiguous" if candidates else "new")
        token = digest([body.project_id, body.sub_product, original.model_dump(mode="json", exclude={"resolution"}),
                        [(r.id, revision(r)) for r in candidates]])
        plan = {"index": i, "title": original.title, "match": kind, "confirmation_token": token,
                "case_id": exact[0].id if kind == "exact" else None,
                "candidates": [{"case_id": r.id, "title": r.title, "steps": r.steps, "expected": r.expected,
                                "precondition": r.precondition, "page": r.page, "exec_kind": r.exec_kind,
                                "revision": revision(r), "reason": "脚本与验收条件一致" if exact else "场景相似，需人工确认"}
                               for r in candidates], "ready": kind != "ambiguous"}
        choice = original.resolution
        if choice:
            if choice.token != token:
                plan.update(ready=False, conflict="候选或输入已变化，请重新确认")
            elif choice.action == "reuse" and choice.case_id in {r.id for r in candidates}:
                plan.update(ready=True, case_id=choice.case_id)
            elif choice.action == "create" and not exact:
                plan.update(ready=True, case_id=None)
            else:
                plan.update(ready=False, conflict="复用目标不在候选中，或相同场景不能强制新建")
        plans.append(plan)
    return plans
