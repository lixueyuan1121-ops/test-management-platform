"""测试点生成分片(shard) 自测(project_id=None 免 DB)。
运行: cd backend && python -m scripts.test_testcase_shards

背景:单次调用吐 100 条用例 → 输出 token 串行 = 主导耗时(实测顶死 15min 硬超时)。
拆成 K 路正交分片并行跑,墙钟≈1/K;且每片 prompt 只带自己需要的规则段(gui 片不带
api spec、api 片不带 gui DSL),input 也减半。本测试锁住分片定义/规划/prompt 收窄。
"""
from app.services.claude_runner import (
    TESTCASE_SHARDS,
    build_testcase_prompt,
    plan_shards,
)


def test_shard_defs():
    """分片定义:id 唯一、字段齐、覆盖四类 gui 维度 + api。"""
    ids = [s["id"] for s in TESTCASE_SHARDS]
    assert len(ids) == len(set(ids)), f"分片 id 重复:{ids}"
    for want in ("flow", "boundary", "exception", "scenario", "api"):
        assert want in ids, f"缺分片「{want}」:{ids}"
    for s in TESTCASE_SHARDS:
        assert s.get("name"), f"分片 {s['id']} 缺 name"
        assert s.get("focus"), f"分片 {s['id']} 缺 focus(职责说明)"
        assert s.get("exclude"), f"分片 {s['id']} 缺 exclude(边界声明,防各片重复产出)"
    # api 片须标记依赖契约(无契约时不排产,避免产出下发即 fail 的废用例)
    api_shard = next(s for s in TESTCASE_SHARDS if s["id"] == "api")
    assert api_shard.get("need_contract") is True, "api 分片须标 need_contract"


def test_plan_shards_no_contract():
    """无契约(project_id=None):不排 api 片,其余照排。"""
    shards = plan_shards(None)
    ids = [s["id"] for s in shards]
    assert "api" not in ids, f"无契约不应排 api 分片:{ids}"
    assert len(ids) >= 3, f"分片数过少,并行度不足:{ids}"


def test_shard_prompt_is_scoped():
    """分片 prompt 只带本片需要的规则段 + 声明职责边界。"""
    flow = next(s for s in TESTCASE_SHARDS if s["id"] == "flow")
    p = build_testcase_prompt("测试首页对话功能", project_id=None, shard=flow)
    # gui 类分片:带界面 script DSL
    assert "connect" in p and "assert_visible" in p, "gui 分片应带界面 script DSL"
    # 不带 api 规范段(徒增 input token)
    assert "请求-断言-提取" not in p, "gui 分片不应带 api script 规范段"
    # 声明本片职责与边界
    assert flow["name"] in p, "分片 prompt 应写明本片名"
    assert "本次只负责" in p, "分片 prompt 应有职责边界声明"
    assert "其余维度由其它分片" in p, "分片 prompt 应声明其余维度不归本片(防重复)"

    api = next(s for s in TESTCASE_SHARDS if s["id"] == "api")
    pa = build_testcase_prompt("测试首页对话功能", project_id=None, shard=api)
    assert "请求-断言-提取" in pa, "api 分片应带 api script 规范段"
    assert "assert_visible" not in pa, "api 分片不应带 gui script DSL"
    assert "可用语义 key 清单" not in pa, "api 分片不应注入选择器 key 清单"


def test_shard_prompt_case_count_scaled():
    """分片条数指导按片给(不能沿用全量的 8-20/100 上限,否则 K 片会吐出 K 倍)。"""
    flow = next(s for s in TESTCASE_SHARDS if s["id"] == "flow")
    p = build_testcase_prompt("需求", project_id=None, shard=flow)
    assert "100 条" not in p, "分片 prompt 不应沿用全量 100 条上限"
    assert "本分片" in p, "分片 prompt 应给本片自己的条数区间"


def test_full_prompt_backward_compat():
    """shard=None 保持全量行为(单片回退路径 / 老调用方不受影响)。"""
    p = build_testcase_prompt("需求", project_id=None)
    assert "connect" in p and "assert_visible" in p, "全量 prompt 应含 gui DSL"
    assert "请求-断言-提取" in p, "全量 prompt 应含 api 规范段"


def main():
    test_shard_defs()
    test_plan_shards_no_contract()
    test_shard_prompt_is_scoped()
    test_shard_prompt_case_count_scaled()
    test_full_prompt_backward_compat()
    print("OK test_testcase_shards")


if __name__ == "__main__":
    main()
