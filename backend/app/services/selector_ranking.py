"""候选结构、身份与排序契约；镜像 frontend/src/utils/selector-ranking.js。
结构合法不等于现场唯一。name/exact 属于定位身份，不能在回填或去重时丢弃。
"""
from __future__ import annotations
import json
import re

VALID_BYS = {"testid", "xpath", "role", "label", "text", "placeholder", "css"}
FRAGILE_BYS = {"text", "role"}


def is_valid_candidate(cand: dict) -> bool:
    return (isinstance(cand, dict) and cand.get("by") in VALID_BYS
            and isinstance(cand.get("value"), str) and bool(cand["value"].strip())
            and ("name" not in cand or isinstance(cand["name"], str))
            and ("exact" not in cand or type(cand["exact"]) is bool))


def normalize_candidate(cand: dict) -> dict | None:
    if not is_valid_candidate(cand):
        return None
    return {k: cand[k] for k in ("by", "value", "name", "exact", "src", "status", "disabled") if k in cand}


def candidate_identity(cand: dict) -> str:
    # 历史探测用 CSS 写 data-testid，录制用 testid；两种精确写法是同一定位。
    if cand.get("by") == "css":
        match = re.fullmatch(r'\[data-testid=("(?:[^"\\]|\\.)*"|[\w-]+)\]', cand.get("value", ""))
        if match:
            value = match[1]
            try:
                cand = {**cand, "by": "testid", "value": json.loads(value) if value.startswith('"') else value}
            except ValueError:
                pass
    return json.dumps([cand.get("by"), cand.get("value"), cand.get("name") or None,
                       cand.get("exact", False)], ensure_ascii=False, separators=(",", ":"))


def is_active_candidate(cand: dict) -> bool:
    return (is_valid_candidate(cand) and cand.get("src") != "learned"
            and cand.get("status") not in ("pending", "rejected", "retired")
            and cand.get("disabled") is not True)


def valid_candidates(cands: list[dict]) -> list[dict]:
    return [c for c in cands if is_valid_candidate(c)] if isinstance(cands, list) else []


def is_fragile(cand: dict) -> bool:
    return (cand.get("by") == "role" and not (cand.get("name") and cand.get("exact"))) or (
        cand.get("by") == "text" and not cand.get("exact"))


def candidate_rank(cand: dict) -> int:
    by, value = cand.get("by"), cand.get("value", "")
    if by == "testid": return 0
    if by == "role" and cand.get("name") and cand.get("exact"): return 1
    if by == "label": return 2
    if by == "placeholder": return 3
    if by == "css" and (value.startswith("#") or value.startswith("[data-test")): return 4
    if by == "text" and cand.get("exact"): return 5
    if by == "role" and cand.get("name"): return 6
    if by == "xpath": return 7
    if by == "css": return 8
    return 9


def order_candidates(cands: list[dict]) -> list[dict]:
    return sorted(cands, key=candidate_rank)


def merge_candidates(*groups: list[dict], limit: int = 6) -> list[dict]:
    seen, merged = set(), []
    for group in groups:
        for c in valid_candidates(group):
            identity = candidate_identity(c)
            if identity not in seen:
                seen.add(identity)
                merged.append(normalize_candidate(c))
    return order_candidates(merged)[:limit]
