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


def shared_key_dicts(db: Session, project_id: int, pages: list[str] | None = None) -> list[dict]:
    """项目共享 key 清单(供 prompt 注入),返回 [{key, frame, desc, page}, ...]。

    pages 非空时按页面收窄:只留「所属页面 ∈ pages」或「未分类(page='')」的 key
    ——未分类通常是全局/通用 key(如连接、登录),始终带上避免误伤。pages 为空/None → 全部。
    """
    rows = (db.query(SelectorKey)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    if pages:
        want = set(pages)
        rows = [r for r in rows if not (r.page or "") or (r.page or "") in want]
    return [{"key": r.key, "frame": r.frame, "desc": r.desc, "page": r.page or ""} for r in rows]


def shared_key_page_map(db: Session, project_id: int) -> dict[str, str]:
    """项目共享 key → 所属页面(page,可为空串)的映射,供按 script 用到的 key 反查页面。"""
    rows = (db.query(SelectorKey.key, SelectorKey.page)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return {k: (p or "") for k, p in rows}


def shared_key_set(db: Session, project_id: int) -> set[str]:
    rows = (db.query(SelectorKey.key)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return {r[0] for r in rows}


def usable_key_set(db: Session, project_id: int) -> set[str]:
    """项目共享 key 中「候选有效」(至少一个含 by+value 的候选)的 key 名集合(L4 生成侧校验口径)。

    与 shared_key_set(仅认 key 名注册)的区别:本函数只收候选结构可用的 key——候选坏成 [{}]、
    或空候选 [] 的 key 视作「不可用」被排除。生成侧校验(_validate_script/回填/parse)据此把
    「注册了但候选坏/缺」的 key 当『选择器待补』降级,而非当可执行 script 放行。
    「有效候选」口径单点定义在 selector_ranking.valid_candidates(schema/服务/runner 三处共用)。
    """
    from app.services.selector_ranking import valid_candidates
    rows = (db.query(SelectorKey.key, SelectorKey.candidates)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return {k for k, raw in rows if valid_candidates(_cands(raw))}
