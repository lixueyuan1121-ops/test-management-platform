"""把全库 navHome 的候选升级为「侧栏导航类名精确定位」,修复自愈点『首页』点不上。

背景:navHome 旧候选仅 by:text"首页"(子串匹配),resolveKey 取 .first() 易锁到隐藏/错位
的"首页"→ 点不上或点错。内置 selectors.json 已改为精确候选,但 pickCandidates 是
「DB 候选有效即不看内置」,故已导入项目需在 DB 侧同步 navHome 候选(本脚本干这个)。

默认 dry-run 只打印;--apply 才写库。幂等:已含 .sidebar-nav__item css 候选的跳过。
不新建 navHome(缺失说明该项目没导过内置注册表,交由 import-legacy);只改 candidates,
不动 frame/desc/page 等其它字段。

运行: cd backend && .venv/bin/python scripts/update_navhome_candidates.py [--apply]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import SelectorKey               # noqa: E402
from sqlalchemy import inspect                   # noqa: E402

# 目标候选(与内置 selectors.json 的 navHome 保持一致):精确 css 优先, text 兜底。
NAV_HOME_CANDIDATES = [
    {"by": "css", "value": '.sidebar-nav__item:has-text("首页")'},
    {"by": "css", "value": '.sidebar-nav__text:text-is("首页")'},
    {"by": "text", "value": "首页"},
]
_TARGET_JSON = json.dumps(NAV_HOME_CANDIDATES, ensure_ascii=False)
# 幂等判据:已含带 sidebar-nav 的 css 候选即视作已升级,跳过(不覆盖可能的人工微调)。
_DONE_MARK = "sidebar-nav__"


def _cands(raw) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _already_upgraded(cands: list) -> bool:
    return any(
        isinstance(c, dict) and c.get("by") == "css" and _DONE_MARK in str(c.get("value") or "")
        for c in cands
    )


def main(apply: bool = False) -> int:
    if "selector_key" not in inspect(engine).get_table_names():
        print("selector_key 表不存在(库未初始化或连错库);无存量可扫,跳过。")
        return 0
    db = SessionLocal()
    updated = skipped = 0
    try:
        rows = db.query(SelectorKey).filter(SelectorKey.key == "navHome").all()
        if not rows:
            print("全库无 navHome key(项目未导入内置注册表?先跑 import-legacy)。")
            return 0
        for r in rows:
            scope = r.sub_product or "(共享)"
            if _already_upgraded(_cands(r.candidates)):
                skipped += 1
                continue
            old = json.dumps(_cands(r.candidates), ensure_ascii=False)
            print(f"[update] id={r.id} proj={r.project_id} scope={scope}")
            print(f"         旧候选: {old}")
            print(f"         新候选: {_TARGET_JSON}")
            if apply:
                r.candidates = _TARGET_JSON
            updated += 1
        if apply:
            db.commit()
        action = "已写库" if apply else "dry-run(未写库,加 --apply 生效)"
        print(f"\n汇总: 待升级={updated} 已升级跳过={skipped} 总 navHome={len(rows)} [{action}]")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
