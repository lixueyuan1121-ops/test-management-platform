"""ModuleEntry 模型建表 + 唯一约束自测。
运行: cd backend && python -m scripts.test_module_entry_model
"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.session import Base
from app.models import ModuleEntry

def main():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng)
    s = S()
    s.add(ModuleEntry(project_id=2, sub_product="", page="自动化",
                      nav_keys=json.dumps(["navAutomation"], ensure_ascii=False), ready_key="automationPageTitle"))
    s.commit()
    row = s.query(ModuleEntry).filter_by(project_id=2, page="自动化").first()
    assert row is not None and json.loads(row.nav_keys) == ["navAutomation"], row
    assert row.ready_key == "automationPageTitle"
    s.add(ModuleEntry(project_id=2, sub_product="", page="自动化", nav_keys="[]", ready_key="x"))
    try:
        s.commit(); raise AssertionError("唯一约束未生效")
    except Exception:
        s.rollback()
    print("OK test_module_entry_model")

if __name__ == "__main__":
    main()
