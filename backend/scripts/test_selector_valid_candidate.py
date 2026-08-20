"""「有效候选」口径 + schema 强校验自测（免 DB）。
运行: cd backend && .venv/bin/python -m scripts.test_selector_valid_candidate
"""
import pydantic

from app.services.selector_ranking import is_valid_candidate, valid_candidates, VALID_BYS
from app.schemas.selector import SelectorKeyIn, SelectorKeyPatch


def main():
    assert VALID_BYS == {"testid", "role", "label", "text", "placeholder", "css"}, VALID_BYS
    # is_valid_candidate
    assert is_valid_candidate({"by": "css", "value": "h1.x"}) is True
    assert is_valid_candidate({"by": "role", "value": "button", "name": "登录"}) is True
    assert is_valid_candidate({}) is False                              # 本次 case 的坏值 [{}]
    assert is_valid_candidate({"by": "css"}) is False                   # 缺 value
    assert is_valid_candidate({"value": "x"}) is False                  # 缺 by
    assert is_valid_candidate({"by": "bogus", "value": "x"}) is False   # 非法 by
    # valid_candidates 过滤保序 / 非 list → []
    assert valid_candidates([{"by": "css", "value": "a"}, {}, {"by": "text", "value": "b"}]) == \
        [{"by": "css", "value": "a"}, {"by": "text", "value": "b"}]
    assert valid_candidates("nope") == []
    # schema：空数组放行、合法放行
    SelectorKeyIn(project_id=1, key="k", candidates=[])
    SelectorKeyIn(project_id=1, key="k", candidates=[{"by": "css", "value": "h1"}])
    # schema：坏候选一律 422
    for bad in ([{}], [{"by": "css"}], [{"value": "x"}], [{"by": "bogus", "value": "x"}]):
        try:
            SelectorKeyIn(project_id=1, key="k", candidates=bad)
            assert False, f"应拒绝坏候选 {bad}"
        except pydantic.ValidationError:
            pass
    # patch：None（不改候选）放行，坏候选拒绝
    SelectorKeyPatch(candidates=None)
    try:
        SelectorKeyPatch(candidates=[{}])
        assert False, "patch 应拒绝坏候选"
    except pydantic.ValidationError:
        pass
    print("OK test_selector_valid_candidate")


if __name__ == "__main__":
    main()
