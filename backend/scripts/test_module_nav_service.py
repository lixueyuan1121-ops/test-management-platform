"""模块导航反查服务自测。
运行: cd backend && python -m scripts.test_module_nav_service
"""
import json
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.session import Base
from app.models import ModuleEntry, SelectorKey
from app.services.module_entry import module_nav_for_page, module_of_script

def main():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add(ModuleEntry(project_id=2, sub_product="", page="自动化",
                      nav_keys=json.dumps(["navAutomation"], ensure_ascii=False), ready_key="automationPageTitle"))
    s.add(SelectorKey(project_id=2, sub_product="", key="automationCreateBtn", page="自动化", candidates="[]"))
    s.add(SelectorKey(project_id=2, sub_product="", key="navHome", page="", candidates="[]"))
    s.commit()

    nav = module_nav_for_page(s, 2, "自动化")
    assert nav == {"nav_keys": ["navAutomation"], "ready_key": "automationPageTitle"}, nav
    assert module_nav_for_page(s, 2, "不存在模块") is None
    assert module_nav_for_page(s, 2, "") is None

    script = [{"action": "connect"}, {"action": "click", "target": {"key": "automationCreateBtn"}}]
    assert module_of_script(s, 2, script) == "自动化", module_of_script(s, 2, script)
    assert module_of_script(s, 2, [{"action": "click", "target": {"key": "navHome"}}]) is None
    print("OK test_module_nav_service")

if __name__ == "__main__":
    main()
