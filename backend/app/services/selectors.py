"""选择器注册表服务:DB 是唯一事实来源。merge 规则=项目级共享(sub_product='') ∪
子产品专属,同名 key 子产品覆盖共享。生成侧/API/runner 都经此层读,口径一致。"""
import hashlib
import json
import os
from sqlalchemy.orm import Session
from app.models import SelectorKey, SelectorScope
from app.services.selector_ranking import is_active_candidate, order_candidates


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
    for r in sorted(rows, key=lambda x: x.sub_product != ""):  # '' 排前,专属排后覆盖
        reg[r.key] = {"frame": r.frame, "desc": r.desc, "candidates": order_candidates([c for c in _cands(r.candidates) if is_active_candidate(c)])}
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
    content = {"project_id": project_id, "sub_product": sub_product, "vmIframe": vm, "registry": reg}
    version = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {**content, "version": version}


def scoped_key_rows(db: Session, project_id: int, sub_product: str = "") -> list:
    rows = db.query(SelectorKey).filter(SelectorKey.project_id == project_id,
        SelectorKey.sub_product.in_(["", sub_product] if sub_product else [""])).all()
    merged = {}
    for row in sorted(rows, key=lambda r: r.sub_product != ""):
        merged[row.key] = row
    return list(merged.values())


def shared_key_dicts(db: Session, project_id: int, pages: list[str] | None = None, sub_product: str = "") -> list[dict]:
    rows = scoped_key_rows(db, project_id, sub_product)
    usable = usable_key_set(db, project_id, sub_product)
    return [{"key": r.key, "frame": r.frame, "desc": r.desc, "page": r.page or ""} for r in rows
            if r.key in usable and (not pages or not r.page or set(r.page.split(",")) & set(pages))]


def shared_key_page_map(db: Session, project_id: int, sub_product: str = "") -> dict[str, str]:
    return {r.key: r.page or "" for r in scoped_key_rows(db, project_id, sub_product)}


def shared_key_set(db: Session, project_id: int) -> set[str]:
    rows = (db.query(SelectorKey.key)
            .filter(SelectorKey.project_id == project_id, SelectorKey.sub_product == "")
            .all())
    return {r[0] for r in rows}


def usable_key_set(db: Session, project_id: int, sub_product: str = "") -> set[str]:
    """项目共享 key 中「候选有效」(至少一个含 by+value 的候选)的 key 名集合(L4 生成侧校验口径)。

    与 shared_key_set(仅认 key 名注册)的区别:本函数只收候选结构可用的 key——候选坏成 [{}]、
    或空候选 [] 的 key 视作「不可用」被排除。生成侧校验(_validate_script/回填/parse)据此把
    「注册了但候选坏/缺」的 key 当『选择器待补』降级,而非当可执行 script 放行。
    「有效候选」口径单点定义在 selector_ranking.valid_candidates(schema/服务/runner 三处共用)。
    """
    return {key for key, entry in resolved_registry(db, project_id, sub_product)["registry"].items()
            if entry["candidates"]}


# 内置 selectors.json 路径(与 api/selectors.py::import_legacy 同一份;settings.SELECTORS_PATH 可覆盖)。
_BUILTIN_SELECTORS_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "tools", "qalab-runner", "gui-mcp", "selectors.json"))


def core_key_set() -> set[str]:
    """核心 key 清单(进入/首页/登录类)的集合(L3 巡检目标)——单一事实源。

    读内置 selectors.json 顶层 `coreKeys` 数组(runner 侧 core-keys.mjs 读同一份,故两端必然一致)。
    这类 key 一旦失效,进入段自导航、复位就绪门禁、掉登录检测全塌,故列为核心、重点巡检。
    读不到/无 coreKeys → 空集(巡检退化为按显式传入的 keys,不误报)。
    """
    from app.core.config import settings
    path = settings.SELECTORS_PATH or _BUILTIN_SELECTORS_PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ck = data.get("coreKeys")
        return set(ck) if isinstance(ck, list) else set()
    except (OSError, json.JSONDecodeError, ValueError):
        return set()
