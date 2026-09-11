"""自愈定位回写选择器:未注册 key 新建、已注册 key 补候选(幂等)。DB 单源,复用 selector_key 多候选。"""
import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import SelectorKey
from app.services.selector_ranking import valid_candidates, order_candidates

_MAX_CANDIDATES = 6
_HEAL_DESC = "[自愈] 执行时自动定位"


def _cands(raw: str | None) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _item_candidates(it: dict) -> list:
    """取该 heal item 的候选:优先 candidates(多候选:testid/xpath/css…,已校验有效+排序);
    回退单 selector(旧 runner,当 css)。返回规范化 [{by,value}](保序、testid>xpath>css>脆弱)。"""
    raw = it.get("candidates")
    if isinstance(raw, list) and raw:
        cands = valid_candidates([c for c in raw if isinstance(c, dict)])
    else:
        sel = str(it.get("selector") or "").strip()
        cands = [{"by": "css", "value": sel}] if sel else []
    return order_candidates([{"by": c["by"], "value": c["value"]} for c in cands])


def apply_heal_items(db: Session, project_id: int, items, updated_by=None, sub_product: str = "") -> dict:
    """回写一批自愈定位。items=[{key, candidates?:[{by,value}], selector?, page?, desc?}]。

    候选优先取 candidates(多候选:testid 优先 + css 兜底 + 需要时 xpath 消歧),兼容旧 runner 的单 selector(css)。
    key 未注册 → 新建(候选=本次全部);已注册 → 把新候选并入现有(去重、testid>xpath>css>脆弱 排序、限 6)。
    空 key / 无有效候选 → 跳过。返回 {created, patched, skipped}。
    """
    created = patched = skipped = 0
    for it in items or []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()
        new_cands = _item_candidates(it)
        if not key or not new_cands:
            continue
        page = str(it.get("page") or "").strip()[:64]
        # runner 传四段式 desc(如 "[任务]-[任务搜索]-...")→ 用它;不带 → 回退旧自愈串(兼容)
        desc = str(it.get("desc") or "").strip() or _HEAL_DESC
        row = (db.query(SelectorKey)
               .filter(SelectorKey.project_id == project_id,
                       SelectorKey.sub_product == sub_product, SelectorKey.key == key).first())
        if not row:
            db.add(SelectorKey(project_id=project_id, sub_product=sub_product, key=key,
                               frame="auto", page=page, desc=desc,
                               candidates=json.dumps(new_cands[:_MAX_CANDIDATES], ensure_ascii=False),
                               updated_by=updated_by, updated_at=datetime.utcnow()))
            db.flush()   # C1:立即 flush,让本批后续同 key 的 query 能查到它(session autoflush=False),
                         # 避免二次 db.add 同 key 在 commit 时撞 uq_selkey_scope_key → 整批回滚。
            created += 1
            continue
        existing = _cands(row.candidates)
        # 新候选里去掉已存在的(by+value 全等);全都已存在 → skip
        def _dup(c):
            return any(isinstance(e, dict) and e.get("by") == c["by"] and e.get("value") == c["value"] for e in existing)
        fresh = [c for c in new_cands if not _dup(c)]
        if not fresh:
            skipped += 1
            continue
        merged = order_candidates(fresh + [c for c in existing if isinstance(c, dict)])[:_MAX_CANDIDATES]
        row.candidates = json.dumps(merged, ensure_ascii=False)
        if page and not (row.page or "").strip():
            row.page = page
        # 补候选时:传了 desc 且原 desc 为空/旧自愈串 → 更新为新四段式;人工 desc 不覆盖
        item_desc = str(it.get("desc") or "").strip()
        if item_desc and (not row.desc or row.desc == _HEAL_DESC or row.desc.startswith("[自愈]")):
            row.desc = item_desc
        row.updated_at = datetime.utcnow()
        patched += 1
    db.commit()
    return {"created": created, "patched": patched, "skipped": skipped}
