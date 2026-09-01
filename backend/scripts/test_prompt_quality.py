"""e2e 人工粒度 steps + 多场景组合设计 + api 恢复 的 prompt 自测。
运行: cd backend && python -m scripts.test_prompt_quality

覆盖三项生成质量要求:
1. e2e 用例 steps 要按人工测试粒度写、与 script 步骤同序一一对应(便于转脚本)。
2. 多场景组合:识别前置状态变量(已安装/未安装、空数据、无权限…),同一路径产多条分支用例。
3. api 用例恢复:项目已配契约时不再劝退到 gui/e2e,并给接口测试规范(正例/必填/边界/鉴权/幂等)。
"""
from app.services.claude_runner import TESTCASE_SHARDS, build_testcase_prompt


def test_e2e_steps_manual_granularity():
    """需求1:e2e 的 steps 要写成人工可直接照做的编号步骤,且与 script 同序对应。"""
    p = build_testcase_prompt("测试首页对话功能", project_id=None)
    assert "人工" in p, "steps 应要求按人工测试粒度书写"
    assert "一一对应" in p, "steps 应要求与 script 步骤一一对应(便于转脚本)"
    assert "同序" in p, "steps 与 script 应同序"
    # 每步的四要素:动作 + 操作对象 + 输入数据 + 即时可见反馈
    assert "可见反馈" in p, "steps 每步应写出该步的即时可见反馈"
    assert "编号" in p, "e2e steps 应编号分步"
    # 按 kind 分级(不能对 gui/e2e/api 一刀切)
    assert "按 kind 分级" in p, "steps 粒度要求应按 kind 分级"


def test_scenario_matrix_design():
    """需求3:多场景组合——前置状态变量分支。"""
    p = build_testcase_prompt("测试首页对话功能", project_id=None)
    assert "前置状态" in p, "应引导识别前置状态变量"
    # 用户给的业务例子(专家已安装/未安装)作为正例锚点
    assert "未安装" in p, "应给出「已安装/未安装」这类前置状态分支正例"
    assert "前置：" in p or "前置:" in p, "分支用例 steps 应以「前置：」写清该分支状态如何构造"
    # 分支用例的 expected 要写出该分支特有的中间过程
    assert "中间过程" in p, "分支用例应断言该分支特有的中间过程(如安装进度)"
    # scenario 分片承载这块
    sc = next(s for s in TESTCASE_SHARDS if s["id"] == "scenario")
    ps = build_testcase_prompt("测试首页对话功能", project_id=None, shard=sc)
    assert "前置状态" in ps and "未安装" in ps, "scenario 分片应带场景组合设计段"


def test_api_revival_and_spec():
    """需求2:api 恢复(有契约时)+ 接口测试规范。"""
    api = next(s for s in TESTCASE_SHARDS if s["id"] == "api")
    p = build_testcase_prompt("测试项目管理接口", project_id=None, shard=api)
    # 接口测试规范五要素
    for want in ("必填", "鉴权", "幂等", "边界"):
        assert want in p, f"api 设计规范缺「{want}」"
    assert "业务码" in p or "code" in p, "api 断言应要求断业务码而不只断 status"
    assert "cleanup" in p, "写操作应自带清理"


def test_no_contract_still_discourages_api():
    """无契约时维持劝退(api-executor 无 base_url 直接 fail,不产废用例)。"""
    p = build_testcase_prompt("测试项目管理接口", project_id=None)
    assert "优先改判 kind=gui/e2e" in p, "无契约应仍引导优先 gui/e2e"


def test_script_prompt_follows_steps():
    """单条重生 script 时要按人工 steps 同序翻译(需求1的另一半:转脚本快捷)。"""
    from app.services.claude_runner import build_script_prompt
    steps = "1. 在输入框输入\"周报\" → 显示该文本\n2. 点击发送 → 进入会话"
    for kind in ("e2e", "gui"):
        p = build_script_prompt(kind, "标题", steps, "预期", project_id=None)
        assert "同序" in p, f"{kind}:script 应要求与 steps 同序"
        assert "一一对应" in p, f"{kind}:script 应要求与 steps 一一对应"
        assert steps in p, f"{kind}:steps 原文应带进 prompt"
    pa = build_script_prompt("api", "标题", steps, "预期", project_id=None)
    assert "同序" in pa, "api:script 应要求与 steps 同序"


def main():
    test_e2e_steps_manual_granularity()
    test_scenario_matrix_design()
    test_api_revival_and_spec()
    test_no_contract_still_discourages_api()
    test_script_prompt_follows_steps()
    print("OK test_prompt_quality")


if __name__ == "__main__":
    main()
