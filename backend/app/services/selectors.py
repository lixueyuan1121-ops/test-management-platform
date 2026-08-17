"""选择器注册表服务:DB 是唯一事实来源。merge 规则=项目级共享(sub_product='') ∪
子产品专属,同名 key 子产品覆盖共享。生成侧/API/runner 都经此层读,口径一致。"""
import json
from sqlalchemy.orm import Session
from app.models import SelectorKey, SelectorScope


def _cands(raw: str) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def resolved_registry(db: Session, project_id: int, sub_product: str = "") -> dict:
    """合并后有效注册表(供 runner 消费)。子产品专属覆盖同名共享 key。"""
    rows = (
        db.query(SelectorKey)
        .filter(SelectorKey.project_id == project_id,
                SelectorKey.sub_product.in_(["", sub_product] if sub_product else [""]))
        .all()
    )
    # 先铺共享,再用子产品覆盖(按 sub_product 非空优先)
    reg: dict = {}
    ver = 0
    for r in sorted(rows, key=lambda x: x.sub_product != ""):  # '' 排前,专属排后覆盖
        reg[r.key] = {"frame": r.frame, "desc": r.desc, "candidates": _cands(r.candidates)}
        ver = max(ver, int(r.updated_at.timestamp()) if r.updated_at else 0)
    # vmIframe:子产品专属优先,回落共享
    scope = (
        db.query(SelectorScope)
        .filter(SelectorScope.project_id == project_id,
                SelectorScope.sub_product.in_(["", sub_product] if sub_product else [""]))
        .all()
    )
    vm = ""
    for sc in sorted(scope, key=lambda x: x.sub_product != ""):
        if sc.vm_iframe:
            vm = sc.vm_iframe
    return {"vmIframe": vm, "registry": reg, "version": str(ver)}


def shared_key_dicts(db: Session, project_id: int) -> list[dict]:
    rows = (db.query(SelectorKey)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return [{"key": r.key, "frame": r.frame, "desc": r.desc} for r in rows]


def shared_key_set(db: Session, project_id: int) -> set[str]:
    rows = (db.query(SelectorKey.key)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return {r[0] for r in rows}
