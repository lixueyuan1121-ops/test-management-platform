"""把入库的语义选择器（selector_key + selector_scope）导出为 Markdown。

只读。复用 backend/app 的 DB 配置：本机默认 SQLite；服务器 .env 启用 DB_* 即导出
生产 MySQL（work_qa）。因开发机连不上生产库（账号绑部署机 IP），此脚本须在
【服务器】上跑。

用法（在 backend 目录）：
    .venv/bin/python scripts/export_selectors_md.py                 # 写到 selectors_export.md
    .venv/bin/python scripts/export_selectors_md.py -o out.md       # 指定输出文件
    .venv/bin/python scripts/export_selectors_md.py --project 1     # 只导某项目
    .venv/bin/python scripts/export_selectors_md.py --stdout        # 直接打印到终端

字段口径与 api/selectors.py::_key_out 一致（candidates 反序列化）；候选是否「脆弱」
按 services/selector_ranking.is_fragile 标注。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402


def _cands(raw):
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, ValueError, TypeError):
        return []


def _fmt_cand(c, is_fragile):
    """把一条候选格式化成 `by=value`（脆弱加 ⚠️）。"""
    from app.services.selector_ranking import is_valid_candidate
    by = c.get("by", "?")
    val = c.get("value", "")
    mark = " ⚠️脆弱" if is_fragile(c) else ""
    bad = "" if is_valid_candidate(c) else " ❌无效"
    return f"`{by}` = `{val}`{mark}{bad}"


def _render(rows, scopes, projects):
    from app.services.selector_ranking import is_fragile

    lines = []
    lines.append("# 选择器注册表导出")
    lines.append("")
    lines.append(f"- 数据源：`{settings.sqlalchemy_url.split('@')[-1] if '@' in settings.sqlalchemy_url else settings.sqlalchemy_url}`")
    lines.append(f"- key 总数：**{len(rows)}**")
    lines.append("")

    # 按 (project_id, sub_product) 分组
    grouped = {}
    for r in rows:
        grouped.setdefault(r.project_id, {}).setdefault(r.sub_product or "", []).append(r)

    scope_map = {(s.project_id, s.sub_product or ""): s.vm_iframe for s in scopes}

    for pid in sorted(grouped):
        pname = projects.get(pid, "")
        lines.append(f"## 项目 {pid}" + (f"（{pname}）" if pname else ""))
        lines.append("")
        for sub in sorted(grouped[pid], key=lambda x: (x != "", x)):
            title = "项目级共享" if sub == "" else f"子产品：{sub}"
            lines.append(f"### {title}")
            vm = scope_map.get((pid, sub), "")
            if vm:
                lines.append(f"- vmIframe：`{vm}`")
            lines.append("")
            lines.append("| key | frame | page | 描述 | 候选（按存库顺序） |")
            lines.append("|---|---|---|---|---|")
            for r in sorted(grouped[pid][sub], key=lambda x: x.key):
                cands = _cands(r.candidates)
                cand_str = "<br>".join(_fmt_cand(c, is_fragile) for c in cands) if cands else "_（空）_"
                desc = (r.desc or "").replace("|", "\\|").replace("\n", " ")
                page = r.page or ""
                lines.append(f"| `{r.key}` | {r.frame} | {page} | {desc} | {cand_str} |")
            lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="selectors_export.md")
    ap.add_argument("--project", type=int, default=None, help="只导指定 project_id")
    ap.add_argument("--stdout", action="store_true", help="打印到终端而非写文件")
    args = ap.parse_args()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.models import SelectorKey, SelectorScope, Project

    engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)
    with Session(engine) as db:
        q = db.query(SelectorKey)
        sq = db.query(SelectorScope)
        if args.project is not None:
            q = q.filter(SelectorKey.project_id == args.project)
            sq = sq.filter(SelectorScope.project_id == args.project)
        rows = q.all()
        scopes = sq.all()
        projects = {p.id: p.name for p in db.query(Project).all()}

    md = _render(rows, scopes, projects)

    if args.stdout:
        print(md)
    else:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"已导出 {len(rows)} 个 key 到 {args.out}")


if __name__ == "__main__":
    main()
