"""模块入口反查:从 page/script 得到 runner 要用的确定性导航快照。DB 单源。"""
import json

from sqlalchemy.orm import Session

from app.models import ModuleEntry
from app.services.selectors import shared_key_page_map


def _nav_keys(raw: str | None) -> list[str]:
    try:
        v = json.loads(raw or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def module_nav_for_page(db: Session, project_id: int, page: str, sub_product: str = "") -> dict | None:
    """取某模块(page)的导航快照 {nav_keys, ready_key};无该模块入口或 page 空 → None。"""
    if not page:
        return None
    row = (db.query(ModuleEntry)
           .filter(ModuleEntry.project_id == project_id,
                   ModuleEntry.sub_product == sub_product,
                   ModuleEntry.page == page).first())
    if not row:
        return None
    return {"nav_keys": _nav_keys(row.nav_keys), "ready_key": row.ready_key or ""}


def module_of_script(db: Session, project_id: int, script) -> str | None:
    """从 script 用到的 target.key 反查所属模块(第一个非空 page)。无则 None。

    与 claude_runner._pages_for_script 同源(读同一 shared_key_page_map),取"第一个"作用例主模块。
    """
    if not isinstance(script, list) or not script:
        return None
    kp = shared_key_page_map(db, project_id)
    for st in script:
        if not isinstance(st, dict):
            continue
        tgt = st.get("target") or {}
        k = tgt.get("key") if isinstance(tgt, dict) else None
        p = (kp.get(k) or "").strip() if k else ""
        if p:
            return p
    return None
