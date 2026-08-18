"""build_testcase_prompt 含 api script 规范段自测(project_id=None 免 DB)。
运行: cd backend && python -m scripts.test_build_testcase_prompt_api

重点防 f-string 花括号转义 bug:{{pid}} 模板与 {字段} 示例须原样出现在 prompt。
"""
from app.services.claude_runner import build_testcase_prompt


def main():
    p = build_testcase_prompt("测试用户登录与项目创建接口", project_id=None)

    # api 规范段存在
    assert "kind=api" in p, "缺 api script 规范段"
    assert "请求-断言-提取" in p
    # 断言 op 全集
    assert "eq/neq/exists/contains/gt/lt/regex/type" in p, "缺 op 全集说明"
    # 花括号转义未吞掉模板占位与 JSON 示例
    assert "{{pid}}" in p, "花括号 bug:{{pid}} 未原样出现"
    assert '"op":"eq"' in p, "花括号 bug:JSON 示例被 f-string 破坏"
    assert '{code,msg,data}' in p, "信封示例应原样出现"
    # 写操作硬约束
    assert "cleanup" in p and "写操作" in p
    # 无契约提示(project_id=None)
    assert "无 api 契约" in p and "manual" in p
    # item2 script 说明已并入 api
    assert "api 给请求-断言-提取数组" in p
    # 回归:gui/e2e 段仍在
    assert "connect" in p and "assert_visible" in p
    # 需求注入
    assert "测试用户登录与项目创建接口" in p
    # 尾部条目重编号未错乱(应有第 11 条边界示例)
    assert "11." in p, "尾部条目重编号缺失"

    print("OK test_build_testcase_prompt_api")


if __name__ == "__main__":
    main()
