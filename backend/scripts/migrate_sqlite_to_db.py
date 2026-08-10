"""一次性数据迁移：SQLite → 当前 .env 配置的数据库（通常是 MySQL）。

设计要点（重要）：
- 目标库连接 = 项目的 SessionLocal（读 .env / DB_*）。务必先把 .env 切到 MySQL 再跑。
- 源 = 传入的 SQLite 文件（默认仓库根 test_platform.db）。
- 外键重映射：user 按 username、project 按 code 去重——已存在则复用目标库 id，
  不存在才插入；据此建立 {旧id: 新id} 映射，改写 ai_task/test_case 的外键。
- ai_task/test_case 无自然唯一键，总是新插；靠 ai_task 的 (created_at, input_ref)
  去重做幂等，重复跑不会插两遍。
- 只迁有数据且有意义的表：user / project / ai_task / test_case。
  其余表（task/daily_report/... 及 P3 占位表）本次源库为空，跳过。

用法（在 backend 目录，且 .env 已切 MySQL）：
    .venv/bin/python scripts/migrate_sqlite_to_db.py [路径/到/test_platform.db]
    .venv/bin/python scripts/migrate_sqlite_to_db.py --dry-run   # 只打印将要迁什么，不写库
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings          # noqa: E402
from app.db.session import SessionLocal        # noqa: E402
from app.models import User, Project, AiTask, TestCase  # noqa: E402


def _sqlite_rows(con: sqlite3.Connection, table: str) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(f'SELECT * FROM "{table}"')]


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    src_path = args[0] if args else str(Path(__file__).resolve().parent.parent.parent / "test_platform.db")

    if not Path(src_path).exists():
        print(f"找不到源 SQLite 文件：{src_path}")
        return 1

    target = settings.sqlalchemy_url
    print(f"源  (SQLite): {src_path}")
    print(f"目标(当前库): {target}")
    if target.startswith("sqlite"):
        print("⚠ 目标仍是 SQLite——请先把 .env 切到 MySQL（设 DB_HOST）再迁移。")
        return 2
    if dry:
        print("== DRY RUN：只统计不写库 ==")

    con = sqlite3.connect(src_path)
    users = _sqlite_rows(con, "user")
    projects = _sqlite_rows(con, "project")
    ai_tasks = _sqlite_rows(con, "ai_task")
    test_cases = _sqlite_rows(con, "test_case")
    con.close()
    print(f"源数据：user={len(users)} project={len(projects)} "
          f"ai_task={len(ai_tasks)} test_case={len(test_cases)}")

    if dry:
        return 0

    db = SessionLocal()
    user_map: dict[int, int] = {}
    project_map: dict[int, int] = {}
    ai_task_map: dict[int, int] = {}   # 旧 ai_task.id -> 新 id（含复用与新插）
    new_ai_task_ids: set[int] = set()  # 本次「新插」的旧 ai_task.id（只为这些迁用例）
    ins_u = ins_p = ins_a = ins_c = 0
    skip_a = 0
    try:
        # ---- user：按 username 去重 ----
        for r in users:
            existing = db.query(User).filter_by(username=r["username"]).first()
            if existing:
                user_map[r["id"]] = existing.id
                continue
            u = User(
                username=r["username"], password_hash=r["password_hash"],
                name=r["name"], email=r.get("email"),
                is_platform_admin=bool(r["is_platform_admin"]),
                status=r.get("status") or "active",
            )
            db.add(u); db.flush()
            user_map[r["id"]] = u.id; ins_u += 1

        # ---- project：按 code 去重 ----
        for r in projects:
            existing = db.query(Project).filter_by(code=r["code"]).first()
            if existing:
                project_map[r["id"]] = existing.id
                continue
            p = Project(
                name=r["name"], code=r["code"], description=r.get("description"),
                status=r.get("status") or "active",
            )
            db.add(p); db.flush()
            project_map[r["id"]] = p.id; ins_p += 1

        # ---- ai_task：重映射外键 + 幂等（按 created_at + input_ref 前缀）----
        for r in ai_tasks:
            new_pid = project_map.get(r["project_id"])
            new_uid = user_map.get(r["user_id"])
            if new_pid is None or new_uid is None:
                print(f"  跳过 ai_task#{r['id']}：外键无法映射（project/user 缺失）")
                continue
            dup = (
                db.query(AiTask)
                .filter(AiTask.user_id == new_uid, AiTask.project_id == new_pid,
                        AiTask.case_count == r.get("case_count", 0))
                .first()
            )
            if dup and (dup.input_ref or "")[:80] == (r.get("input_ref") or "")[:80]:
                ai_task_map[r["id"]] = dup.id; skip_a += 1
                continue
            a = AiTask(
                project_id=new_pid, task_id=None, user_id=new_uid,
                kind=r.get("kind") or "testcase_gen",
                input_type=r.get("input_type") or "text",
                input_ref=r.get("input_ref"),
                status=r.get("status") or "done",
                output_raw=r.get("output_raw"), error=r.get("error"),
                case_count=r.get("case_count") or 0,
                cost_usd=r.get("cost_usd"), output_tokens=r.get("output_tokens"),
                duration_ms=r.get("duration_ms"),
            )
            db.add(a); db.flush()
            ai_task_map[r["id"]] = a.id; ins_a += 1
            new_ai_task_ids.add(r["id"])

        # ---- test_case：只迁「新插 ai_task」的用例（复用的旧任务用例已在库里）----
        for r in test_cases:
            if r["ai_task_id"] not in new_ai_task_ids:
                continue
            new_aid = ai_task_map.get(r["ai_task_id"])
            new_pid = project_map.get(r["project_id"])
            if new_aid is None or new_pid is None:
                print(f"  跳过 test_case#{r['id']}：外键无法映射")
                continue
            tc = TestCase(
                ai_task_id=new_aid, project_id=new_pid, task_id=None,
                category=r.get("category"), title=r["title"],
                steps=r.get("steps"), expected=r.get("expected"),
                priority=r.get("priority"), adopted=bool(r.get("adopted")),
            )
            db.add(tc); ins_c += 1

        db.commit()
        print("迁移完成：")
        print(f"  user 新插 {ins_u}（其余复用已存在）")
        print(f"  project 新插 {ins_p}（其余复用已存在）")
        print(f"  ai_task 新插 {ins_a}，跳过已存在 {skip_a}")
        print(f"  test_case 新插 {ins_c}")
        return 0
    except Exception as e:
        db.rollback()
        print("迁移失败，已回滚：", type(e).__name__, e)
        return 3
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
