"""L4:usable_key_set(只返回「候选有效」的 key)+ 生成侧校验切此口径 自测。
运行: cd backend && python -m scripts.test_usable_key_set

覆盖:
  A. usable_key_set 服务层(内存库):候选有效的 key 出现;候选坏([{}])/空([])的 key 不出现;
     只看项目共享(sub_product='')。与 shared_key_set(仅 key 名)对比,证明口径已收窄。
  B. 生成侧 _registered_keys → usable_key_set 口径:注册但候选坏的 key 在 parse_testcases 里
     被当「选择器待补」降级(而非当可执行 script 放行);候选补有效后确定性回填通过。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base
from app.models import Project, SelectorKey
from app.services.selectors import usable_key_set, shared_key_set

_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(_engine)
_Session = sessionmaker(bind=_engine)
_s = _Session()
_s.add(Project(id=1, name="P", code="P1", status="active"))
# good:候选有效 → usable。bad:候选坏([{}]) → 注册但不可用。empty:空候选 → 注册但不可用。
_s.add(SelectorKey(project_id=1, sub_product="", key="goodKey", frame="auto",
                   candidates='[{"by": "testid", "value": "nav-home"}]'))
_s.add(SelectorKey(project_id=1, sub_product="", key="badKey", frame="auto",
                   candidates='[{}]'))
_s.add(SelectorKey(project_id=1, sub_product="", key="emptyKey", frame="auto",
                   candidates='[]'))
# 另一项目的同名有效 key,验证不串项目。
_s.add(SelectorKey(project_id=2, sub_product="", key="otherProjKey", frame="auto",
                   candidates='[{"by": "css", "value": "#x"}]'))
_s.commit()


def test_usable_key_set_filters_bad_and_empty():
    usable = usable_key_set(_s, 1)
    assert usable == {"goodKey"}, f"只 goodKey 可用,实际 {usable}"
    # shared_key_set(旧口径,仅 key 名)三者都在 → 证明 usable_key_set 确实收窄了口径
    shared = shared_key_set(_s, 1)
    assert {"goodKey", "badKey", "emptyKey"} <= shared, f"shared_key_set 应含全部注册 key,实际 {shared}"
    assert "badKey" not in usable and "emptyKey" not in usable
    # 不串项目
    assert "otherProjKey" not in usable


# ---- 生成侧:_registered_keys 切 usable 口径后的端到端行为(monkeypatch 免真实 DB) ----
import app.services.claude_runner as cr


def _case_with_key(key: str):
    return json.dumps([{
        "title": "点某按钮", "kind": "gui", "category": "功能", "priority": "P1",
        "steps": "进入→点", "expected": "有反应",
        "script": [
            {"action": "connect", "desc": "连"},
            {"action": "click", "target": {"key": key}, "desc": "点按钮(可见文案『按钮』)"},
            {"action": "assert_visible", "target": {"key": key}, "desc": "断言可见"},
        ],
    }], ensure_ascii=False)


def test_bad_candidate_key_downgraded_as_selector_fix():
    """注册但候选坏的 key:_registered_keys(=usable 口径)里没有它 → 被当『选择器待补』降级。"""
    # usable 口径:badKey 候选坏 → 不在可用集
    cr._registered_keys = lambda pid: {"goodKey"}
    cr._key_page_map = lambda pid: {}
    cases = cr.parse_testcases(_case_with_key("badKey"), project_id=1)
    assert len(cases) == 1
    c = cases[0]
    assert c["kind"] == "manual", "候选坏的 key 不应当可执行 script 放行"
    assert c["kind_reason"].startswith("[选择器待补]"), "应标记选择器待补"
    assert "badKey" in c["kind_reason"], "应点名候选坏的 key"


def test_good_candidate_key_passes():
    """候选有效的 key:在 usable 集内 → 用例正常保留为可执行 gui。"""
    cr._registered_keys = lambda pid: {"goodKey"}
    cr._key_page_map = lambda pid: {}
    cases = cr.parse_testcases(_case_with_key("goodKey"), project_id=1)
    assert cases[0]["kind"] == "gui", f"候选有效应放行为 gui,实际 {cases[0]['kind']}"
    assert cases[0]["script"] is not None


def main():
    test_usable_key_set_filters_bad_and_empty()
    test_bad_candidate_key_downgraded_as_selector_fix()
    test_good_candidate_key_passes()
    print("OK test_usable_key_set")


if __name__ == "__main__":
    main()
