"""扫描全库 selector_key，找出候选结构坏（缺 by/value）的 key。
默认 dry-run 只打印报告；--apply 用内置 selectors.json 同名 key 回填可回填者。
运行: cd backend && .venv/bin/python scripts/fix_broken_selector_candidates.py [--apply]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import SelectorKey               # noqa: E402
from app.services.selector_ranking import valid_candidates  # noqa: E402
from sqlalchemy import inspect                   # noqa: E402

_BUILTIN = Path(__file__).resolve().parents[2] / "tools/qalab-runner/gui-mcp/selectors.json"


def classify_key(db_cands, builtin_cands) -> str:
    """该 key 的处置：ok(候选有效,跳过) / backfill(坏但内置可回填) / manual(坏且内置无)。"""
    if valid_candidates(db_cands):
        return "ok"
    if valid_candidates(builtin_cands):
        return "backfill"
    return "manual"


def _load_builtin() -> dict:
    try:
        return json.loads(_BUILTIN.read_text("utf-8")).get("registry", {})
    except (OSError, ValueError):
        return {}


def _cands(raw) -> list:
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def main(apply: bool = False) -> int:
    if "selector_key" not in inspect(engine).get_table_names():
        print("selector_key 表不存在(库未初始化或连错库);无存量可扫,跳过。")
        return 0
    builtin = _load_builtin()
    db = SessionLocal()
    ok = backfilled = manual = 0
    try:
        for r in db.query(SelectorKey).all():
            bc = (builtin.get(r.key) or {}).get("candidates", [])
            verdict = classify_key(_cands(r.candidates), bc)
            if verdict == "ok":
                ok += 1
            elif verdict == "backfill":
                good = valid_candidates(bc)
                print(f"[backfill] id={r.id} proj={r.project_id} key={r.key} <- 内置 {len(good)} 候选")
                if apply:
                    r.candidates = json.dumps(good, ensure_ascii=False)
                backfilled += 1
            else:
                print(f"[manual]   id={r.id} proj={r.project_id} key={r.key} 候选坏且内置无同名,需人工补")
                manual += 1
        if apply:
            db.commit()
        print(f"\n汇总: ok={ok} backfill={backfilled} manual={manual} apply={apply}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
