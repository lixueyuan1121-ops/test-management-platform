"""编辑已生成 script 的校验分流(validate_script_for_edit)自测。
运行: cd backend && python -m scripts.test_edit_script

覆盖:按 kind 分流——gui/e2e 走 _validate_script(需注册表可用 key 集),api 走
_validate_api_script;非结构化 kind(manual/cli)拒绝;非法 script 返回错误原因。
口径与生成侧一致(复用同一批校验器 + usable_key_set)。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import Project, SelectorKey
from app.services.claude_runner import validate_script_for_edit

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
_s.add(SelectorKey(project_id=1, sub_product="", key="navTasks", frame="auto",
                   candidates='[{"by": "testid", "value": "nav-tasks"}]'))
_s.add(SelectorKey(project_id=1, sub_product="", key="taskList", frame="auto",
                   candidates='[{"by": "css", "value": ".task-list"}]'))
_s.commit()


def test_gui_valid_script_passes():
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "navTasks"}, "desc": "进入任务页"},
        {"action": "assert_visible", "target": {"key": "taskList"}, "desc": "断言列表可见"},
    ]
    norm, err = validate_script_for_edit("gui", script, project_id=1, db=_s)
    assert err is None, f"合法 gui script 不应报错: {err}"
    assert len(norm) == 3


def test_gui_unregistered_key_rejected():
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "ghostKey"}, "desc": "点不存在的 key"},
        {"action": "assert_visible", "target": {"key": "taskList"}, "desc": "断言"},
    ]
    _norm, err = validate_script_for_edit("gui", script, project_id=1, db=_s)
    assert err is not None and "ghostKey" in err, f"未注册 key 应被拒: {err}"


def test_gui_no_assert_rejected():
    script = [
        {"action": "connect", "desc": "连接"},
        {"action": "click", "target": {"key": "navTasks"}, "desc": "点导航"},
    ]
    _norm, err = validate_script_for_edit("gui", script, project_id=1, db=_s)
    assert err is not None and "断言" in err, f"无断言应被拒: {err}"


def test_api_valid_script_passes():
    script = [
        {"name": "查询项目", "request": {"method": "GET", "path": "/api/projects"},
         "asserts": [{"type": "jsonpath", "path": "code", "op": "eq", "value": 0}]},
    ]
    norm, err = validate_script_for_edit("api", script, project_id=1, db=_s)
    assert err is None, f"合法 api script 不应报错: {err}"
    assert len(norm) == 1


def test_api_bad_assert_rejected():
    script = [{"name": "无断言", "request": {"method": "GET", "path": "/api/x"}, "asserts": []}]
    _norm, err = validate_script_for_edit("api", script, project_id=1, db=_s)
    assert err is not None, "api 空断言应被拒"


def test_non_structured_kind_rejected():
    script = [{"action": "connect"}]
    for kind in ("manual", "cli"):
        _norm, err = validate_script_for_edit(kind, script, project_id=1, db=_s)
        assert err is not None, f"{kind} 不支持结构化 script,应报错"


def main():
    test_gui_valid_script_passes()
    test_gui_unregistered_key_rejected()
    test_gui_no_assert_rejected()
    test_api_valid_script_passes()
    test_api_bad_assert_rejected()
    test_non_structured_kind_rejected()
    print("OK test_edit_script")


if __name__ == "__main__":
    main()
