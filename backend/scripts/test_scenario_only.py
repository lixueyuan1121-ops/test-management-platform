"""「仅场景组合」+ no_script + 降条数 的 prompt 构造自测。
运行: cd backend && python -m scripts.test_scenario_only

覆盖:
- _SHARD_CASE_RANGE 已降到 3-8
- build_testcase_prompt(no_script=True):script 段说明「一律给 []」,不含 gui/api script schema 重段
- scenario 片 + no_script:只带场景规格意图,不带 _GUI_SCRIPT_SPEC
- 普通生成(no_script=False):仍带 script schema(不回归)
- scenario_only 排产:run_testcase_gen_job 只保留 scenario 片(通过 TESTCASE_SHARDS 过滤验证)
"""
from app.services import claude_runner as c


def test_case_range_lowered():
    assert "3-8" in c._SHARD_CASE_RANGE, f"每片条数应降到 3-8,实际:{c._SHARD_CASE_RANGE}"
    assert "25" not in c._SHARD_CASE_RANGE, "不应再有 25 条上限"
    print(f"✓ 每片条数已降:{c._SHARD_CASE_RANGE}")


def test_no_script_prompt():
    req = "用户登录:账号密码登录,错误提示,连续5次锁定,记住我30天,成功跳工作台"
    p_full = c.build_testcase_prompt(req, project_id=None, shard=None, no_script=False)
    p_nos = c.build_testcase_prompt(req, project_id=None, shard=None, no_script=True)
    # no_script 应显著更短(砍掉 gui/api script schema 大段)
    assert len(p_nos) < len(p_full), f"no_script prompt 应更短:{len(p_nos)} vs {len(p_full)}"
    assert "一律给 `[]`" in p_nos, "no_script 应声明 script 一律给 []"
    # 不含 gui script schema 的标志性内容
    assert "action 只能取" not in p_nos, "no_script 不该带 gui script schema"
    assert "action 只能取" in p_full, "普通模式应带 gui script schema(不回归)"
    print(f"✓ no_script prompt {len(p_nos)} 字 < 普通 {len(p_full)} 字,且无 script schema")


def test_scenario_shard_no_script():
    scenario = next(s for s in c.TESTCASE_SHARDS if s["id"] == "scenario")
    p = c.build_testcase_prompt("需求X", project_id=None, shard=scenario, no_script=True)
    assert "多场景组合" in p or scenario["name"] in p, "应聚焦场景组合维度"
    assert "一律给 `[]`" in p, "场景片 no_script 应声明 script 一律给 []"
    assert "action 只能取" not in p, "场景片 no_script 不该带 gui script schema"
    print("✓ scenario 片 + no_script:聚焦场景、不产 script、不带 schema")


def test_scenario_only_shard_planning():
    # 模拟 run_testcase_gen_job 的排产:scenario_only 只保留 scenario 片
    shards = [s for s in c.TESTCASE_SHARDS if s["id"] == "scenario"]
    assert len(shards) == 1 and shards[0]["id"] == "scenario", "scenario_only 应只排 1 个 scenario 片"
    assert shards[0]["kinds"] == "e2e", "场景片应为 e2e"
    print("✓ scenario_only 排产:只 1 个 scenario 片(e2e)")


def main():
    test_case_range_lowered()
    test_no_script_prompt()
    test_scenario_shard_no_script()
    test_scenario_only_shard_planning()
    print("\n✅ 仅场景组合 + no_script + 降条数 全部通过")


if __name__ == "__main__":
    main()
