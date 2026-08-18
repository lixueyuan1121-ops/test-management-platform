"""单条重生 api 分支自测(project_id=None 免 DB;不实际调 AI)。
运行: cd backend && python -m scripts.test_gen_script_api
"""
from app.services.claude_runner import build_script_prompt
from app.services.generators import deepseek_runner
from app.services import claude_runner


def main():
    # build_script_prompt(api) 含请求-断言-提取规范 + 模板占位原样
    sp = build_script_prompt("api", "创建项目并校验", "1.登录 2.创建项目", "创建成功 code=0", project_id=None)
    assert "请求-断言-提取" in sp, sp[:300]
    assert "{{pid}}" in sp, "花括号 bug:api 规范段模板未原样出现"
    assert '"op":"eq"' in sp, "JSON 示例被破坏"
    assert "创建项目并校验" in sp and "创建成功 code=0" in sp, "用例信息未注入"

    # gui 分支回归:仍产出界面步骤规范
    gp = build_script_prompt("gui", "看到导航", "打开首页", "导航可见", project_id=None)
    assert "connect" in gp and "assert_visible" in gp

    # 两引擎 generate_script("api",...) 不再被 kind 守卫挡(返回的错误不应是"仅 gui/e2e")。
    # 环境无 claude/deepseek → 返回"未启用/未配置",但绝不能是 kind 守卫报错。
    for mod, name in ((claude_runner, "claude"), (deepseek_runner, "deepseek")):
        _, err = mod.generate_script("api", "t", "s", "e", project_id=None)
        assert err is None or "仅 gui/e2e" not in err, f"{name} 仍被 kind 守卫挡下: {err}"

    print("OK test_gen_script_api")


if __name__ == "__main__":
    main()
