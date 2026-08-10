"""切 MySQL 前的体检脚本：连通性 + 表撞名检查。

用法（在 backend 目录）：
    .venv/bin/python scripts/check_db.py

读取 backend/.env 的 DB_* 配置（若已填 MySQL）；也可直接用环境变量覆盖。
只读，不建表、不改任何数据。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402

OURS = {
    "user", "project", "team", "project_member", "task", "daily_report",
    "remaining_issue", "integration", "api_token", "integration_event",
    "ai_task", "test_case",
}


def main() -> int:
    url = settings.sqlalchemy_url
    if url.startswith("sqlite"):
        print("当前配置仍是 SQLite（未设 DB_HOST）。请先在 .env 填 MySQL 的 DB_* 再跑本脚本。")
        print("effective URL:", url)
        return 1

    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT VERSION()")).scalar()
            db = conn.execute(text("SELECT DATABASE()")).scalar()
        existing = set(inspect(engine).get_table_names())
    except Exception as e:
        print("连接失败:", type(e).__name__, e)
        return 2

    print("连接成功  MySQL:", ver, " 库:", db)
    print("库里现有表数:", len(existing))
    print("现有表:", sorted(existing))
    clash = OURS & existing
    if clash:
        print("!!! 撞名的表（危险，先别建表）:", sorted(clash))
        return 3
    print("撞名检查: 无（安全，可以建表）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
