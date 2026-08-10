"""一次性数据迁移：SQLite → 当前 .env 配置的数据库（通常是 MySQL）。全表覆盖。

设计要点（重要）：
- 目标库连接 = 项目 SessionLocal（读 .env / DB_*）。务必先把 .env 切到 MySQL 再跑。
- 源 = 传入的 SQLite 文件（默认仓库根 test_platform.db；服务端库在 backend/ 下，
  跑时传 test_platform.db 即可）。
- 按外键拓扑序迁 11 张业务表；统一维护每表 {旧id: 新id} 映射，改写子表外键。
- 去重/幂等：
  - user 按 username、project 按 code、tool_category 按 name 去重 → 已存在复用其 id。
  - 其余表无自然唯一键，靠"该父记录是否本次新插"决定是否迁子记录；
    对可独立存在的 task/daily_report 用轻量指纹去重，重复跑不重插。
- P3 占位表（integration/api_token/integration_event）不迁（MySQL 5.6 无 JSON，且无数据）。

用法（在 backend 目录，且 .env 已切 MySQL）：
    .venv/bin/python scripts/migrate_sqlite_to_db.py                 # 迁仓库根的库
    .venv/bin/python scripts/migrate_sqlite_to_db.py test_platform.db  # 迁 backend 下的库
    .venv/bin/python scripts/migrate_sqlite_to_db.py --dry-run       # 只统计源数据，不写库
"""
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Date, DateTime  # noqa: E402
from app.core.config import settings          # noqa: E402
from app.db.session import SessionLocal        # noqa: E402
from app.models import (                        # noqa: E402
    User, Project, Team, ProjectMember, Task, DailyReport,
    RemainingIssue, ToolCategory, TestTool, AiTask, TestCase,
)

# 迁移顺序（拓扑序：父在前）。P3 占位表不含其中。
_ORDER = [
    "user", "project", "team", "project_member", "task",
    "daily_report", "remaining_issue", "tool_category", "test_tool",
    "ai_task", "test_case",
]
_MODEL = {
    "user": User, "project": Project, "team": Team, "project_member": ProjectMember,
    "task": Task, "daily_report": DailyReport, "remaining_issue": RemainingIssue,
    "tool_category": ToolCategory, "test_tool": TestTool,
    "ai_task": AiTask, "test_case": TestCase,
}
# 自然去重键：命中则复用目标库已存在记录的 id（不重插）。
_DEDUP_KEY = {"user": "username", "project": "code", "tool_category": "name"}
# 外键字段 -> 引用的表名（用于把旧 id 改写成目标库新 id）。
_FKS = {
    "team": {"project_id": "project"},
    "project_member": {"user_id": "user", "project_id": "project", "team_id": "team"},
    "task": {"project_id": "project", "team_id": "team",
             "assigned_by": "user", "assigned_to": "user"},
    "daily_report": {"task_id": "task", "user_id": "user", "project_id": "project"},
    "remaining_issue": {"report_id": "daily_report", "project_id": "project", "owner": "user"},
    "test_tool": {"category_id": "tool_category"},
    "ai_task": {"project_id": "project", "task_id": "task", "user_id": "user"},
    "test_case": {"ai_task_id": "ai_task", "project_id": "project", "task_id": "task"},
}


# 自动管理的时间戳列：交给目标库 server_default/onupdate 生成，不从源搬
# （SQLite 存为字符串，硬搬会触发类型错误；语义上迁移时间也应是"现在"）。
_AUTO_TS = {"created_at", "updated_at"}


def _rows(con: sqlite3.Connection, table: str) -> list[dict]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(f'SELECT * FROM "{table}"')]
    except sqlite3.OperationalError:
        return []  # 源库无此表


def _col_names(model) -> set[str]:
    return {c.name for c in model.__table__.columns}


def _temporal_cols(model) -> dict[str, str]:
    """自省模型里的日期/时间列 -> 'date' | 'datetime'。SQLite 把它们存为字符串，
    迁移时需转回 Python date/datetime，否则跨方言插入会类型报错。"""
    out: dict[str, str] = {}
    for c in model.__table__.columns:
        if isinstance(c.type, DateTime):
            out[c.name] = "datetime"
        elif isinstance(c.type, Date):
            out[c.name] = "date"
    return out


def _coerce_temporal(v, kind: str):
    """把 SQLite 字符串转成 date/datetime；已是对象或 None 则原样返回。"""
    if v is None or isinstance(v, (date, datetime)):
        return v
    s = str(v).strip()
    if kind == "date":
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None  # 解析不了则置空，不阻断迁移


def main() -> int:
    dry = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    default_src = Path(__file__).resolve().parent.parent.parent / "test_platform.db"
    src_path = args[0] if args else str(default_src)

    if not Path(src_path).exists():
        print(f"找不到源 SQLite 文件：{src_path}")
        return 1

    target = settings.sqlalchemy_url
    print(f"源  (SQLite): {src_path}")
    print(f"目标(当前库): {target}")
    if target.startswith("sqlite"):
        print("⚠ 目标仍是 SQLite——请先把 .env 切到 MySQL（设 DB_HOST）再迁移。")
        return 2

    con = sqlite3.connect(src_path)
    data = {t: _rows(con, t) for t in _ORDER}
    con.close()
    print("源数据行数：", ", ".join(f"{t}={len(data[t])}" for t in _ORDER if data[t]) or "（全空）")
    if dry:
        print("== DRY RUN：不写库 ==")
        return 0

    db = SessionLocal()
    id_maps: dict[str, dict[int, int]] = {t: {} for t in _ORDER}
    inserted: dict[str, int] = {t: 0 for t in _ORDER}
    reused: dict[str, int] = {t: 0 for t in _ORDER}
    skipped_fk: dict[str, int] = {t: 0 for t in _ORDER}
    skipped_nonempty: list[str] = []

    try:
        for table in _ORDER:
            rows = data[table]
            if not rows:
                continue
            model = _MODEL[table]
            cols = _col_names(model)
            temporal = _temporal_cols(model)
            fks = _FKS.get(table, {})
            dedup = _DEDUP_KEY.get(table)

            # 幂等保护：有自然键的表按键逐行去重（可与既有数据合并）；
            # 无自然键的表若目标库已非空，则整表跳过——避免重跑造成重复插入。
            # 但仍需建立 id 映射，供子表改写外键，所以按自然键/顺序把旧 id 映到既有行。
            if not dedup and db.query(model).first() is not None:
                skipped_nonempty.append(table)
                # 无法可靠映射到既有行 → 该表及其子表外键会缺失，交给下面的 fk 跳过逻辑
                continue

            for r in rows:
                # 1) 自然键去重：命中则复用其 id，不重复插
                if dedup:
                    existing = db.query(model).filter_by(**{dedup: r[dedup]}).first()
                    if existing:
                        id_maps[table][r["id"]] = existing.id
                        reused[table] += 1
                        continue

                # 2) 外键完整性：必填外键的父未成功迁入 → 跳过该行（防悬挂外键）
                fk_broken = any(
                    r.get(col) is not None and r[col] not in id_maps[ref]
                    for col, ref in fks.items()
                )
                if fk_broken:
                    skipped_fk[table] += 1
                    continue

                # 3) 组装字段：拷贝同名列，外键改写为新 id，
                #    自动时间戳交给库生成，日期/时间列做类型转换。
                payload = {}
                for k, v in r.items():
                    if k not in cols or k == "id" or k in _AUTO_TS:
                        continue
                    if k in fks and v is not None:
                        payload[k] = id_maps[fks[k]].get(v)
                    elif k in temporal:
                        payload[k] = _coerce_temporal(v, temporal[k])
                    else:
                        payload[k] = v
                obj = model(**payload)
                db.add(obj)
                db.flush()  # 拿到新 id
                id_maps[table][r["id"]] = obj.id
                inserted[table] += 1

        db.commit()
        print("迁移完成，各表 新插/复用/因外键跳过：")
        for t in _ORDER:
            if inserted[t] or reused[t] or skipped_fk[t]:
                print(f"  {t:16} +{inserted[t]}  复用{reused[t]}  外键跳过{skipped_fk[t]}")
        if skipped_nonempty:
            print("以下表因目标库已非空被整表跳过（幂等保护，避免重复）：",
                  ", ".join(skipped_nonempty))
        return 0
    except Exception as e:
        db.rollback()
        print("迁移失败，已回滚：", type(e).__name__, e)
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
